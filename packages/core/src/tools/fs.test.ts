import { mkdir, mkdtemp, readFile, realpath, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { editFileTool, globTool, grepFallback, grepTool, listDirTool, readFileTool, writeFileTool } from './fs';
import { testToolContext } from '../testing/context';
import { NO_SANDBOX, sandboxGuard } from './sandbox';
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
  ctx = testToolContext(ws, { readRoots: [ws, src] });
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

describe('guard', () => {
  // `outer` plays a project source that contains Desk's data dir (like a source at ~); the workspace lives inside it.
  let outer: string;
  let data: string;
  let work: string;
  let secret: string;
  let g: ToolContext;
  beforeEach(async () => {
    outer = await realpath(base);
    data = join(outer, 'data');
    work = join(data, 'workspaces', 't1');
    secret = join(data, 'daemon.json');
    await mkdir(work, { recursive: true });
    await writeFile(secret, '{"token":"tok-123"}');
    await symlink(secret, join(work, 'link.json'));
    g = testToolContext(work, { readRoots: [work, outer], writeRoots: [work, outer], sandbox: { ...NO_SANDBOX, guard: sandboxGuard({ dataDir: data, secrets: [secret] }) } });
  });

  it('never reads a secret, directly or through a symlink, and never searches a folder that holds one', async () => {
    await expect(readFileTool.execute({ path: secret }, g)).rejects.toThrow(/off limits because it holds Desk credentials/);
    await expect(readFileTool.execute({ path: 'link.json' }, g)).rejects.toThrow(ToolDenied);
    await expect(grepTool.execute({ pattern: 'tok', root: outer }, g)).rejects.toThrow(/off limits because it contains Desk credentials/);
    await expect(grepTool.execute({ pattern: 'tok', root: data }, g)).rejects.toThrow(ToolDenied);
    // The workspace holds only a symlink to the secret: searches skip symlinks.
    expect(await grepTool.execute({ pattern: 'tok', root: '.' }, g)).not.toContain('tok-123');
    expect(await grepFallback('tok', work)).not.toContain('tok-123');
    expect(await listDirTool.execute({ path: data }, g)).toContain('daemon.json');
  });

  it("writes Desk's data dir only inside the workspace, even under a writable source", async () => {
    await writeFileTool.execute({ path: 'notes.md', content: 'x' }, g);
    await writeFileTool.execute({ path: join(outer, 'source.md'), content: 'x' }, g);
    await expect(writeFileTool.execute({ path: join(data, 'config.json'), content: '{}' }, g)).rejects.toThrow(/off limits because it is inside Desk's data folder/);
    await expect(writeFileTool.execute({ path: secret, content: '{}' }, g)).rejects.toThrow(/holds Desk credentials/);
    await expect(editFileTool.execute({ path: secret, old_string: 'tok', new_string: 'x' }, g)).rejects.toThrow(ToolDenied);
    expect(await readFile(secret, 'utf8')).toBe('{"token":"tok-123"}');
  });

  it("never writes the git or ssh config deskd's git runs with", async () => {
    const ro = testToolContext(work, { readRoots: [work, outer], writeRoots: [work, outer], sandbox: { ...NO_SANDBOX, guard: sandboxGuard({ dataDir: data, secrets: [secret], readOnly: [join(outer, '.gitconfig'), join(outer, '.ssh')] }) } });
    await expect(writeFileTool.execute({ path: join(outer, '.gitconfig'), content: '[filter "x"]' }, ro)).rejects.toThrow(/git or ssh configuration/);
    await expect(writeFileTool.execute({ path: join(outer, '.ssh', 'config'), content: 'Host *' }, ro)).rejects.toThrow(/git or ssh configuration/);
  });

  it('never writes git internals: deskd runs git outside the sandbox', async () => {
    await mkdir(join(work, '.git', 'hooks'), { recursive: true });
    await expect(writeFileTool.execute({ path: '.git/hooks/pre-commit', content: '#!/bin/sh' }, g)).rejects.toThrow(/inside a \.git folder/);
    await expect(writeFileTool.execute({ path: '.git/config', content: '' }, g)).rejects.toThrow(/inside a \.git folder/);
    await expect(writeFileTool.execute({ path: join(outer, 'repo', '.git'), content: 'gitdir: /tmp/x' }, g)).rejects.toThrow(/inside a \.git folder/);
    await writeFileTool.execute({ path: '.gitignore', content: 'dist\n' }, g);
    await writeFileTool.execute({ path: '.github/workflows/ci.yml', content: 'on: push\n' }, g);
  });
});
