import { existsSync } from 'node:fs';
import { chmod, mkdir, mkdtemp, readdir, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { call, error, text, tools, type ChatRequest, type Script } from '@desk/fake-model';
import type { ToolImage } from '@desk/protocol';
import { AttachmentStore } from '../attachments/store';
import { openDb } from '../db/open';
import { EventStore } from '../events/store';
import { IMAGES_HEADER } from '../agent/transcript';
import { renderTranscript } from '../coordination/render';
import { deskToolsFor, threadToolsFor } from '../runtime/toolsets';
import { getAgent, getDeskAgent } from '../state/queries';
import { testServices, testToolContext } from '../testing/context';
import { createHarness, FAKE_MODEL, newRuntime, noSleep, type Harness } from '../testing/harness';
import { encodePng, imageHeaders } from '../testing/png';
import { prepareToolCall, runPreparedTool } from './registry';
import type { ToolContext } from './types';
import { MAX_IMAGE_BYTES, viewImageTool } from './vision';

let dir: string;
let h: Harness | undefined;
let closeDb: (() => void) | undefined;
afterEach(async () => {
  await h?.cleanup();
  h = undefined;
  closeDb?.();
  closeDb = undefined;
  if (dir) await rm(dir, { recursive: true, force: true });
});

async function setup(vision = true) {
  dir = await realpath(await mkdtemp(join(tmpdir(), 'desk-vision-')));
  const ws = join(dir, 'ws');
  const other = join(dir, 'source');
  await mkdir(ws);
  await mkdir(other);
  const attachments = new AttachmentStore(join(dir, 'attachments'));
  const { db, close } = openDb(':memory:');
  closeDb = close;
  const store = new EventStore(db);
  const ctx = testToolContext(ws, {
    readRoots: [ws, other],
    services: testServices({
      attachments,
      store,
      // The model that asked decides; 'blind-model' has vision off.
      agentModel: (_agent, model) => (model ? { id: model, vision: model !== 'blind-model' } : { id: vision ? 'seeing-model' : 'text-model', vision }),
    }),
  });
  return { ws, other, attachments, store, ctx };
}

async function view(ctx: ToolContext, args: unknown) {
  const prepared = prepareToolCall([viewImageTool], { id: 'c1', name: 'view_image', arguments: JSON.stringify(args) });
  return prepared.ok ? runPreparedTool(prepared.tool, prepared.input, ctx) : prepared.result;
}

describe('view_image', () => {
  it('stores each image content-addressed and describes it in one line', async () => {
    const { ws, other, attachments, ctx } = await setup();
    const png = encodePng(40, 30);
    await mkdir(join(ws, 'renders'));
    await writeFile(join(ws, 'renders', 'page-1.png'), png);
    await writeFile(join(other, 'photo.jpg'), imageHeaders.jpeg(1240, 1754));
    await writeFile(join(ws, 'anim.gif'), imageHeaders.gif(320, 200));
    await writeFile(join(ws, 'logo.webp'), imageHeaders.webp('VP8L', 64, 64));
    const r = await view(ctx, { paths: ['renders/page-1.png', join(other, 'photo.jpg'), 'anim.gif', join(ws, 'logo.webp')], purpose: 'check the layout' });
    expect(r.status).toBe('ok');
    expect(r.content.split('\n')).toEqual([
      `renders/page-1.png · 40×30 · PNG · ${png.length} B`,
      `${join(other, 'photo.jpg')} · 1240×1754 · JPEG · ${imageHeaders.jpeg(1240, 1754).length} B`,
      'anim.gif · 320×200 · GIF · 13 B',
      'logo.webp · 64×64 · WebP · 40 B',
    ]);
    const images = r.images as ToolImage[];
    expect(images.map((i) => [i.name, i.media_type, i.width, i.height, i.bytes])).toEqual([
      ['renders/page-1.png', 'image/png', 40, 30, png.length],
      [join(other, 'photo.jpg'), 'image/jpeg', 1240, 1754, imageHeaders.jpeg(1240, 1754).length],
      ['anim.gif', 'image/gif', 320, 200, 13],
      ['logo.webp', 'image/webp', 64, 64, 40],
    ]);
    expect((await attachments.read(images[0]!.sha256))?.data.equals(png)).toBe(true);
    expect((await readdir(attachments.dir)).sort()).toEqual(images.map((i) => `${i.sha256}.${{ 'image/png': 'png', 'image/jpeg': 'jpg', 'image/gif': 'gif', 'image/webp': 'webp' }[i.media_type]}`).sort());
  });

  it('reads only where read_file may, and takes 1 to 8 paths', async () => {
    const { ctx } = await setup();
    await writeFile(join(dir, 'secret.png'), encodePng(2, 2));
    expect(await view(ctx, { paths: [join(dir, 'secret.png')] })).toMatchObject({ status: 'denied', content: expect.stringContaining('outside the directories you may read') });
    expect(await view(ctx, { paths: [] })).toMatchObject({ status: 'error', content: expect.stringContaining('Invalid arguments') });
    expect(await view(ctx, { paths: Array.from({ length: 9 }, (_, i) => `${i}.png`) })).toMatchObject({ status: 'error' });
  });

  it('names the file skill that renders formats it does not take', async () => {
    const { ws, ctx } = await setup();
    const files: Array<[string, string | Buffer, RegExp]> = [
      ['diagram.svg', '<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg"></svg>', /SVG .*images skill \(img_view\.py\)/],
      ['drawing', '<svg xmlns="http://www.w3.org/2000/svg"></svg>', /SVG .*images skill \(img_view\.py\)/],
      ['IMG_0001.HEIC', Buffer.concat([Buffer.from([0, 0, 0, 24]), Buffer.from('ftypheic')]), /HEIC .*images skill \(img_view\.py\)/],
      ['scan.tiff', Buffer.from('II*\0rest'), /TIFF .*images skill/],
      ['report.pdf', '%PDF-1.7\n', /PDF .*pdf-toolkit skill \(pdf_render\.py\)/],
      ['unnamed', '%PDF-1.4\n', /PDF .*pdf-toolkit skill \(pdf_render\.py\)/],
      ['memo.docx', Buffer.from('PK\x03\x04'), /word-documents skill \(docx_render\.py\)/],
      ['deck.pptx', Buffer.from('PK\x03\x04'), /presentations skill \(pptx_render\.py\)/],
      ['budget.xlsx', Buffer.from('PK\x03\x04'), /spreadsheets skill \(sheet_render\.py\)/],
      ['clip.mp4', Buffer.concat([Buffer.from([0, 0, 0, 24]), Buffer.from('ftypisom')]), /audio-video skill \(media_frames\.py\)/],
      ['notes.bin', Buffer.from([1, 2, 3]), /not a PNG, JPEG, GIF or WebP image.*file-inspector/],
    ];
    for (const [name, content, hint] of files) {
      await writeFile(join(ws, name), content);
      const r = await view(ctx, { paths: [name] });
      expect(r.status, name).toBe('error');
      expect(r.content, name).toMatch(hint);
    }
  });

  it('enforces the per-image and per-call limits with a hint to downscale', async () => {
    const { ws, ctx } = await setup();
    const big = Buffer.concat([encodePng(10, 10), Buffer.alloc(MAX_IMAGE_BYTES)]);
    await writeFile(join(ws, 'big.png'), big);
    await writeFile(join(ws, 'wide.png'), encodePng(8001, 1));
    await writeFile(join(ws, 'broken.png'), encodePng(2, 2).subarray(0, 18));
    expect(await view(ctx, { paths: ['big.png'] })).toMatchObject({ status: 'error', content: expect.stringMatching(/limit per image; downscale it first: images skill img_view\.py preview, or render at a lower DPI/) });
    expect(await view(ctx, { paths: ['wide.png'] })).toMatchObject({ status: 'error', content: expect.stringMatching(/8001×1 is over the 8000 px limit per side; downscale it first/) });
    expect(await view(ctx, { paths: ['broken.png'] })).toMatchObject({ status: 'error', content: expect.stringContaining('unreadable') });
    // 20 MB per call: six 3.5 MB images fit five; the sixth waits for another call.
    const chunk = Buffer.concat([encodePng(10, 10), Buffer.alloc(3.5 * 1024 * 1024)]);
    const names = Array.from({ length: 6 }, (_, i) => `part-${i}.png`);
    for (const [i, n] of names.entries()) await writeFile(join(ws, n), Buffer.concat([chunk, Buffer.from([i])]));
    const r = await view(ctx, { paths: names });
    expect(r.status).toBe('ok');
    expect(r.images).toHaveLength(5);
    expect(r.content).toContain('Not shown: part-5.png: over the 20 MB limit per call; view it in another call');
  });

  it('shows what it can and reports the rest', async () => {
    const { ws, ctx } = await setup();
    await writeFile(join(ws, 'ok.png'), encodePng(4, 4));
    const r = await view(ctx, { paths: ['ok.png', 'missing.png'] });
    expect(r).toMatchObject({ status: 'ok', images: [expect.objectContaining({ name: 'ok.png' })] });
    expect(r.content).toContain('Not shown: missing.png: no such file');
  });

  it('reports a path it cannot read (not a directory, no permission) without losing the others', async () => {
    const { ws, ctx } = await setup();
    await writeFile(join(ws, 'ok.png'), encodePng(4, 4));
    await writeFile(join(ws, 'locked.png'), encodePng(4, 4));
    const r = await view(ctx, { paths: ['ok.png', 'ok.png/x.png'] });
    expect(r).toMatchObject({ status: 'ok', images: [expect.objectContaining({ name: 'ok.png' })] });
    expect(r.content).toContain('Not shown: ok.png/x.png: no such file');
    if (process.getuid?.() !== 0) {
      await chmod(join(ws, 'locked.png'), 0o000);
      const locked = await view(ctx, { paths: ['ok.png', 'locked.png'] });
      expect(locked).toMatchObject({ status: 'ok', images: [expect.objectContaining({ name: 'ok.png' })] });
      expect(locked.content).toContain('Not shown: locked.png: permission denied');
      expect(await view(ctx, { paths: ['locked.png'] })).toMatchObject({ status: 'error', content: 'locked.png: permission denied' });
      await chmod(join(ws, 'locked.png'), 0o644);
    }
  });

  it('refuses clearly when the agent’s model cannot see images', async () => {
    const { ws, ctx } = await setup(false);
    await writeFile(join(ws, 'ok.png'), encodePng(4, 4));
    expect(await view(ctx, { paths: ['ok.png'] })).toMatchObject({ status: 'error', content: expect.stringMatching(/text-model\) cannot see images/) });
  });

  it('checks the model that asked for the call: a fallback without vision is refused too', async () => {
    const { ws, ctx } = await setup(true);
    await writeFile(join(ws, 'ok.png'), encodePng(4, 4));
    expect(await view({ ...ctx, model: 'blind-model' }, { paths: ['ok.png'] })).toMatchObject({ status: 'error', content: expect.stringContaining('(blind-model) cannot see images') });
    expect(await view({ ...ctx, model: 'other-model' }, { paths: ['ok.png'] })).toMatchObject({ status: 'ok' });
  });

  it('refuses whole files a model endpoint would refuse, with a hint to re-render or convert them', async () => {
    const { ws, ctx } = await setup();
    const png = encodePng(20, 10, (x) => [x * 10, 0, 0]);
    const crc = Buffer.from(png);
    crc[45] = crc[45]! ^ 0xff; // inside the IDAT data (the chunk starts at 33)
    await writeFile(join(ws, 'cut.png'), png.subarray(0, 33)); // a render that crashed after the header
    await writeFile(join(ws, 'crc.png'), crc);
    await writeFile(join(ws, 'arith.jpg'), imageHeaders.jpeg(64, 64, 0xc9));
    await writeFile(join(ws, 'cut.webp'), imageHeaders.webp('VP8L', 64, 64).subarray(0, 32));
    const hint = 're-render it, or convert it with the images skill (img_convert.py)';
    expect(await view(ctx, { paths: ['cut.png'] })).toMatchObject({ status: 'error', content: `cut.png: it has no IEND chunk (the file is incomplete); ${hint}` });
    expect(await view(ctx, { paths: ['crc.png'] })).toMatchObject({ status: 'error', content: `crc.png: its IDAT chunk fails its CRC check (corrupt file); ${hint}` });
    expect(await view(ctx, { paths: ['arith.jpg'] })).toMatchObject({ status: 'error', content: `arith.jpg: it is an arithmetic-coded JPEG, which model providers cannot decode; ${hint}` });
    expect(await view(ctx, { paths: ['cut.webp'] })).toMatchObject({ status: 'error', content: `cut.webp: the file is truncated; ${hint}` });
  });

  it('refuses an image the model endpoint refused on its own before', async () => {
    const { ws, store, attachments, ctx } = await setup();
    const png = encodePng(6, 6);
    await writeFile(join(ws, 'odd.png'), png);
    const sha256 = await attachments.put(png, 'image/png');
    const withheld = (names: string[]) =>
      store.append({
        project_id: ctx.projectId,
        agent_id: ctx.agentId,
        type: 'images.withheld',
        payload: { run_id: 'r', reason: '400 Could not process image', images: names.map((name, i) => ({ tool_call_id: `c${i}`, sha256: i ? 'e'.repeat(64) : sha256, name })) },
      });
    // Withheld with another image, it may be innocent: it can be viewed again (alone).
    withheld(['odd.png', 'other.png']);
    expect(await view(ctx, { paths: ['odd.png'] })).toMatchObject({ status: 'ok' });
    withheld(['odd.png']);
    expect(await view(ctx, { paths: ['odd.png'] })).toMatchObject({
      status: 'error',
      content: 'odd.png: the model provider refused this image before (400 Could not process image); re-render it, or convert it with the images skill (img_convert.py), then view the new file',
    });
  });
});

