import { Hono } from 'hono';
import { getService, listServices, NotFoundError, type Db, type ServiceRow } from '@desk/core';
import type { AppDeps } from '../app';
import { intQuery } from '../http';
import { requireProject } from './projects';

function requireService(db: Db, id: string): ServiceRow {
  const s = getService(db, id);
  if (!s) throw new NotFoundError(`Unknown service: ${id}`);
  return s;
}

/** Project services: list, log tail, and the user's start/stop/restart. */
export function serviceRoutes({ runtime, store }: AppDeps): Hono {
  const r = new Hono();
  const db = store.db;

  r.get('/projects/:id/services', (c) => {
    requireProject(db, c.req.param('id'));
    return c.json(listServices(db, c.req.param('id')));
  });

  r.get('/services/:id/logs', (c) => {
    const s = requireService(db, c.req.param('id'));
    return c.json(runtime.serviceLogs(s.id, intQuery(c, 'lines') ?? 200));
  });

  r.post('/services/:id/start', async (c) => c.json(await runtime.restartService(requireService(db, c.req.param('id')).id, 'user')));
  r.post('/services/:id/restart', async (c) => c.json(await runtime.restartService(requireService(db, c.req.param('id')).id, 'user')));
  r.post('/services/:id/stop', async (c) => c.json(await runtime.stopService(requireService(db, c.req.param('id')).id, 'user')));
  return r;
}
