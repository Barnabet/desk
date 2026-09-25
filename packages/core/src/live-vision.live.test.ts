import { randomInt } from 'node:crypto';
import { mkdir, mkdtemp, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { createModelAdapter, EventStore, getAgent, loadModelConfig, ModelRegistry, openDb, renderTranscript, Runtime } from './index';
import { encodePng } from './testing/png';

/** 5×7 glyphs, one string of 35 bits per character, for characters that are hard to confuse. */
const FONT: Record<string, string> = {
  A: '01110100011000111111100011000110001',
  C: '01111100001000010000100001000001111',
  E: '11111100001000011110100001000011111',
  F: '11111100001000011110100001000010000',
  H: '10001100011000111111100011000110001',
  K: '10001100101010011000101001001010001',
  L: '10000100001000010000100001000011111',
  M: '10001110111010110101100011000110001',
  N: '10001110011010110011100011000110001',
  P: '11110100011000111110100001000010000',
  R: '11110100011000111110101001001010001',
  T: '11111001000010000100001000010000100',
  U: '10001100011000110001100011000101110',
  W: '10001100011000110101101011010101010',
  X: '10001100010101000100010101000110001',
  Y: '10001100010101000100001000010000100',
  '3': '11110000010000101110000010000111110',
  '4': '00010001100101010010111110001000010',
  '7': '11111000010001000100010000100001000',
};

/** A PNG with `code` drawn in black on white, large and clear. */
function codePng(code: string): Buffer {
  const scale = 10;
  const gap = 20;
  const margin = 40;
  const width = margin * 2 + code.length * 5 * scale + (code.length - 1) * gap;
  const height = margin * 2 + 7 * scale;
  return encodePng(width, height, (x, y) => {
    const gx = x - margin;
    const gy = Math.floor((y - margin) / scale);
    if (gx < 0 || gy < 0 || gy >= 7) return [255, 255, 255];
    const slot = Math.floor(gx / (5 * scale + gap));
    const within = gx - slot * (5 * scale + gap);
    const glyph = FONT[code[slot] ?? ''];
    if (!glyph || within >= 5 * scale) return [255, 255, 255];
    return glyph[gy * 5 + Math.floor(within / scale)] === '1' ? [0, 0, 0] : [255, 255, 255];
  });
}

const MODELS = ['claude-opus-5-5', 'gpt-6-sol'];

describe.each(MODELS)('live vision: %s', (model) => {
  it('answers a question only the pixels can answer, after view_image', async (ctx) => {
    const alphabet = Object.keys(FONT);
    const code = Array.from({ length: 6 }, () => alphabet[randomInt(alphabet.length)]!).join('');
    const dir = await realpath(await mkdtemp(join(tmpdir(), 'desk-live-vision-')));
    const { db, close } = openDb(':memory:');
    const store = new EventStore(db);
    const models = new ModelRegistry();
    const runtime = new Runtime({ store, adapter: createModelAdapter(loadModelConfig(), models), models, dataDir: dir, retry: { maxAttempts: 3 } });
    try {
      const projectId = runtime.createProject({ name: 'Live vision', goal: 'Verify that agents can see images' });
      const workspace = join(dir, 'ws');
      await mkdir(workspace, { recursive: true });
      await writeFile(join(workspace, 'code.png'), codePng(code));
      const agentId = runtime.createThread(projectId, {
        title: 'Read the code',
        brief:
          'The image code.png in your workspace shows a short code of capital letters and digits. Look at it with view_image, then call complete with a summary that is exactly the code as shown and nothing else.',
        workspacePath: workspace,
        model,
      });
      runtime.sendMessage(agentId, 'Start now.');
      await runtime.whenIdle();

      const finished = store.list({ agentId, types: ['run.finished'] }).at(-1);
      if (finished?.type === 'run.finished' && finished.payload.reason === 'error' && /rate limit|429/i.test(finished.payload.detail ?? '')) ctx.skip();
      const transcript = renderTranscript(store.list({ agentId }));
      if (process.env.DESK_LIVE_VERBOSE) console.log(`code ${code}\n${transcript}`);

      const calls = store.list({ agentId, types: ['tool.call'] }).map((e) => (e.type === 'tool.call' ? e.payload.name : ''));
      expect(calls, transcript).toContain('view_image');
      const viewed = store.list({ agentId, types: ['tool.result'] }).find((e) => e.type === 'tool.result' && e.payload.images?.length);
      expect(viewed?.type === 'tool.result' && viewed.payload.images?.[0]).toMatchObject({ name: 'code.png', media_type: 'image/png' });
      const agent = getAgent(db, agentId);
      expect(agent?.status, transcript).toBe('done');
      expect((agent?.result_summary ?? '').toUpperCase().replace(/[^A-Z0-9]/g, ''), transcript).toBe(code);
    } finally {
      close();
      await rm(dir, { recursive: true, force: true });
    }
  });
});