describe('view_image in the agent loop', () => {
  const lastUser = (r: ChatRequest) => r.messages.at(-1) as { role: string; content: unknown };

  it('is in both toolsets; the model gets the pixels after the tool result, and the event keeps the image', async () => {
    h = await createHarness();
    const rt = newRuntime(h);
    const projectId = rt.createProject({ name: 'P', goal: 'g', settings: { desk_model: FAKE_MODEL.id, thread_model: FAKE_MODEL.id } });
    const desk = getDeskAgent(h.store.db, projectId)!;
    expect(deskToolsFor(desk).map((t) => t.name)).toContain('view_image');
    expect(threadToolsFor({ ...desk, role: 'thread' }).map((t) => t.name)).toContain('view_image');
    const png = encodePng(24, 16, (x) => (x < 12 ? [255, 0, 0] : [0, 0, 255]));
    await writeFile(join(desk.workspace_path!, 'flag.png'), png);
    h.fake.setScript((req) =>
      req.messages.some((m) => m.role === 'tool') ? text('Red on the left, blue on the right.') : tools(call('view_image', { paths: ['flag.png'] })),
    );
    rt.sendToDesk(projectId, 'What colours is flag.png?');
    await rt.whenIdle();

    const result = h.store.list({ agentId: desk.id, types: ['tool.result'] })[0];
    const image = result?.type === 'tool.result' ? result.payload.images?.[0] : undefined;
    expect(image).toMatchObject({ name: 'flag.png', media_type: 'image/png', width: 24, height: 16, bytes: png.length });
    expect(existsSync(join(h.dir, 'attachments', `${image!.sha256}.png`))).toBe(true);
    const second = h.fake.requests[1]!;
    expect(second.messages.map((m) => m.role)).toEqual(['system', 'user', 'assistant', 'tool', 'user']);
    expect(lastUser(second).content).toEqual([
      { type: 'text', text: IMAGES_HEADER },
      { type: 'image_url', image_url: { url: `data:image/png;base64,${png.toString('base64')}` } },
    ]);
    expect(getAgent(h.store.db, desk.id)?.status).toBe('idle');
    // Desk's read_thread (full) shows images as references view_image takes.
    expect(renderTranscript(h.store.list({ agentId: desk.id }))).toContain(`[image: flag.png 24×16, view_image attachment:${image!.sha256}]`);
  });

  it('sends references instead of pixels once the model has vision off, and the tool refuses for it', async () => {
    h = await createHarness();
    const rt = newRuntime(h);
    const projectId = rt.createProject({ name: 'P', goal: 'g', settings: { desk_model: FAKE_MODEL.id } });
    const desk = getDeskAgent(h.store.db, projectId)!;
    await writeFile(join(desk.workspace_path!, 'a.png'), encodePng(3, 3));
    h.fake.setScript((req) => (req.messages.some((m) => m.role === 'tool') ? text('seen') : tools(call('view_image', { paths: ['a.png'] }))));
    rt.sendToDesk(projectId, 'look');
    await rt.whenIdle();
    // The user turns vision off for the model in the registry: the earlier image becomes a reference.
    h.models.upsert({ ...FAKE_MODEL, vision: false });
    h.fake.setScript(() => text('ok'));
    rt.sendToDesk(projectId, 'again');
    await rt.whenIdle();
    // Plain text, not content parts: endpoints without vision may take string content only.
    const req = h.fake.requests.at(-1)!;
    expect(req.messages.some((m) => Array.isArray(m.content))).toBe(false);
    expect(req.messages.filter((m) => m.role === 'user').map((m) => m.content)).toContain(`${IMAGES_HEADER}\n[image: a.png 3×3]`);
    const blind = await runPreparedTool(viewImageTool, { paths: ['a.png'] }, testToolContext(desk.workspace_path!, { agentId: desk.id, services: rt.services }));
    expect(blind).toMatchObject({ status: 'error', content: expect.stringContaining(`(${FAKE_MODEL.id}) cannot see images`) });
  });
});

