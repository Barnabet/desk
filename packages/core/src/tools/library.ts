import { readFile, stat } from 'node:fs/promises';
import { z } from 'zod';
import { ArtifactKind } from '@desk/protocol';
import { formatArtifactLine, listArtifacts } from '../library/library';
import { resolveInside } from './paths';
import { defineTool } from './types';

const MAX_READ_CHARS = 200_000;

export const libraryListTool = defineTool({
  name: 'library_list',
  description: 'List the project library: files added by the user and artifacts published by threads.',
  input: z.object({}),
  async execute(_input, ctx) {
    const items = listArtifacts(ctx.services.store.db, ctx.projectId);
    return items.length ? items.map(formatArtifactLine).join('\n') : 'The library is empty';
  },
});

export const libraryReadTool = defineTool({
  name: 'library_read',
  description: 'Read a text file from the project library by its library path (as shown by library_list).',
  input: z.object({ path: z.string().min(1) }),
  async execute({ path }, ctx) {
    const dir = ctx.services.libraryDir(ctx.projectId);
    const file = await resolveInside(path, [dir], dir);
    const text = await readFile(file, 'utf8');
    return text.length > MAX_READ_CHARS ? `${text.slice(0, MAX_READ_CHARS)}\n[truncated]` : text;
  },
});

export const libraryPublishTool = defineTool({
  name: 'library_publish',
  description:
    'Publish a file from your workspace to the shared project library so the user, Desk and other threads can use it. Returns its library path.',
  input: z.object({
    path: z.string().min(1).describe('File in your workspace'),
    title: z.string().min(1),
    kind: ArtifactKind,
    description: z.string(),
    name: z.string().optional().describe('Library file name (defaults to the file name)'),
  }),
  async execute({ path, title, kind, description, name }, ctx) {
    const file = await resolveInside(path, [ctx.workspace], ctx.workspace);
    if (!(await stat(file)).isFile()) throw new Error(`Not a file: ${path}`);
    const published = await ctx.services.publishToLibrary(
      ctx.projectId,
      file,
      { title, kind, description, ...(name ? { name } : {}) },
      `agent:${ctx.agentId}`,
    );
    return `Published to library as ${published.path} (${published.id})`;
  },
});

export const libraryTools = [libraryListTool, libraryReadTool, libraryPublishTool];
