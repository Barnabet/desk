import { readFile } from 'node:fs/promises';
import { extname } from 'node:path';
import { Hono } from 'hono';
import { LibraryUploadRequest, MemoryUpdateRequest, MemoryWriteRequest } from '@desk/protocol';
import { activeMemory, getMemory, listArtifacts, NotFoundError, resolveInside, searchMemory, ToolDenied } from '@desk/core';
import type { AppDeps } from '../app';
import { body, HttpError } from '../http';
import { requireProject } from './projects';

const MIME: Record<string, string> = {
  '.md': 'text/markdown; charset=utf-8',
  '.txt': 'text/plain; charset=utf-8',
  '.json': 'application/json',
  '.html': 'text/html; charset=utf-8',
  '.csv': 'text/csv; charset=utf-8',
  '.pdf': 'application/pdf',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.svg': 'image/svg+xml',
};

export function knowledgeRoutes({ runtime, store }: AppDeps): Hono {
  const r = new Hono();
  const db = store.db;

  r.get('/projects/:id/memory', (c) => {
    const id = c.req.param('id');
    requireProject(db, id);
    const q = c.req.query('q');
    return c.json(q ? searchMemory(db, id, q) : activeMemory(db, id));
  });

  r.post('/projects/:id/memory', async (c) => {
    const req = await body(c, MemoryWriteRequest);
    const id = runtime.writeMemory(c.req.param('id'), { kind: req.kind, content: req.content, ...(req.supersedes ? { supersedes: req.supersedes } : {}) });
    return c.json(getMemory(db, id), 201);
  });

  r.patch('/projects/:id/memory/:mid', async (c) => {
    const projectId = c.req.param('id');
    const old = getMemory(db, c.req.param('mid'));
    if (!old || old.project_id !== projectId) throw new NotFoundError(`Unknown memory: ${c.req.param('mid')}`);
    const req = await body(c, MemoryUpdateRequest);
    const id = runtime.writeMemory(projectId, { kind: req.kind ?? old.kind, content: req.content, supersedes: old.id });
    return c.json(getMemory(db, id));
  });

  r.delete('/projects/:id/memory/:mid', (c) => {
    runtime.deleteMemory(c.req.param('id'), c.req.param('mid'));
    return c.json({ ok: true });
  });

  r.get('/projects/:id/library', (c) => {
    requireProject(db, c.req.param('id'));
    return c.json(listArtifacts(db, c.req.param('id')));
  });

  r.post('/projects/:id/library', async (c) => {
    const id = c.req.param('id');
    requireProject(db, id);
    const req = await body(c, LibraryUploadRequest);
    const created = runtime.addLibraryFile(id, {
      name: req.name,
      content: Buffer.from(req.content_base64, 'base64'),
      ...(req.title ? { title: req.title } : {}),
      ...(req.kind ? { kind: req.kind } : {}),
      ...(req.description ? { description: req.description } : {}),
    });
    return c.json(listArtifacts(db, id).find((a) => a.id === created.id), 201);
  });

  r.get('/projects/:id/library/file/:path{.+}', async (c) => {
    const id = c.req.param('id');
    requireProject(db, id);
    const dir = runtime.libraryDir(id);
    let file: string;
    try {
      file = await resolveInside(decodeURIComponent(c.req.param('path')), [dir], dir);
    } catch (err) {
      if (err instanceof ToolDenied) throw new HttpError(403, 'forbidden', 'Path is outside the library');
      throw err;
    }
    const data = await readFile(file).catch(() => {
      throw new NotFoundError(`No such library file: ${c.req.param('path')}`);
    });
    return c.body(new Uint8Array(data), 200, { 'content-type': MIME[extname(file).toLowerCase()] ?? 'application/octet-stream' });
  });

  return r;
}
