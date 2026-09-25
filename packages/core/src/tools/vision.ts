import { createHash } from 'node:crypto';
import { open, readFile, realpath, stat } from 'node:fs/promises';
import { extname, isAbsolute, relative, sep } from 'node:path';
import { z } from 'zod';
import type { ImageMediaType, ToolImage } from '@desk/protocol';
import { imageProblem, readImageInfo, sniffImageType } from '../attachments/image';
import { MAX_IMAGES_SHOWN } from '../agent/transcript';
import { findToolImage } from '../state/queries';
import { resolveInside } from './paths';
import { defineTool, ToolDenied, type Tool, type ToolContext } from './types';

/** Per image: the largest file a provider takes once base64-encoded (5 MB). */
export const MAX_IMAGE_BYTES = 3.75 * 1024 * 1024;
/** Per image, per side. */
export const MAX_IMAGE_SIDE = 8000;
/** Per view_image call, all images together. */
export const MAX_CALL_BYTES = 20 * 1024 * 1024;
export const MAX_IMAGES_PER_CALL = 8;

const LIMIT_HINT = 'downscale it first: images skill img_view.py preview, or render at a lower DPI';
const REPAIR_HINT = 're-render it, or convert it with the images skill (img_convert.py)';
/** A path naming an image another agent of the project looked at (read_thread shows these). */
const ATTACHMENT = /^attachment:([0-9a-f]{64})$/;
const FORMAT_LABEL: Record<ImageMediaType, string> = { 'image/png': 'PNG', 'image/jpeg': 'JPEG', 'image/gif': 'GIF', 'image/webp': 'WebP' };

/** Which file skill renders a format to PNG, by extension. */
const RENDERERS: Array<{ exts: string[]; skill: string; script: string }> = [
  { exts: ['pdf'], skill: 'pdf-toolkit', script: 'pdf_render.py' },
  { exts: ['docx', 'docm', 'dotx', 'dotm', 'doc', 'odt', 'rtf'], skill: 'word-documents', script: 'docx_render.py' },
  { exts: ['pptx', 'pptm', 'potx', 'ppsx', 'ppt', 'odp'], skill: 'presentations', script: 'pptx_render.py' },
  { exts: ['xlsx', 'xlsm', 'xltx', 'xls', 'xlsb', 'ods', 'csv', 'tsv'], skill: 'spreadsheets', script: 'sheet_render.py' },
  { exts: ['mp4', 'mov', 'm4v', 'mkv', 'webm', 'avi'], skill: 'audio-video', script: 'media_frames.py' },
  { exts: ['mp3', 'wav', 'm4a', 'aac', 'flac', 'ogg', 'opus'], skill: 'audio-video', script: 'media_audio.py' },
  { exts: ['md', 'html', 'htm', 'rst', 'tex', 'epub', 'ipynb', 'typ'], skill: 'markup-ebooks', script: 'mk_render.py' },
  { exts: ['ttf', 'otf', 'woff', 'woff2'], skill: 'images', script: 'font_tool.py' },
  {
    exts: ['svg', 'svgz', 'heic', 'heif', 'avif', 'jxl', 'tif', 'tiff', 'bmp', 'ico', 'icns', 'psd', 'dng', 'cr2', 'cr3', 'nef', 'arw', 'raf', 'orf', 'rw2'],
    skill: 'images',
    script: 'img_view.py',
  },
];

/** The format a header names when the extension says nothing useful. */
function formatFromMagic(b: Uint8Array): string | null {
  const head = Buffer.from(b.subarray(0, 512)).toString('latin1');
  if (head.startsWith('%PDF')) return 'pdf';
  if (/^\s*(<\?xml[^>]*>\s*)?(<!--[\s\S]*?-->\s*)*(<!DOCTYPE svg[^>]*>\s*)?<svg[\s>]/i.test(head)) return 'svg';
  if (head.slice(4, 8) === 'ftyp') {
    const brand = head.slice(8, 12);
    if (/^(heic|heix|hevc|heim|heis|mif1|msf1)$/.test(brand)) return 'heic';
    if (/^avi[fs]$/.test(brand)) return 'avif';
    return 'mp4';
  }
  if (head.startsWith('II*\0') || head.startsWith('MM\0*')) return 'tiff';
  if (head.startsWith('BM')) return 'bmp';
  if (head.startsWith('8BPS')) return 'psd';
  if (head.startsWith('\0\0\x01\0')) return 'ico';
  return null;
}