describe('images the model endpoint refuses', () => {
  const urlOf = (png: Buffer) => `data:image/png;base64,${png.toString('base64')}`;
  const parts = (r: ChatRequest) => r.messages.flatMap((m) => (Array.isArray(m.content) ? (m.content as Array<{ type: string; text?: string; image_url?: { url: string } }>) : []));
  const imageUrls = (r: ChatRequest) => parts(r).flatMap((p) => (p.image_url ? [p.image_url.url] : []));
  /** Text of the parts, and the lines of plain user messages (an images message without pixels is plain text). */
  const texts = (r: ChatRequest) => [
    ...parts(r).flatMap((p) => (p.type === 'text' ? [p.text!] : [])),
    ...r.messages.flatMap((m) => (m.role === 'user' && typeof m.content === 'string' ? m.content.split('\n') : [])),
  ];
  const toolResults = (r: ChatRequest) => r.messages.filter((m) => m.role === 'tool').length;
  const refused = () => error(400, 'invalid_request_error', 'Could not process image');

  async function thread(files: Record<string, Buffer>, script: Script, opts: { fallback?: string; maxAttempts?: number } = {}) {
    h = await createHarness({ script });
    const rt = newRuntime(h, { retry: { ...noSleep, maxAttempts: opts.maxAttempts ?? 3 } });
    if (opts.fallback) h.models.upsert({ ...FAKE_MODEL, id: opts.fallback, vision: false });
    const projectId = rt.createProject({ name: 'P', goal: 'G', ...(opts.fallback ? { settings: { fallback_model: opts.fallback } } : {}) });
    const workspace = join(h.dir, 'w');
    await mkdir(workspace, { recursive: true });
    for (const [name, data] of Object.entries(files)) await writeFile(join(workspace, name), data);
    const t = rt.createThread(projectId, { title: 'T', brief: 'B', workspacePath: workspace, model: FAKE_MODEL.id, parentId: null });
    const withheld = () => h!.store.list({ agentId: t, types: ['images.withheld'] }).map((e) => (e.type === 'images.withheld' ? e.payload : null)!);
    return { rt, t, withheld };
  }

  it('withholds an image the endpoint cannot process, retries without it, and never sends it again', async () => {
    const good = encodePng(8, 8, () => [0, 200, 0]);
    const bad = encodePng(8, 8, () => [200, 0, 0]);
    let phase = 1;
    const { rt, t, withheld } = await thread({ 'good.png': good, 'bad.png': bad }, (req) => {
      if (imageUrls(req).includes(urlOf(bad))) return refused();
      if (phase === 1) return toolResults(req) === 0 ? tools(call('view_image', { paths: ['bad.png'] })) : text('seen');
      return toolResults(req) === 1 ? tools(call('view_image', { paths: ['bad.png', 'good.png'] })) : text('seen again');
    });
    rt.sendMessage(t, 'look');
    await rt.whenIdle();
    const [first, rejected, retried] = h!.fake.requests;
    expect([first, rejected, retried].map((r) => imageUrls(r!))).toEqual([[], [urlOf(bad)], []]);
    expect(withheld()).toEqual([{ run_id: expect.any(String), reason: '400 Could not process image', images: [{ tool_call_id: expect.any(String), sha256: expect.any(String), name: 'bad.png' }] }]);
    expect(texts(retried!)).toContain(
      '[image not shown: bad.png 8×8, the model provider refused it (400 Could not process image); re-render it or convert it to PNG (images skill img_convert.py) before viewing it again]',
    );
    expect(getAgent(h!.store.db, t)?.status).toBe('idle');

    // Later runs never send it; viewing it again is refused before any call, the other image goes through.
    phase = 2;
    rt.sendMessage(t, 'look again');
    await rt.whenIdle();
    const later = h!.fake.requests.slice(3);
    expect(later.map((r) => imageUrls(r))).toEqual([[], [urlOf(good)]]);
    const result = h!.store.list({ agentId: t, types: ['tool.result'] }).at(-1);
    expect(result?.type === 'tool.result' && result.payload.content).toContain('Not shown: bad.png: the model provider refused this image before (400 Could not process image)');
    expect(withheld()).toHaveLength(1);
    expect(getAgent(h!.store.db, t)?.status).toBe('idle');
  });

  it('blames only the images a refusal proves guilty: an older image is found without blaming the newest', async () => {
    const [bad, good, good2] = [[200, 0, 0], [0, 200, 0], [0, 0, 200]].map((c) => encodePng(8, 8, () => c as [number, number, number]));
    let refuse: 'none' | 'bad' | 'all' = 'none';
    const { rt, t, withheld } = await thread({ 'bad.png': bad!, 'good.png': good!, 'good2.png': good2! }, (req) => {
      if (refuse === 'all' || (refuse === 'bad' && imageUrls(req).includes(urlOf(bad!)))) return refused();
      const n = toolResults(req);
      if (n === 0) return tools(call('view_image', { paths: ['bad.png'] }));
      if (n === 1) return tools(call('view_image', { paths: ['good.png', 'good2.png'] }));
      return text('done');
    });
    // Sent fine at first (as on another model before a switch), refused later.
    rt.sendMessage(t, 'look');
    await rt.whenIdle();
    refuse = 'bad';
    const before = h!.fake.requests.length;
    rt.sendMessage(t, 'again');
    await rt.whenIdle();
    // All three refused; without the newest two still refused (so the older image is at fault); without the older one it goes through.
    const retries = h!.fake.requests.slice(before);
    expect(retries.map((r) => imageUrls(r))).toEqual([[urlOf(bad!), urlOf(good!), urlOf(good2!)], [urlOf(bad!)], [urlOf(good!), urlOf(good2!)]]);
    expect(withheld().map((w) => w.images.map((i) => i.name))).toEqual([['bad.png']]);
    expect(texts(retries[2]!)).toContain(
      '[image not shown: bad.png 8×8, the model provider refused it (400 Could not process image); re-render it or convert it to PNG (images skill img_convert.py) before viewing it again]',
    );
    expect(getAgent(h!.store.db, t)?.status).toBe('idle');
    // The newest images were never blamed: view_image still takes them.
    const ctx = testToolContext(join(h!.dir, 'w'), { projectId: getAgent(h!.store.db, t)!.project_id, agentId: t, services: rt.services });
    expect(await runPreparedTool(viewImageTool, { paths: ['good.png', 'good2.png', 'bad.png'] }, ctx)).toMatchObject({
      status: 'ok',
      images: [expect.objectContaining({ name: 'good.png' }), expect.objectContaining({ name: 'good2.png' })],
      content: expect.stringContaining('Not shown: bad.png: the model provider refused this image before'),
    });

    // A refusal that is not about the images after all: every retry fails too, the run fails as before, and nothing is recorded.
    refuse = 'all';
    const last = h!.fake.requests.length;
    rt.sendMessage(t, 'more');
    await rt.whenIdle();
    expect(h!.fake.requests.slice(last).map((r) => imageUrls(r).length)).toEqual([2, 0]);
    expect(withheld()).toHaveLength(1);
    expect(getAgent(h!.store.db, t)).toMatchObject({ status: 'failed' });
  });

  it('withholds both groups when each holds an image the endpoint refuses', async () => {
    const [bad1, ok1, bad2] = [[200, 0, 0], [0, 200, 0], [0, 0, 200]].map((c) => encodePng(8, 8, () => c as [number, number, number]));
    let refuse = false;
    const { rt, t, withheld } = await thread({ 'bad1.png': bad1!, 'ok1.png': ok1!, 'bad2.png': bad2! }, (req) => {
      if (refuse && (imageUrls(req).includes(urlOf(bad1!)) || imageUrls(req).includes(urlOf(bad2!)))) return refused();
      const n = toolResults(req);
      if (n === 0) return tools(call('view_image', { paths: ['bad1.png', 'ok1.png'] }));
      if (n === 1) return tools(call('view_image', { paths: ['bad2.png'] }));
      return text('done');
    });
    rt.sendMessage(t, 'look');
    await rt.whenIdle();
    refuse = true;
    const before = h!.fake.requests.length;
    rt.sendMessage(t, 'again');
    await rt.whenIdle();
    expect(h!.fake.requests.slice(before).map((r) => imageUrls(r).length)).toEqual([3, 2, 1, 0]);
    expect(withheld().map((w) => [w.images.map((i) => i.name), w.images.length])).toEqual([
      [['bad2.png'], 1],
      [['bad1.png', 'ok1.png'], 2],
    ]);
    expect(getAgent(h!.store.db, t)?.status).toBe('idle');
  });

  it('sends fewer images after a request is refused as too large, and keeps the lower budget', async () => {
    const pngs = [0, 1, 2].map((i) => encodePng(30, 30, (x, y) => [i * 80, x * 8, y * 8]));
    const { rt, t, withheld } = await thread({ 'a.png': pngs[0]!, 'b.png': pngs[1]!, 'c.png': pngs[2]! }, (req) => {
      if (imageUrls(req).length >= 2) return error(413, 'request_too_large', 'Request exceeds the maximum size');
      const n = toolResults(req);
      if (n === 0) return tools(call('view_image', { paths: ['a.png', 'b.png', 'c.png'] }));
      if (n === 1) return tools(call('list_dir', {}));
      return text('done');
    });
    rt.sendMessage(t, 'look');
    await rt.whenIdle();
    const reqs = h!.fake.requests;
    expect(reqs.map((r) => imageUrls(r).length)).toEqual([0, 3, 1, 1]);
    expect(imageUrls(reqs[2]!)).toEqual([urlOf(pngs[2]!)]);
    // The model never saw a and b (the request with them was refused): they are not "no longer shown".
    expect(texts(reqs[2]!)).toContain('[image not shown: a.png 30×30 — more images than the model is shown at once; view fewer or smaller images at a time]');
    expect(withheld()).toEqual([]);
    expect(getAgent(h!.store.db, t)?.status).toBe('idle');
  });

  it('keeps the image budget when a request is too large for another reason: fewer images did not help', async () => {
    const pngs = [0, 1, 2].map((i) => encodePng(30, 30, (x, y) => [i * 80, x * 8, y * 8]));
    let tooLarge = false;
    const { rt, t } = await thread({ 'a.png': pngs[0]!, 'b.png': pngs[1]!, 'c.png': pngs[2]! }, (req) => {
      if (tooLarge) return error(413, 'request_too_large', 'Request exceeds the maximum size');
      return toolResults(req) === 0 ? tools(call('view_image', { paths: ['a.png', 'b.png', 'c.png'] })) : text('done');
    });
    rt.sendMessage(t, 'look');
    await rt.whenIdle();
    tooLarge = true;
    const before = h!.fake.requests.length;
    rt.sendMessage(t, 'more');
    await rt.whenIdle();
    // Halved until no image is left, still too large: the run fails and the budget stays as it was.
    expect(h!.fake.requests.slice(before).map((r) => imageUrls(r).length)).toEqual([3, 1, 0]);
    expect(getAgent(h!.store.db, t)).toMatchObject({ status: 'failed' });
    tooLarge = false;
    const after = h!.fake.requests.length;
    rt.sendMessage(t, 'again');
    await rt.whenIdle();
    expect(imageUrls(h!.fake.requests[after]!)).toHaveLength(3);
  });

  it('puts the images of parallel view_image calls in one message after all their results, in call order', async () => {
    const [a, b] = [encodePng(5, 5, () => [1, 1, 1]), encodePng(6, 6, () => [2, 2, 2])];
    const { rt, t } = await thread({ 'a.png': a, 'b.png': b }, (req) =>
      toolResults(req) === 0 ? tools(call('view_image', { paths: ['a.png'] }), call('view_image', { paths: ['b.png'] })) : text('both seen'),
    );
    rt.sendMessage(t, 'look');
    await rt.whenIdle();
    const second = h!.fake.requests[1]!;
    expect(second.messages.map((m) => m.role)).toEqual(['system', 'user', 'assistant', 'tool', 'tool', 'user']);
    expect(second.messages.at(-1)?.content).toEqual([
      { type: 'text', text: IMAGES_HEADER },
      { type: 'image_url', image_url: { url: urlOf(a) } },
      { type: 'image_url', image_url: { url: urlOf(b) } },
    ]);
  });

  it('gives a fallback model without vision text references, and view_image refuses under it', async () => {
    const png = encodePng(4, 4);
    let limited = false;
    const { rt, t } = await thread(
      { 'a.png': png },
      (req) => {
        if (req.model === FAKE_MODEL.id) {
          if (limited) return error(429, 'rate_limit_error', 'slow down');
          return toolResults(req) === 0 ? tools(call('view_image', { paths: ['a.png'] })) : text('seen');
        }
        const last = req.messages.at(-1);
        return last?.role === 'user' && typeof last.content === 'string' ? tools(call('view_image', { paths: ['a.png'] })) : text('could not look');
      },
      { fallback: 'blind-model', maxAttempts: 2 },
    );
    rt.sendMessage(t, 'look');
    await rt.whenIdle();
    limited = true;
    rt.sendMessage(t, 'look again');
    await rt.whenIdle();
    const blind = h!.fake.requests.filter((r) => r.model === 'blind-model');
    expect(blind).toHaveLength(2);
    expect(blind.flatMap(imageUrls)).toEqual([]);
    expect(texts(blind[0]!)).toContain('[image: a.png 4×4]');
    const result = h!.store.list({ agentId: t, types: ['tool.result'] }).at(-1);
    expect(result?.type === 'tool.result' && result.payload).toMatchObject({ status: 'error', content: expect.stringContaining('(blind-model) cannot see images') });
  });

  it('lets Desk look at an image a thread looked at, by the reference read_thread shows, within the project only', async () => {
    h = await createHarness();
    const rt = newRuntime(h);
    const projectId = rt.createProject({ name: 'P', goal: 'G' });
    const desk = getDeskAgent(h.store.db, projectId)!;
    const ws = join(h.dir, 'thread-ws');
    await mkdir(join(ws, 'renders'), { recursive: true });
    await writeFile(join(ws, 'renders', 'page-1.png'), encodePng(12, 9));
    const t = rt.createThread(projectId, { title: 'T', brief: 'B', workspacePath: ws, parentId: desk.id });
    const r = await runPreparedTool(viewImageTool, { paths: ['renders/page-1.png'] }, testToolContext(ws, { projectId, agentId: t, services: rt.services }));
    h.store.append({ project_id: projectId, agent_id: t, type: 'tool.result', payload: { run_id: 'r', tool_call_id: 'c1', name: 'view_image', status: r.status, content: r.content, images: r.images! } });
    const sha = r.images![0]!.sha256;
    expect(renderTranscript(h.store.list({ agentId: t }))).toContain(`[image: renders/page-1.png 12×9, view_image attachment:${sha}]`);

    const deskView = await runPreparedTool(viewImageTool, { paths: [`attachment:${sha}`] }, testToolContext(desk.workspace_path!, { projectId, agentId: desk.id, services: rt.services }));
    expect(deskView).toMatchObject({ status: 'ok', images: [{ sha256: sha, name: 'renders/page-1.png', width: 12, height: 9, media_type: 'image/png' }] });
    const other = rt.createProject({ name: 'Q', goal: 'G' });
    const otherDesk = getDeskAgent(h.store.db, other)!;
    const ctx = testToolContext(otherDesk.workspace_path!, { projectId: other, agentId: otherDesk.id, services: rt.services });
    expect(await runPreparedTool(viewImageTool, { paths: [`attachment:${sha}`] }, ctx)).toMatchObject({
      status: 'error',
      content: `attachment:${sha}: no image with this digest was viewed recently in this project`,
    });
    // A digest with no stored image is refused without searching the project's events (findToolImage reads store.db).
    const scans: string[] = [];
    const store = new Proxy(rt.services.store, {
      get: (s, p) => (p === 'db' ? (scans.push('db'), s.db) : typeof Reflect.get(s, p) === 'function' ? Reflect.get(s, p).bind(s) : Reflect.get(s, p)),
    });
    const services = new Proxy(rt.services, { get: (target, prop) => (prop === 'store' ? store : Reflect.get(target, prop)) });
    const unknown = await runPreparedTool(viewImageTool, { paths: [`attachment:${'e'.repeat(64)}`] }, testToolContext(desk.workspace_path!, { projectId, agentId: desk.id, services }));
    expect(unknown).toMatchObject({ status: 'error', content: expect.stringContaining('no image with this digest') });
    expect(scans).toEqual([]);
  });
});
