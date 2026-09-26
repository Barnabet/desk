import { readFile } from 'node:fs/promises';
import { Hono } from 'hono';
import { BuiltinDuplicateRequest, BuiltinToggleRequest } from '@desk/protocol';
import type { AppDeps } from '../app';
import { body } from '../http';

/** Desk's own skills (spec 2026-09-26-builtin-skills-design §3): read-only, switchable, duplicable. */
export function builtinRoutes({ runtime }: AppDeps): Hono {
  const r = new Hono();
  r.get('/builtin-skills', (c) => c.json(runtime.listBuiltins(c.req.query('project_id') || undefined)));
  r.get('/builtin-skills/:name', (c) => c.json(runtime.getBuiltin(c.req.param('name'))));
  r.get('/builtin-skills/:name/files/*', async (c) => {
    const skill = runtime.getBuiltin(c.req.param('name'));
    const marker = `/builtin-skills/${c.req.param('name')}/files/`;
    const rel = decodeURIComponent(c.req.path.slice(c.req.path.indexOf(marker) + marker.length));
    return c.body(new Uint8Array(await readFile(runtime.skills.filePath(skill, rel))), 200, { 'content-type': 'application/octet-stream' });
  });
  r.put('/builtin-skills/:name', async (c) => {
    const req = await body(c, BuiltinToggleRequest);
    return c.json(runtime.setBuiltinEnabled(c.req.param('name'), req.enabled));
  });
  r.post('/builtin-skills/:name/duplicate', async (c) => {
    const req = await body(c, BuiltinDuplicateRequest);
    const out = runtime.duplicateBuiltin(c.req.param('name'), { scope: req.scope, ...(req.project_id ? { projectId: req.project_id } : {}) });
    return c.json(out, 201);
  });
  r.post('/builtin-skills/:name/runtime/retry', (c) => c.json(runtime.retryBuiltinRuntime(c.req.param('name'))));
  return r;
}
