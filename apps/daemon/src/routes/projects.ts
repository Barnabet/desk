import { existsSync } from 'node:fs';
import { Hono } from 'hono';
import { AddSourceRequest, CreateProjectRequest, MessageRequest, UpdateProjectRequest, type EventType } from '@desk/protocol';
import {
  getDeskAgent,
  getPlan,
  getProject,
  lastProjectSeq,
  listApprovals,
  listProjects,
  listServices,
  listSources,
  listThreads,
  NotFoundError,
  ValidationError,
  type Db,
} from '@desk/core';
import type { AppDeps } from '../app';
import { body, intQuery } from '../http';

/** Event types that make up the Desk conversation as a client shows it. */
export const CHAT_EVENT_TYPES: EventType[] = [
  'message.user',
  'message.agent',
  'assistant.message',
  'tool.call',
  'tool.result',
  'report',
  'question.asked',
  'agent.status_changed',
  'run.finished',
];

export function requireProject(db: Db, id: string) {
  const p = getProject(db, id);
  if (!p) throw new NotFoundError(`Unknown project: ${id}`);
  return p;
}

export function projectOverview(db: Db, id: string) {
  const project = requireProject(db, id);
  return {
    project,
    desk: getDeskAgent(db, id) ?? null,
    sources: listSources(db, id),
    plan: getPlan(db, id) ?? null,
    threads: listThreads(db, id).filter((t) => !t.archived_at),
    approvals: listApprovals(db, id, 'pending'),
    services: listServices(db, id),
    last_seq: lastProjectSeq(db, id),
  };
}

export function projectRoutes({ runtime, store }: AppDeps): Hono {
  const r = new Hono();
  const db = store.db;

  r.get('/projects', (c) => c.json(listProjects(db, { includeArchived: c.req.query('all') === '1' })));

  r.post('/projects', async (c) => {
    const req = await body(c, CreateProjectRequest);
    for (const s of req.sources ?? []) if (!existsSync(s.path)) throw new ValidationError(`Source path does not exist: ${s.path}`);
    const id = runtime.createProject({
      name: req.name,
      goal: req.goal,
      ...(req.instructions !== undefined ? { instructions: req.instructions } : {}),
      ...(req.settings ? { settings: req.settings } : {}),
    });
    for (const s of req.sources ?? []) await runtime.addSource(id, s.path, s.label);
    return c.json(projectOverview(db, id), 201);
  });

  r.get('/projects/:id', (c) => c.json(projectOverview(db, c.req.param('id'))));

  r.patch('/projects/:id', async (c) => {
    const id = c.req.param('id');
    requireProject(db, id);
    const patch = await body(c, UpdateProjectRequest);
    const { settings, ...fields } = patch;
    if (settings) runtime.updateSettings(id, settings);
    if (Object.keys(fields).length) runtime.updateProject(id, fields);
    return c.json(getProject(db, id));
  });

  r.post('/projects/:id/archive', (c) => {
    runtime.archiveProject(c.req.param('id'));
    return c.json({ ok: true });
  });

  r.post('/projects/:id/sources', async (c) => {
    const id = c.req.param('id');
    const req = await body(c, AddSourceRequest);
    const sourceId = await runtime.addSource(id, req.path, req.label);
    return c.json(
      listSources(db, id).find((s) => s.id === sourceId),
      201,
    );
  });

  r.delete('/projects/:id/sources/:sid', (c) => {
    runtime.removeSource(c.req.param('id'), c.req.param('sid'));
    return c.json({ ok: true });
  });

  r.post('/projects/:id/messages', async (c) => {
    const req = await body(c, MessageRequest);
    runtime.sendToDesk(c.req.param('id'), req.text);
    return c.json({ ok: true }, 202);
  });

  r.get('/projects/:id/chat', (c) => {
    const id = c.req.param('id');
    requireProject(db, id);
    const desk = getDeskAgent(db, id)!;
    const after = intQuery(c, 'after');
    const limit = intQuery(c, 'limit');
    const events = store.list({ agentId: desk.id, types: CHAT_EVENT_TYPES, ...(after !== undefined ? { after } : {}), ...(limit ? { limit } : {}) });
    return c.json({ events, next_after: events.at(-1)?.id ?? after ?? 0 });
  });

  r.get('/projects/:id/plan', (c) => {
    requireProject(db, c.req.param('id'));
    return c.json(getPlan(db, c.req.param('id')) ?? null);
  });

  return r;
}
