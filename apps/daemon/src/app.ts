import { Hono } from 'hono';
import { PROTOCOL_VERSION, type ModelInfo } from '@desk/protocol';
import type { EventStore, ModelRegistry, Runtime } from '@desk/core';
import { bearerAuth, errorResponse } from './http';
import { agentRoutes } from './routes/agents';
import { knowledgeRoutes } from './routes/knowledge';
import { projectRoutes } from './routes/projects';
import { skillRoutes } from './routes/skills';
import { systemRoutes } from './routes/system';
import { uiRoutes } from './routes/ui';

export type AppDeps = {
  runtime: Runtime;
  store: EventStore;
  models: ModelRegistry;
  token: string;
  version: string;
  /** Persists the model registry after PUT /models. */
  saveModels?: (models: ModelInfo[]) => void;
};

export function createApp(deps: AppDeps): Hono {
  const app = new Hono();
  app.onError((err, c) => errorResponse(c, err));
  app.notFound((c) => c.json({ error: { code: 'not_found', message: `No route for ${c.req.method} ${c.req.path}` } }, 404));

  app.get('/v1/health', (c) => c.json({ version: deps.version, protocol_version: PROTOCOL_VERSION }));
  app.use('/v1/*', bearerAuth(deps.token));
  app.route('/v1', projectRoutes(deps));
  app.route('/v1', agentRoutes(deps));
  app.route('/v1', knowledgeRoutes(deps));
  app.route('/v1', uiRoutes(deps));
  app.route('/v1', skillRoutes(deps));
  app.route('/v1', systemRoutes(deps));
  return app;
}
