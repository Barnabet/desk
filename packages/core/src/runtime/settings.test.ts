import { afterEach, describe, expect, it } from 'vitest';
import { getProject } from '../state/queries';
import { createHarness, noSleep, type Harness } from '../testing/harness';
import { Runtime } from './runtime';

let h: Harness;
afterEach(async () => h?.cleanup());

describe('project settings', () => {
  it('stores resolved settings and applies updates', async () => {
    h = await createHarness();
    const rt = new Runtime({ store: h.store, adapter: h.adapter, models: h.models, retry: noSleep });
    const id = rt.createProject({ name: 'P', goal: 'G', settings: { check_in: 'minimal' } });
    expect(getProject(h.store.db, id)?.settings).toMatchObject({ check_in: 'minimal', max_concurrent_threads: 4 });
    rt.updateProject(id, { goal: 'G2', settings: { max_concurrent_threads: 2 } });
    const p = getProject(h.store.db, id)!;
    expect(p.goal).toBe('G2');
    expect(p.settings).toMatchObject({ check_in: 'minimal', max_concurrent_threads: 2 });
    expect(() => rt.updateProject(id, { settings: { review_rounds: -1 } })).toThrow();
    expect(getProject(h.store.db, id)?.settings.review_rounds).toBe(2);
  });
});
