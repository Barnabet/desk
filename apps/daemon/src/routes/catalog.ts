import { Hono } from 'hono';
import { CatalogInstallRequest } from '@desk/protocol';
import type { AppDeps } from '../app';
import { body, HttpError } from '../http';

/** The skill catalog: pinned, reviewed skills the user installs (spec: 2026-09-24-skill-catalog-design). */
export function catalogRoutes({ catalog }: AppDeps): Hono {
  const r = new Hono();
  const need = () => {
    if (!catalog) throw new HttpError(501, 'unsupported', 'The skill catalog is not available in this daemon');
    return catalog;
  };
  r.get('/catalog', (c) => c.json(need().list()));
  r.post('/catalog/:id/prepare', async (c) => c.json(await need().prepare(c.req.param('id'))));
  r.post('/catalog/:id/install', async (c) => {
    const req = await body(c, CatalogInstallRequest);
    return c.json(await need().install(c.req.param('id'), req), 201);
  });
  return r;
}
