import { readFile } from 'node:fs/promises';
import { Hono, type Context } from 'hono';
import {
  getAgent,
  getUsageByProject,
  lastSeq,
  listAttention,
  listOverview,
  listWorkspace,
  NotFoundError,
  resolveWorkspaceFile,
  threadDiff,
  ToolDenied,
  ValidationError,
  type AgentRow,
  type Db,
} from '@desk/core';
import type { AppDeps } from '../app';
import { HttpError } from '../http';
import { requireProject } from './projects';

function requireThread(db: Db, id: string): AgentRow {
  const t = getAgent(db, id);
  if (!t || t.role !== 'thread') throw new NotFoundError(`Unknown thread: ${id}`);
  return t;
}

/** Maps a path escape to 403, like the library route. */
async function confined<T>(fn: () => Promise<T>): Promise<T> {
  try {
    return await fn();
  } catch (err) {
    if (err instanceof ToolDenied) throw new HttpError(403, 'forbidden', 'Path is outside the workspace');
    throw err;
  }
}

/** The path after `marker` in the request URL, decoded. */
function tail(c: Context, marker: string): string {
  const p = c.req.path;
  return decodeURIComponent(p.slice(p.indexOf(marker) + marker.length));
}

function versionParam(c: Context): number {
  const v = Number(c.req.param('v'));
  if (!Number.isInteger(v) || v < 1) throw new ValidationError('Version must be a positive integer');
  return v;
}

/** A fresh headers object per response: @hono/node-server writes each body's Content-Length into the one it is given. */
const octet = () => ({ 'content-type': 'application/octet-stream' });

/** Read endpoints for the desktop app (design spec §4.1–4.2). */
export function uiRoutes({ runtime, store }: AppDeps): Hono {
  const r = new Hono();
  const db = store.db;

  r.get('/attention', (c) => {
    const projectId = c.req.query('project_id');
    const items = listAttention(db, projectId ? { projectId } : {});
    return c.json({ items, seq: lastSeq(db) });
  });

  r.post('/attention/:id/dismiss', (c) => {
    runtime.dismissAttention(c.req.param('id'));
    return c.json({ ok: true });
  });

  r.get('/overview', (c) => c.json(listOverview(db)));

  r.get('/threads/:id/diff', async (c) => c.json(await threadDiff(requireThread(db, c.req.param('id')))));

  r.get('/threads/:id/files', async (c) => {
    const t = requireThread(db, c.req.param('id'));
    return c.json(await confined(() => listWorkspace(t, c.req.query('path') ?? '')));
  });

  r.get('/threads/:id/files/raw/*', async (c) => {
    const t = requireThread(db, c.req.param('id'));
    const file = await confined(() => resolveWorkspaceFile(t, tail(c, '/files/raw/')));
    return c.body(new Uint8Array(await readFile(file)), 200, octet());
  });

  const skillVersions = (prefix: string, project: boolean) => {
    const opts = (c: Context) => (project ? { projectId: requireProject(db, c.req.param('id') ?? '').id } : { scope: 'global' as const });
    r.get(`${prefix}/:name/versions/:v`, (c) => c.json(runtime.getSkillVersion(c.req.param('name'), versionParam(c), opts(c))));
    r.get(`${prefix}/:name/versions/:v/files/*`, async (c) => {
      const skill = runtime.getSkillVersion(c.req.param('name'), versionParam(c), opts(c));
      const file = runtime.skills.filePath(skill, tail(c, `/versions/${c.req.param('v')}/files/`));
      return c.body(new Uint8Array(await readFile(file)), 200, octet());
    });
  };
  skillVersions('/skills', false);
  skillVersions('/projects/:id/skills', true);

  r.get('/usage', (c) => {
    const since = c.req.query('since');
    if (since !== undefined && !/^\d{4}-\d{2}-\d{2}/.test(since)) throw new ValidationError('since must be an ISO date (YYYY-MM-DD…)');
    const rows = getUsageByProject(db, since?.slice(0, 10));
    const totals = rows.reduce((t, x) => ({ prompt_tokens: t.prompt_tokens + x.prompt_tokens, completion_tokens: t.completion_tokens + x.completion_tokens }), { prompt_tokens: 0, completion_tokens: 0 });
    return c.json({ rows, totals });
  });

  return r;
}
