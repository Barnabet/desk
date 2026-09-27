// apps/daemon/src/routes/automations.ts
import { existsSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import { Hono, type Context } from 'hono';
import {
  AutomationAnswerRequest,
  AutomationCreateRequest,
  AutomationEnabledRequest,
  AutomationGrantsRequest,
  AutomationImportRequest,
  AutomationLayoutRequest,
  AutomationRunRequest,
  AutomationSaveRequest,
  AutomationValidateRequest,
  type RunListEntry,
} from '@desk/protocol';
import {
  automationDetail,
  automationSummary,
  effectiveRunStatus,
  getRun,
  getStepRun,
  listFolder,
  listRuns,
  listTriggerSkips,
  logFile,
  NotFoundError,
  readAgentFile,
  resolveFolderFile,
  runDetail,
  runDir,
  runInfo,
  toolByName,
  ToolDenied,
  ValidationError,
  type AutomationRow,
  type AutomationRunRow,
} from '@desk/core';
import type { AppDeps } from '../app';
import { body, HttpError, intQuery } from '../http';
import { requireProject } from './projects';

/** Maps a path escape (or a refused file) to 403, like the thread and library routes. */
async function confined<T>(fn: () => Promise<T>): Promise<T> {
  try {
    return await fn();
  } catch (err) {
    if (err instanceof ToolDenied) throw new HttpError(403, 'forbidden', 'Path is outside the run folder');
    throw err;
  }
}

function tail(c: Context, marker: string): string {
  const p = c.req.path;
  return decodeURIComponent(p.slice(p.indexOf(marker) + marker.length));
}

function versionParam(c: Context): number {
  const v = Number(c.req.param('v'));
  if (!Number.isInteger(v) || v < 1) throw new ValidationError('Version must be a positive integer');
  return v;
}

const octet = () => ({ 'content-type': 'application/octet-stream' });

/** Automations, their versions and runs (automations spec §7.1). Every change made here is the user's. */
export function automationRoutes({ runtime, store }: AppDeps): Hono {
  const r = new Hono();
  const db = store.db;
  const { automations, engine } = runtime;
  const now = () => engine.now();
  const detail = (a: AutomationRow) => automationDetail(db, automations.require(a.id), now(), toolByName);
  const requireRun = (id: string): AutomationRunRow => {
    const run = getRun(db, id);
    if (!run) throw new NotFoundError(`Unknown run: ${id}`);
    return run;
  };

  r.get('/projects/:id/automations', (c) => {
    const p = requireProject(db, c.req.param('id'));
    return c.json(automations.list(p.id).map((a) => automationSummary(db, a, now())));
  });

  r.post('/projects/:id/automations', async (c) => {
    const p = requireProject(db, c.req.param('id'));
    const req = await body(c, AutomationCreateRequest);
    const { automation, warnings } = automations.create(p.id, req.name, req.definition, { origin: 'user', via: req.via, ...(req.change_note ? { changeNote: req.change_note } : {}) });
    return c.json({ automation: detail(automation), warnings }, 201);
  });

  r.post('/projects/:id/automations/validate', async (c) => {
    const p = requireProject(db, c.req.param('id'));
    const req = await body(c, AutomationValidateRequest);
    return c.json(automations.validate(p.id, req.definition, req.name));
  });

  r.post('/projects/:id/automations/import', async (c) => {
    const p = requireProject(db, c.req.param('id'));
    const req = await body(c, AutomationImportRequest);
    const { automation, warnings } = automations.importInto(p.id, req, 'user');
    return c.json({ automation: detail(automation), warnings }, 201);
  });

  r.get('/automations/:aid', (c) => c.json(detail(automations.require(c.req.param('aid')))));

  r.put('/automations/:aid', async (c) => {
    const req = await body(c, AutomationSaveRequest);
    const { automation, warnings } = automations.save(c.req.param('aid'), req.definition, {
      origin: 'user',
      via: req.via,
      baseVersion: req.base_version,
      ...(req.change_note ? { changeNote: req.change_note } : {}),
    });
    return c.json({ automation: detail(automation), warnings });
  });

  r.delete('/automations/:aid', async (c) => {
    await automations.delete(c.req.param('aid'), 'user');
    return c.json({ ok: true });
  });

  r.put('/automations/:aid/layout', async (c) => {
    const req = await body(c, AutomationLayoutRequest);
    automations.setLayout(c.req.param('aid'), req.layout);
    return c.json({ ok: true });
  });

  r.get('/automations/:aid/versions', (c) => c.json(automations.versions(c.req.param('aid'))));

  r.get('/automations/:aid/versions/:v', (c) => {
    const v = automations.version(c.req.param('aid'), versionParam(c));
    return c.json({ version: v.version, definition: v.definition, origin: v.origin, change_note: v.change_note, via: v.via, created_at: v.created_at });
  });

  r.post('/automations/:aid/versions/:v/restore', (c) => {
    const { automation } = automations.restore(c.req.param('aid'), versionParam(c), 'user');
    return c.json(detail(automation));
  });

  r.put('/automations/:aid/enabled', async (c) => {
    const req = await body(c, AutomationEnabledRequest);
    return c.json(detail(automations.setEnabled(c.req.param('aid'), req.enabled, 'user')));
  });

  r.put('/automations/:aid/grants', async (c) => {
    const req = await body(c, AutomationGrantsRequest);
    automations.setGrants(c.req.param('aid'), req.grants, req.reason);
    return c.json(detail(automations.require(c.req.param('aid'))));
  });

  r.post('/automations/:aid/grants/keep', (c) => {
    automations.keepGrants(c.req.param('aid'));
    return c.json(detail(automations.require(c.req.param('aid'))));
  });

  r.get('/automations/:aid/export', (c) => c.json(automations.exportOf(c.req.param('aid'))));

  r.post('/automations/:aid/runs', async (c) => {
    const a = automations.require(c.req.param('aid'));
    const req = await body(c, AutomationRunRequest);
    const runId = await engine.startRun(a.id, { trigger: req.test ? 'test' : 'manual', test: req.test, inputs: req.inputs, by: 'user' });
    return c.json({ run_id: runId }, 202);
  });

  r.get('/automations/:aid/runs', (c) => {
    const a = automations.require(c.req.param('aid'));
    const before = c.req.query('before');
    const limit = Math.min(intQuery(c, 'limit') ?? 50, 200);
    // Each run with its derived status: `waiting` while it waits on the user (spec §2.2).
    const runs: Array<{ at: string; entry: RunListEntry }> = listRuns(db, a.id, { ...(before ? { before } : {}), limit }).map((run) => ({
      at: run.started_at,
      entry: { kind: 'run', run: { ...runInfo(db, run), status: effectiveRunStatus(db, run) } },
    }));
    const skips = listTriggerSkips(db, a.project_id, a.id)
      .filter((s) => !before || s.ts < before)
      .map((s) => ({ at: s.ts, entry: { kind: 'skipped' as const, automation_id: a.id, trigger_index: s.trigger_index, due_at: s.due_at, reason: s.reason, ts: s.ts } }));
    return c.json(
      [...runs, ...skips]
        .sort((x, y) => y.at.localeCompare(x.at))
        .slice(0, limit)
        .map((x) => x.entry),
    );
  });

  r.get('/automation-runs/:rid', (c) => c.json(runDetail(db, requireRun(c.req.param('rid')).id)));

  r.post('/automation-runs/:rid/cancel', async (c) => {
    await engine.cancelRun(requireRun(c.req.param('rid')).id, 'Cancelled by the user');
    return c.json({ ok: true });
  });

  r.post('/automation-runs/:rid/steps/:sid/answer', async (c) => {
    const req = await body(c, AutomationAnswerRequest);
    await engine.answer(requireRun(c.req.param('rid')).id, c.req.param('sid'), {
      decision: req.decision,
      ...(req.note ? { note: req.note } : {}),
      ...(req.remember ? { remember: true } : {}),
    });
    return c.json({ ok: true });
  });

  r.post('/automation-runs/:rid/steps/:sid/stop', (c) => {
    engine.stopStep(requireRun(c.req.param('rid')).id, c.req.param('sid'));
    return c.json({ ok: true });
  });

  r.get('/automation-runs/:rid/steps/:sid/log', async (c) => {
    const run = requireRun(c.req.param('rid'));
    const file = logFile(runtime.dataDir, run.id, c.req.param('sid'));
    if (!existsSync(file)) throw new NotFoundError(`No log for step ${c.req.param('sid')}`);
    // `<run>/logs/` is deskd's own (Task 11): a plain read.
    return c.text(await readFile(file, 'utf8'));
  });

  r.get('/automation-runs/:rid/steps/:sid/transcript', (c) => {
    const run = requireRun(c.req.param('rid'));
    const row = getStepRun(db, run.id, c.req.param('sid'));
    if (!row?.agent_id) throw new NotFoundError(`Step ${c.req.param('sid')} of run ${run.id} has no agent`);
    const after = intQuery(c, 'after');
    const limit = intQuery(c, 'limit');
    const events = store.list({ agentId: row.agent_id, ...(after !== undefined ? { after } : {}), ...(limit ? { limit } : {}) });
    return c.json({ events, next_after: events.at(-1)?.id ?? after ?? 0 });
  });

  r.get('/automation-runs/:rid/files', async (c) => {
    const run = requireRun(c.req.param('rid'));
    // A step's .desk/ holds DESK_OUTPUT for deskd (its outputs show in the run detail), as publishing and attachments skip it.
    const entries = await confined(() => listFolder(runDir(runtime.dataDir, run.id), c.req.query('path') ?? ''));
    return c.json(entries.filter((e) => e.name !== '.desk'));
  });

  r.get('/automation-runs/:rid/files/raw/*', async (c) => {
    const run = requireRun(c.req.param('rid'));
    // Step folders are scripts' and agents' own: no final symlink, a regular file, never a secret.
    const data = await confined(async () => readAgentFile(await resolveFolderFile(runDir(runtime.dataDir, run.id), tail(c, '/files/raw/')), runtime.guard));
    return c.body(new Uint8Array(data), 200, octet());
  });

  return r;
}
