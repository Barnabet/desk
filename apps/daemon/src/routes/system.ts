import { Hono } from 'hono';
import { EventBody, ModelsPutRequest, type EventType } from '@desk/protocol';
import { getUsageTotals, ValidationError } from '@desk/core';
import type { AppDeps } from '../app';
import { body, intQuery } from '../http';
import { requireProject } from './projects';

const EVENT_TYPES = new Set<string>(EventBody.options.map((o) => o.shape.type.value));

export function systemRoutes({ store, models, saveModels }: AppDeps): Hono {
  const r = new Hono();
  const db = store.db;

  r.get('/projects/:id/usage', (c) => {
    requireProject(db, c.req.param('id'));
    const rows = getUsageTotals(db, c.req.param('id'));
    const totals = rows.reduce((t, r) => ({ prompt_tokens: t.prompt_tokens + r.prompt_tokens, completion_tokens: t.completion_tokens + r.completion_tokens }), {
      prompt_tokens: 0,
      completion_tokens: 0,
    });
    return c.json({ rows, totals });
  });

  r.get('/projects/:id/events', (c) => {
    const id = c.req.param('id');
    requireProject(db, id);
    const after = intQuery(c, 'after');
    const limit = Math.min(intQuery(c, 'limit') ?? 500, 5000);
    const typesParam = c.req.query('types');
    const types = typesParam ? typesParam.split(',') : undefined;
    if (types?.some((t) => !EVENT_TYPES.has(t))) throw new ValidationError(`Unknown event type in: ${typesParam}`);
    const events = store.list({ projectId: id, limit, ...(after !== undefined ? { after } : {}), ...(types ? { types: types as EventType[] } : {}) });
    return c.json({ events, next_after: events.at(-1)?.id ?? after ?? 0 });
  });

  r.get('/models', (c) => c.json(models.list()));

  r.put('/models', async (c) => {
    const list = await body(c, ModelsPutRequest);
    const ids = new Set(list.map((m) => m.id));
    if (ids.size !== list.length) throw new ValidationError('Model ids must be unique');
    models.replaceAll(list);
    saveModels?.(list);
    return c.json(models.list());
  });

  return r;
}
