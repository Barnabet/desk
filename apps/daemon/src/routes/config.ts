import { Hono } from 'hono';
import { DaemonConfigPatch, ModelEndpointPutRequest, ModelEndpointTestRequest, WebKeyPutRequest, WebKeyTestRequest, WebProvider } from '@desk/protocol';
import { ValidationError } from '@desk/core';
import type { AppDeps } from '../app';
import { body, HttpError } from '../http';

/** Daemon settings and model-endpoint setup. Bodies here are never logged; the key is never echoed. */
export function configRoutes({ config, endpoint, webKeys }: AppDeps): Hono {
  const r = new Hono();

  r.get('/config', (c) => {
    if (!config) throw new HttpError(501, 'unsupported', 'Daemon config is not available');
    return c.json(config.get());
  });

  r.patch('/config', async (c) => {
    if (!config) throw new HttpError(501, 'unsupported', 'Daemon config is not available');
    return c.json(config.patch(await body(c, DaemonConfigPatch)));
  });

  r.get('/config/model-endpoint', (c) => {
    if (!endpoint) throw new HttpError(501, 'unsupported', 'Model endpoint setup is not available');
    return c.json(endpoint.status());
  });

  r.put('/config/model-endpoint', async (c) => {
    if (!endpoint) throw new HttpError(501, 'unsupported', 'Model endpoint setup is not available');
    return c.json(endpoint.save(await body(c, ModelEndpointPutRequest)));
  });

  r.post('/config/model-endpoint/test', async (c) => {
    if (!endpoint) throw new HttpError(501, 'unsupported', 'Model endpoint setup is not available');
    const raw = await c.req.text();
    let parsed: unknown;
    if (raw.trim()) {
      try {
        parsed = JSON.parse(raw);
      } catch {
        throw new ValidationError('Request body must be JSON');
      }
    }
    const req = parsed === undefined ? undefined : ModelEndpointTestRequest.parse(parsed);
    return c.json(await endpoint.test(req));
  });

  const keys = () => {
    if (!webKeys) throw new HttpError(501, 'unsupported', 'Web key setup is not available');
    return webKeys;
  };
  const provider = (raw: string) => {
    const p = WebProvider.safeParse(raw);
    if (!p.success) throw new HttpError(404, 'not_found', `No web provider named ${raw}`);
    return p.data;
  };

  r.get('/config/web-keys', (c) => c.json(keys().status()));

  r.put('/config/web-keys/:provider', async (c) => {
    const p = provider(c.req.param('provider'));
    const { key } = await body(c, WebKeyPutRequest);
    return c.json(keys().save(p, key));
  });

  r.delete('/config/web-keys/:provider', (c) => c.json(keys().remove(provider(c.req.param('provider')))));

  r.post('/config/web-keys/:provider/test', async (c) => {
    const p = provider(c.req.param('provider'));
    const raw = await c.req.text();
    let parsed: unknown;
    if (raw.trim()) {
      try {
        parsed = JSON.parse(raw);
      } catch {
        throw new ValidationError('Request body must be JSON');
      }
    }
    const req = parsed === undefined ? undefined : WebKeyTestRequest.parse(parsed);
    return c.json(await keys().test(p, req?.key));
  });

  return r;
}
