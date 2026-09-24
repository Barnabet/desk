import { describe, expect, it } from 'vitest';
import { startFakeModel, text } from '@desk/fake-model';
import { FAKE_MODEL } from '../testing';
import { ModelError } from './errors';
import { ModelRegistry, SEED_MODELS } from './registry';
import { createSwitchableAdapter } from './switchable';

describe('createSwitchableAdapter', () => {
  it('reports proxy_down while unconfigured and works once configured', async () => {
    const registry = new ModelRegistry([...SEED_MODELS, FAKE_MODEL]);
    const adapter = createSwitchableAdapter(null, registry);
    expect(adapter.configured).toBe(false);
    expect(await adapter.health!()).toBe(false);
    const err = await adapter.complete({ model: FAKE_MODEL.id, messages: [], tools: [] }).catch((e) => e);
    expect(err).toBeInstanceOf(ModelError);
    expect(err.kind).toBe('proxy_down');

    const fake = await startFakeModel([text('hello')]);
    try {
      adapter.replace({ baseURL: fake.url, apiKey: 'k' });
      expect(adapter.configured).toBe(true);
      expect(await adapter.health!()).toBe(true);
      expect((await adapter.complete({ model: FAKE_MODEL.id, messages: [{ role: 'user', content: 'hi' }], tools: [] })).content).toBe('hello');
    } finally {
      await fake.close();
    }
  });
});
