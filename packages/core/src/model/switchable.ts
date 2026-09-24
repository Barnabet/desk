import { createModelAdapter } from './adapter';
import type { ModelConfig } from './config';
import { ModelError } from './errors';
import type { ModelRegistry } from './registry';
import type { ModelAdapter } from './types';

export type SwitchableAdapter = ModelAdapter & { replace(config: ModelConfig | null): void; readonly configured: boolean };

/**
 * A model adapter whose endpoint can be set or changed while the daemon runs.
 * Unconfigured, it behaves like an unreachable proxy: runs pause (proxy_down) and resume once configured.
 */
export function createSwitchableAdapter(config: ModelConfig | null, registry: ModelRegistry): SwitchableAdapter {
  let inner: ModelAdapter | null = config ? createModelAdapter(config, registry) : null;
  return {
    get configured() {
      return inner !== null;
    },
    replace(next) {
      inner = next ? createModelAdapter(next, registry) : null;
    },
    async health() {
      if (!inner) return false;
      return inner.health ? inner.health() : true;
    },
    complete(req, opts) {
      if (!inner) return Promise.reject(new ModelError('proxy_down', 'No model endpoint is configured'));
      return inner.complete(req, opts);
    },
  };
}