function unsupported(name: string, file: string, head: Uint8Array): string {
  const ext = extname(file).slice(1).toLowerCase();
  const known = RENDERERS.find((r) => r.exts.includes(ext)) ? ext : (formatFromMagic(head) ?? ext);
  const r = RENDERERS.find((x) => x.exts.includes(known));
  if (!r) {
    return `${name}: not a PNG, JPEG, GIF or WebP image. Render it to PNG with its file skill (the file-inspector skill's file_identify.py says which one), then view the PNG.`;
  }
  return `${name}: ${known.toUpperCase()} is not an image view_image takes (PNG, JPEG, GIF, WebP). Render it to PNG with the ${r.skill} skill (${r.script}), then view the PNG.`;
}

function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const mb = (n: number) => `${+(n / (1024 * 1024)).toFixed(2)} MB`;

async function readHead(file: string, n: number): Promise<Buffer> {
  const fh = await open(file, 'r');
  try {
    const buf = Buffer.alloc(n);
    const { bytesRead } = await fh.read(buf, 0, n, 0);
    return buf.subarray(0, bytesRead);
  } finally {
    await fh.close();
  }
}

/** How the transcript names an image: relative to the workspace when inside it, else the absolute path. */
async function displayName(real: string, ctx: ToolContext): Promise<string> {
  const ws = await realpath(ctx.workspace).catch(() => ctx.workspace);
  const rel = relative(ws, real);
  return rel && !rel.startsWith('..') && !isAbsolute(rel) ? rel.split(sep).join('/') : real;
}

type Loaded = { ok: true; image: ToolImage; data: Buffer } | { ok: false; line: string; denied?: boolean };

/** Why a path could not be read, for its line in the result (the other paths are still shown). */
function readFailure(path: string, err: unknown): Loaded {
  const code = (err as { code?: unknown } | null)?.code;
  const why =
    code === 'ENOENT' || code === 'ENOTDIR'
      ? 'no such file'
      : code === 'EACCES' || code === 'EPERM'
        ? 'permission denied'
        : code === 'EISDIR'
          ? 'not a file'
          : code === 'ELOOP'
            ? 'too many levels of symbolic links'
            : `cannot read it (${err instanceof Error ? err.message : String(err)})`;
  return { ok: false, line: `${path}: ${why}` };
}

/** Images the model endpoint refused on their own earlier (images.withheld with one image), by sha256: why. */
function refusedImages(ctx: ToolContext): Map<string, string> {
  const out = new Map<string, string>();
  for (const ev of ctx.services.store.list({ agentId: ctx.agentId, types: ['images.withheld'] })) {
    if (ev.type === 'images.withheld' && ev.payload.images.length === 1) out.set(ev.payload.images[0]!.sha256, ev.payload.reason);
  }
  return out;
}

/**
 * An `attachment:<sha256>` path: an image some agent of this project already looked at, checked then. The store is
 * checked first, so a mistyped digest never searches the project's events.
 */
async function loadAttachment(path: string, sha256: string, ctx: ToolContext, budget: number, refused: Map<string, string>): Promise<Loaded> {
  const known = ctx.services.attachments.find(sha256) ? findToolImage(ctx.services.store.db, ctx.projectId, sha256) : undefined;
  const stored = known ? await ctx.services.attachments.read(sha256) : null;
  if (!known || !stored) return { ok: false, line: `${path}: no image with this digest was viewed recently in this project` };
  const why = refused.get(sha256);
  if (why) return { ok: false, line: `${known.name}: the model provider refused this image before (${why})` };
  if (stored.data.length > budget) return { ok: false, line: `${known.name}: over the ${mb(MAX_CALL_BYTES)} limit per call; view it in another call` };
  return { ok: true, data: stored.data, image: known };
}

