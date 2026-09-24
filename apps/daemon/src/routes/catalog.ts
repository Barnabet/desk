import { Hono } from 'hono';
import { CatalogInstallRequest } from '@desk/protocol';
import type { AppDeps } from '../app';
import { body, HttpError } from '../http';

/** The skill catalog: pinned, reviewed skills the user installs (spec: 2026-09-24-skill-catalog-design). */
export function catalogRoutes({ catalog, skillRuntimes }: AppDeps): Hono {
  const r = new Hono();
  const need = () => {
    if (!catalog) throw new HttpError(501, 'unsupported', 'The skill catalog is not available in this daemon');
    return catalog;
  };
  r.get('/catalog', (c) => c.json(need().list()));
  r.post('/catalog/:id/prepare', async (c) => c.json(await need().prepare(c.req.param('id'))));
  r.get('/catalog/:id/files/*', async (c) => {
    const marker = `/catalog/${c.req.param('id')}/files/`;
    const rel = decodeURIComponent(c.req.path.slice(c.req.path.indexOf(marker) + marker.length));
    return c.body(new Uint8Array(await need().file(c.req.param('id'), rel)), 200, { 'content-type': 'application/octet-stream' });
  });
  r.post('/catalog/:id/install', async (c) => {
    const req = await body(c, CatalogInstallRequest);
    return c.json(await need().install(c.req.param('id'), req), 201);
  });

  const runtimes = () => {
    if (!skillRuntimes) throw new HttpError(501, 'unsupported', 'Skill runtimes are not available in this daemon');
    return skillRuntimes;
  };
  r.post('/skills/:name/runtime/retry', (c) => c.json(need().retryRuntime({ scope: 'global', name: c.req.param('name') })));
  r.post('/projects/:id/skills/:name/runtime/retry', (c) =>
    c.json(need().retryRuntime({ scope: 'project', name: c.req.param('name'), projectId: c.req.param('id') })),
  );
  r.get('/system/runtimes', (c) => c.json(runtimes().report()));
  r.post('/system/runtimes/cleanup', (c) => c.json(runtimes().cleanup()));
  return r;
}
