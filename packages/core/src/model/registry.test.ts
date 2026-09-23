import { describe, expect, it } from 'vitest';
import { DEFAULT_MODEL_ID, ModelRegistry, SEED_MODELS } from './registry';

describe('ModelRegistry', () => {
  it('seeds the four supported models with opus 5.5 as default', () => {
    const r = new ModelRegistry();
    expect(DEFAULT_MODEL_ID).toBe('claude-opus-5-5');
    expect(r.list().map((m) => m.id)).toEqual(['claude-opus-5-5', 'claude-fable-5-1', 'gpt-6-astra', 'gpt-6-sol']);
    expect(SEED_MODELS.find((m) => m.id === 'claude-fable-5-1')?.concurrency).toBe(2);
  });

  it('throws on unknown models and supports upsert', () => {
    const r = new ModelRegistry([]);
    expect(() => r.get('x')).toThrow(/Unknown model: x/);
    r.upsert({ id: 'x', family: 'gpt', context_window: 1000, max_output_tokens: 100, supports_reasoning_effort: false, concurrency: 1 });
    expect(r.has('x')).toBe(true);
    expect(r.get('x').context_window).toBe(1000);
  });
});
