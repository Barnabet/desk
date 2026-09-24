import { mkdir, readdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import fg from 'fast-glob';
import { z } from 'zod';
import { resolveInside } from './paths';
import { runProcess } from './process';
import { defineTool, type Tool, type ToolContext } from './types';

const DEFAULT_READ_LIMIT = 2000;
const MAX_LIST_RESULTS = 500;
const IGNORE = ['**/node_modules/**', '**/.git/**'];

const readable = (p: string, ctx: ToolContext) => resolveInside(p, ctx.readRoots, ctx.workspace);
const writable = (p: string, ctx: ToolContext) => resolveInside(p, ctx.writeRoots ?? [ctx.workspace], ctx.workspace);

export const readFileTool = defineTool({
  name: 'read_file',
  description: 'Read a text file. Returns numbered lines (line<TAB>text). Use offset (1-based) and limit for large files.',
  input: z.object({
    path: z.string().describe('Absolute path, or relative to your workspace'),
    offset: z.number().int().min(1).optional(),
    limit: z.number().int().min(1).max(10_000).optional(),
  }),
  async execute({ path, offset = 1, limit = DEFAULT_READ_LIMIT }, ctx) {
    const file = await readable(path, ctx);
    const lines = (await readFile(file, 'utf8')).split('\n');
    const slice = lines.slice(offset - 1, offset - 1 + limit);
    const body = slice.map((line, i) => `${offset + i}\t${line}`).join('\n');
    const end = offset - 1 + slice.length;
    return end < lines.length || offset > 1 ? `${body}\n[lines ${offset}-${end} of ${lines.length}]` : body;
  },
});

export const writeFileTool = defineTool({
  name: 'write_file',
  description: 'Create or overwrite a file in your workspace (or a project source that allows agents to write). Parent directories are created.',
  input: z.object({ path: z.string(), content: z.string() }),
  async execute({ path, content }, ctx) {
    const file = await writable(path, ctx);
    await mkdir(dirname(file), { recursive: true });
    await writeFile(file, content);
    return `Wrote ${Buffer.byteLength(content)} bytes to ${file}`;
  },
});

export const editFileTool = defineTool({
  name: 'edit_file',
  description:
    'Replace an exact string in a workspace file. old_string must match exactly once unless replace_all is true. Include surrounding context to make it unique.',
  input: z.object({
    path: z.string(),
    old_string: z.string().min(1),
    new_string: z.string(),
    replace_all: z.boolean().optional(),
  }),
  async execute({ path, old_string, new_string, replace_all = false }, ctx) {
    const file = await writable(path, ctx);
    const original = await readFile(file, 'utf8');
    const count = original.split(old_string).length - 1;
    if (count === 0) throw new Error(`old_string not found in ${file}`);
    if (count > 1 && !replace_all) {
      throw new Error(`old_string matches ${count} times in ${file}; include more context or set replace_all`);
    }
    const updated = replace_all ? original.split(old_string).join(new_string) : original.replace(old_string, () => new_string);
    await writeFile(file, updated);
    const n = replace_all ? count : 1;
    return `Edited ${file} (${n} replacement${n === 1 ? '' : 's'})`;
  },
});

export const listDirTool = defineTool({
  name: 'list_dir',
  description: 'List a directory. Directories end with "/".',
  input: z.object({ path: z.string().default('.') }),
  async execute({ path }, ctx) {
    const dir = await readable(path, ctx);
    const entries = await readdir(dir, { withFileTypes: true });
    const names = entries.map((e) => (e.isDirectory() ? `${e.name}/` : e.name)).sort();
    return names.length ? names.join('\n') : '(empty directory)';
  },
});

export const globTool = defineTool({
  name: 'glob',
  description: 'Find files by glob pattern (e.g. "**/*.ts"). Paths are returned relative to root. Skips node_modules and .git.',
  input: z.object({ pattern: z.string(), root: z.string().default('.') }),
  async execute({ pattern, root }, ctx) {
    const dir = await readable(root, ctx);
    const files = (await fg(pattern, { cwd: dir, onlyFiles: true, dot: false, ignore: IGNORE })).sort();
    if (!files.length) return 'No files matched';
    const shown = files.slice(0, MAX_LIST_RESULTS);
    const more = files.length - shown.length;
    return shown.join('\n') + (more ? `\n[${more} more not shown]` : '');
  },
});

export async function grepFallback(pattern: string, dir: string, glob?: string): Promise<string> {
  const re = new RegExp(pattern);
  const files = (await fg(glob ? `**/${glob}` : '**/*', { cwd: dir, onlyFiles: true, ignore: IGNORE })).sort();
  const out: string[] = [];
  for (const f of files) {
    if (out.length >= MAX_LIST_RESULTS) break;
    let text: string;
    try {
      text = await readFile(join(dir, f), 'utf8');
    } catch {
      continue;
    }
    text.split('\n').forEach((line, i) => {
      if (out.length < MAX_LIST_RESULTS && re.test(line)) out.push(`${f}:${i + 1}:${line}`);
    });
  }
  return out.length ? out.join('\n') : 'No matches';
}

export const grepTool = defineTool({
  name: 'grep',
  description: 'Search file contents with a regular expression. Output: path:line:text, paths relative to root.',
  input: z.object({
    pattern: z.string().describe('Regular expression'),
    root: z.string().default('.'),
    glob: z.string().optional().describe('Only search files whose name matches this glob, e.g. "*.ts"'),
  }),
  async execute({ pattern, root, glob }, ctx) {
    const dir = await readable(root, ctx);
    try {
      const r = await runProcess({
        command: 'rg',
        args: ['-n', '--no-heading', '--color', 'never', '--max-count', '50', ...(glob ? ['--glob', glob] : []), '-e', pattern, '.'],
        cwd: dir,
        timeoutMs: 30_000,
        signal: ctx.signal,
      });
      if (r.exitCode === 1) return 'No matches';
      if (r.exitCode !== 0) throw new Error(`grep failed: ${r.output.trim()}`);
      return r.output.replace(/^\.\//gm, '').trimEnd().split('\n').sort().join('\n');
    } catch (err) {
      if ((err as NodeJS.ErrnoException).code === 'ENOENT') return grepFallback(pattern, dir, glob);
      throw err;
    }
  },
});

export const fileTools: Tool[] = [readFileTool, writeFileTool, editFileTool, listDirTool, globTool, grepTool];