async function load(path: string, ctx: ToolContext, budget: number, refused: Map<string, string>): Promise<Loaded> {
  const attachment = ATTACHMENT.exec(path.trim());
  if (attachment) return loadAttachment(path, attachment[1]!, ctx, budget, refused);
  let file: string;
  try {
    file = await resolveInside(path, ctx.readRoots, ctx.workspace);
  } catch (err) {
    if (err instanceof ToolDenied) return { ok: false, line: `${path}: outside the directories you may read`, denied: true };
    throw err;
  }
  const name = await displayName(file, ctx);
  const st = await stat(file).catch(() => null);
  if (!st) return { ok: false, line: `${name}: no such file` };
  if (!st.isFile()) return { ok: false, line: `${name}: not a file` };
  const head = await readHead(file, 64 * 1024);
  const type = sniffImageType(head);
  if (!type) return { ok: false, line: unsupported(name, file, head) };
  if (st.size > MAX_IMAGE_BYTES) return { ok: false, line: `${name}: ${mb(st.size)} is over the ${mb(MAX_IMAGE_BYTES)} limit per image; ${LIMIT_HINT}` };
  if (st.size > budget) return { ok: false, line: `${name}: over the ${mb(MAX_CALL_BYTES)} limit per call; view it in another call` };
  const data = await readFile(file);
  if (data.length > Math.min(MAX_IMAGE_BYTES, budget)) return { ok: false, line: `${name}: the file grew past the size limits while it was read; view it again` };
  const info = readImageInfo(data);
  if (!info) return { ok: false, line: `${name}: the ${FORMAT_LABEL[type]} header is unreadable (corrupt or truncated file)` };
  if (info.width > MAX_IMAGE_SIDE || info.height > MAX_IMAGE_SIDE) {
    return { ok: false, line: `${name}: ${info.width}×${info.height} is over the ${MAX_IMAGE_SIDE} px limit per side; ${LIMIT_HINT}` };
  }
  // Model endpoints refuse a request with an image they cannot decode, so a broken file never reaches one.
  const problem = await imageProblem(data, info);
  if (problem) return { ok: false, line: `${name}: ${problem}; ${REPAIR_HINT}` };
  const sha256 = createHash('sha256').update(data).digest('hex');
  const why = refused.get(sha256);
  if (why) return { ok: false, line: `${name}: the model provider refused this image before (${why}); ${REPAIR_HINT}, then view the new file` };
  await ctx.services.attachments.put(data, info.media_type);
  return { ok: true, data, image: { sha256, media_type: info.media_type, width: info.width, height: info.height, bytes: data.length, name } };
}

export const viewImageTool = defineTool({
  name: 'view_image',
  description: [
    `Look at images: PNG, JPEG, GIF or WebP files (1–${MAX_IMAGES_PER_CALL} per call). You see the pixels right after the result.`,
    'To see a document, page, slide, sheet, video frame or any other file, first render it to PNG with its file skill (pdf-toolkit pdf_render.py, word-documents docx_render.py, presentations pptx_render.py, spreadsheets sheet_render.py, images img_view.py), then view the PNGs.',
    `Limits: ${mb(MAX_IMAGE_BYTES)} and ${MAX_IMAGE_SIDE} px per side per image, ${mb(MAX_CALL_BYTES)} per call. Only the ${MAX_IMAGES_SHOWN} most recent images stay visible (parallel calls in one step count together); view one again when you need it later.`,
    'A path may also be attachment:<sha256>, an image another agent of the project looked at (read_thread lists them).',
  ].join(' '),
  input: z.object({
    paths: z.array(z.string().min(1)).min(1).max(MAX_IMAGES_PER_CALL).describe('Image files (absolute, or relative to your workspace), or attachment:<sha256> references'),
    purpose: z.string().max(500).optional().describe('What you are looking for (shown to the user)'),
  }),
  async execute({ paths }, ctx) {
    const model = ctx.services.agentModel(ctx.agentId, ctx.model);
    if (!model.vision) {
      throw new Error(
        `The model this agent runs on (${model.id}) cannot see images: vision is off for it in the model registry. Read the file's text with its skill instead, or ask the user for a model that sees images.`,
      );
    }
    const images: ToolImage[] = [];
    const failed: Array<Extract<Loaded, { ok: false }>> = [];
    let budget = MAX_CALL_BYTES;
    const refused = refusedImages(ctx);
    for (const path of paths) {
      // One unreadable path (not a directory, no permission, a disk error) is reported; the others are still shown.
      const r = await load(path, ctx, budget, refused).catch((err: unknown) => readFailure(path, err));
      if (!r.ok) {
        failed.push(r);
        continue;
      }
      budget -= r.data.length;
      images.push(r.image);
    }
    if (!images.length) {
      const msg = failed.map((f) => f.line).join('\n');
      throw failed.every((f) => f.denied) ? new ToolDenied(msg) : new Error(msg);
    }
    const lines = images.map((i) => `${i.name} · ${i.width}×${i.height} · ${FORMAT_LABEL[i.media_type]} · ${humanSize(i.bytes)}`);
    return { content: [...lines, ...failed.map((f) => `Not shown: ${f.line}`)].join('\n'), images };
  },
});

export const visionTools: Tool[] = [viewImageTool];
