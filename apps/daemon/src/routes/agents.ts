import { Hono } from 'hono';
import { AcceptSubmissionRequest, MessageRequest, RequestReviewRequest, ResolveApprovalRequest, WaiveFindingRequest } from '@desk/protocol';
import { getAgent, listApprovals, listThreads, NotFoundError, threadReview, type AgentRow, type ApprovalStatus, type Db } from '@desk/core';
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

  // Reviews and acceptance (spec 2026-10-02-reviews-and-acceptance-design.md §7).
  r.get('/threads/:id/review', (c) => c.json(threadReview(db, requireThread(db, c.req.param('id')))));

  r.post('/threads/:id/review', async (c) => {
    const t = requireThread(db, c.req.param('id'));
    const req = await body(c, RequestReviewRequest);
    const out = await runtime.requestReview({
      threadId: t.id,
      criteria: req.criteria,
      ...(req.focus?.trim() ? { focus: req.focus.trim() } : {}),
      ...(req.model ? { model: req.model } : {}),
      ...(req.reviewer_id ? { reviewerId: req.reviewer_id } : {}),
      by: 'user',
    });
    return c.json({ reviewer_id: out.reviewerId, review_id: out.reviewId, reopened: out.reopened }, 201);
  });

  r.post('/threads/:id/accept', async (c) => {
    const t = requireThread(db, c.req.param('id'));
    const req = await body(c, AcceptSubmissionRequest);
    const out = runtime.acceptSubmission({
      threadId: t.id,
      decision: req.decision,
      ...(req.limitations ? { limitations: req.limitations } : {}),
      ...(req.waive ? { waive: req.waive.map((w) => ({ findingId: w.finding_id, ...(w.reason ? { reason: w.reason } : {}) })) } : {}),
      ...(req.note ? { note: req.note } : {}),
      by: 'user',
    });
    return c.json({ submission_seq: out.submissionSeq, waived: out.waived });
  });

  r.post('/findings/:id/waive', async (c) => {
    const req = await body(c, WaiveFindingRequest);
    runtime.waiveFinding(c.req.param('id'), req.reason, 'user');
    return c.json({ ok: true });
  });

  r.get('/projects/:id/approvals', (c) => {
    requireProject(db, c.req.param('id'));
    const status = c.req.query('status');
    return c.json(listApprovals(db, c.req.param('id'), status && APPROVAL_STATUSES.has(status) ? (status as ApprovalStatus) : undefined));
  });

  r.post('/approvals/:id/resolve', async (c) => {
    const req = await body(c, ResolveApprovalRequest);
    await runtime.resolveApproval(c.req.param('id'), req.decision, { by: 'user', ...(req.note ? { note: req.note } : {}), ...(req.remember ? { remember: true } : {}) });
    return c.json({ ok: true });
  });

  return r;
}
