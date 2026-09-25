import { Hono } from 'hono';
import { MessageRequest, ResolveApprovalRequest } from '@desk/protocol';
import { getAgent, listApprovals, listThreads, NotFoundError, type AgentRow, type ApprovalStatus, type Db } from '@desk/core';
import type { AppDeps } from '../app';
import { body, intQuery } from '../http';
import { requireProject } from './projects';

function requireThread(db: Db, id: string): AgentRow {
  const t = getAgent(db, id);
  if (!t || t.role !== 'thread') throw new NotFoundError(`Unknown thread: ${id}`);
  return t;
}

const APPROVAL_STATUSES = new Set(['pending', 'approved', 'denied']);

export function agentRoutes({ runtime, store }: AppDeps): Hono {
  const r = new Hono();
  const db = store.db;

  r.get('/projects/:id/threads', (c) => {
    requireProject(db, c.req.param('id'));
    const all = c.req.query('all') === '1';
    return c.json(listThreads(db, c.req.param('id')).filter((t) => all || !t.archived_at));
  });

  r.get('/threads/:id', (c) => c.json(requireThread(db, c.req.param('id'))));

  r.get('/threads/:id/transcript', (c) => {
    const t = requireThread(db, c.req.param('id'));
    const after = intQuery(c, 'after');
    const limit = intQuery(c, 'limit');
    const events = store.list({ agentId: t.id, ...(after !== undefined ? { after } : {}), ...(limit ? { limit } : {}) });
    return c.json({ events, next_after: events.at(-1)?.id ?? after ?? 0 });
  });

  r.post('/threads/:id/messages', async (c) => {
    const t = requireThread(db, c.req.param('id'));
    const req = await body(c, MessageRequest);
    runtime.sendMessage(t.id, req.text, { question: req.question ?? false });
    return c.json({ ok: true }, 202);
  });

  r.post('/threads/:id/stop', (c) => {
    const t = requireThread(db, c.req.param('id'));
    runtime.stop(t.id);
    return c.json({ ok: true });
  });

  r.post('/threads/:id/archive', async (c) => {
    const t = requireThread(db, c.req.param('id'));
    await runtime.archiveThread(t.id);
    return c.json({ ok: true });
  });

  r.get('/projects/:id/approvals', (c) => {
    requireProject(db, c.req.param('id'));
    const status = c.req.query('status');
    return c.json(listApprovals(db, c.req.param('id'), status && APPROVAL_STATUSES.has(status) ? (status as ApprovalStatus) : undefined));
  });

  r.post('/approvals/:id/resolve', async (c) => {
    const req = await body(c, ResolveApprovalRequest);
    await runtime.resolveApproval(c.req.param('id'), req.decision, { by: 'user', ...(req.note ? { note: req.note } : {}) });
    return c.json({ ok: true });
  });

  return r;
}
