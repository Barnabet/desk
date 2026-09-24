import { Hono } from 'hono';
import {
  PROTOCOL_VERSION,
  type DaemonConfig,
  type DaemonConfigPatch,
  type ModelEndpointStatus,
  type ModelEndpointTestResult,
  type ModelInfo,
} from '@desk/protocol';
import type { CatalogService, EventStore, ModelRegistry, Runtime, SkillRuntimes } from '@desk/core';
import { bearerAuth, errorResponse } from './http';
import { agentRoutes } from './routes/agents';
import { catalogRoutes } from './routes/catalog';
import { configRoutes } from './routes/config';
import { knowledgeRoutes } from './routes/knowledge';
import { projectRoutes } from './routes/projects';
import { skillRoutes } from './routes/skills';
import { systemRoutes } from './routes/system';
import { uiRoutes } from './routes/ui';

export type ConfigDeps = { get(): DaemonConfig; patch(p: DaemonConfigPatch): DaemonConfig };
export type EndpointDeps = {
  status(): ModelEndpointStatus;
  save(req: { base_url: string; api_key: string }): ModelEndpointStatus;
  test(req?: { base_url: string; api_key: string }): Promise<ModelEndpointTestResult>;
};

export type AppDeps = {
  runtime: Runtime;
  store: EventStore;
  models: ModelRegistry;
  /** The skill catalog; routes answer 501 without it. */
  catalog?: CatalogService;
  /** Desk-managed skill runtimes (retry, sizes, cleanup). */
  skillRuntimes?: SkillRuntimes;
  token: string;
  version: string;
  /** The bundle's build id, or null when running from source. */
  build?: string | null;
  /** Persists the model registry after PUT /models. */
  saveModels?: (models: ModelInfo[]) => void;
  /** Extra health fields (proxy state, uptime). */
  health?: () => { proxy: 'up' | 'down' | 'unknown'; uptime_s: number };
  /** Daemon settings (config.json). */
  config?: ConfigDeps;
  /** Model endpoint setup; the key is write-only. */
  endpoint?: EndpointDeps;
};

export function createApp(deps: AppDeps): Hono {
  const app = new Hono();
  app.onError((err, c) => errorResponse(c, err));
  app.notFound((c) => c.json({ error: { code: 'not_found', message: `No route for ${c.req.method} ${c.req.path}` } }, 404));

  app.get('/v1/health', (c) => c.json({ version: deps.version, protocol_version: PROTOCOL_VERSION, build: deps.build ?? null, ...(deps.health?.() ?? {}) }));
  app.use('/v1/*', bearerAuth(deps.token));
  app.route('/v1', projectRoutes(deps));
  app.route('/v1', agentRoutes(deps));
  app.route('/v1', knowledgeRoutes(deps));
  app.route('/v1', uiRoutes(deps));
  app.route('/v1', skillRoutes(deps));
  app.route('/v1', catalogRoutes(deps));
  app.route('/v1', systemRoutes(deps));
  app.route('/v1', configRoutes(deps));
  return app;
}
