import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { editFileTool, globTool, grepFallback, grepTool, listDirTool, readFileTool, writeFileTool } from './fs';
import { NO_SANDBOX } from './sandbox';
import { ToolDenied, type ToolContext } from './types';

let base: string;
let ws: string;
let src: string;
let ctx: ToolContext;

beforeEach(async () => {
  base = await mkdtemp(join(tmpdir(), 'desk-fs-'));
  ws = join(base, 'ws');
  src = join(base, 'src');
  await mkdir(ws);
  await mkdir(join(src, 'lib'), { recursive: true });
  await writeFile(join(src, 'readme.md'), 'line one\nline two\nline three');
  await writeFile(join(src, 'lib', 'util.ts'), 'export const answer = 42;\n');
  ctx = { projectId: 'p', agentId: 'a', runId: 'r', toolCallId: 't', workspace: ws, readRoots: [ws, src], signal: new AbortController().signal, sandbox: NO_SANDBOX };
});
afterEach(async () => rm(base, { recursive: true, force: true }));

describe('read_file', () => {
  it('returns numbered lines', async () => {
    expect(await readFileTool.execute({ path: join(src, 'readme.md') }, ctx)).toBe('1\tline one\n2\tline two\n3\tline three');
  });

  it('supports offset and limit with a range note', async () => {
    expect(await readFileTool.execute({ path: join(src, 'readme.md'), offset: 2, limit: 1 }, ctx)).toBe('2\tline two\n[lines 2-2 of 3]');
  });

  it('denies reads outside read roots', async () => {
    await expect(readFileTool.execute({ path: join(base, 'x.txt') }, ctx)).rejects.toBeInstanceOf(ToolDenied);
  });
});

describe('write_file', () => {
  it('writes into the workspace, creating directories', async () => {
    await writeFileTool.execute({ path: 'out/notes.md', content: 'hello' }, ctx);
    expect(await readFile(join(ws, 'out', 'notes.md'), 'utf8')).toBe('hello');
  });

  it('denies writes to read-only sources', async () => {
    await expect(writeFileTool.execute({ path: join(src, 'readme.md'), content: 'x' }, ctx)).rejects.toBeInstanceOf(ToolDenied);
  });
});

describe('edit_file', () => {
  beforeEach(async () => writeFile(join(ws, 'f.txt'), 'a b a c'));

  it('replaces a unique match', async () => {
    await writeFile(join(ws, 'g.txt'), 'hello world');
    await editFileTool.execute({ path: 'g.txt', old_string: 'world', new_string: 'desk' }, ctx);
    expect(await readFile(join(ws, 'g.txt'), 'utf8')).toBe('hello desk');
  });

  it('rejects ambiguous matches unless replace_all', async () => {
    await expect(editFileTool.execute({ path: 'f.txt', old_string: 'a', new_string: 'z' }, ctx)).rejects.toThrow(/matches 2 times/);
    await editFileTool.execute({ path: 'f.txt', old_string: 'a', new_string: 'z', replace_all: true }, ctx);
    expect(await readFile(join(ws, 'f.txt'), 'utf8')).toBe('z b z c');
  });

  it('rejects missing matches', async () => {
    await expect(editFileTool.execute({ path: 'f.txt', old_string: 'q', new_string: 'z' }, ctx)).rejects.toThrow(/not found/);
  });

  it('treats $ in new_string literally', async () => {
    await writeFile(join(ws, 'h.txt'), 'price');
    await editFileTool.execute({ path: 'h.txt', old_string: 'price', new_string: '$& $1' }, ctx);
    expect(await readFile(join(ws, 'h.txt'), 'utf8')).toBe('$& $1');
  });
});

describe('list_dir and glob', () => {
  it('lists entries with trailing slash for directories', async () => {
    expect(await listDirTool.execute({ path: src }, ctx)).toBe('lib/\nreadme.md');
  });

  it('globs relative to root', async () => {
    expect(await globTool.execute({ pattern: '**/*.ts', root: src }, ctx)).toBe('lib/util.ts');
    expect(await globTool.execute({ pattern: '*.none', root: src }, ctx)).toBe('No files matched');
  });
});

describe('grep', () => {
  it('finds matches with file and line', async () => {
    const out = await grepTool.execute({ pattern: 'answer', root: src }, ctx);
    expect(out).toBe('lib/util.ts:1:export const answer = 42;');
  });

  it('reports no matches', async () => {
    expect(await grepTool.execute({ pattern: 'zzz_nope', root: src }, ctx)).toBe('No matches');
  });

  it('has a JS fallback with the same output shape', async () => {
    expect(await grepFallback('two', src)).toBe('readme.md:2:line two');
    expect(await grepFallback('answer', src, '*.ts')).toBe('lib/util.ts:1:export const answer = 42;');
  });
});
