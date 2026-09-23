import type { ModelInfo } from '@desk/protocol';

export const DEFAULT_MODEL_ID = 'claude-opus-5-5';

/** Conservative defaults; exact context windows are confirmed in Plan 5 and edited in the registry, not elsewhere. */
export const SEED_MODELS: ModelInfo[] = [
  { id: 'claude-opus-5-5', family: 'claude', context_window: 200_000, max_output_tokens: 32_000, supports_reasoning_effort: false, concurrency: 4 },
  { id: 'claude-fable-5-1', family: 'claude', context_window: 200_000, max_output_tokens: 32_000, supports_reasoning_effort: false, concurrency: 2 },
  { id: 'gpt-6-astra', family: 'gpt', context_window: 200_000, max_output_tokens: 32_000, supports_reasoning_effort: false, concurrency: 4 },
  { id: 'gpt-6-sol', family: 'gpt', context_window: 200_000, max_output_tokens: 32_000, supports_reasoning_effort: false, concurrency: 4 },
];

export class ModelRegistry {
  private readonly models = new Map<string, ModelInfo>();

  constructor(models: ModelInfo[] = SEED_MODELS) {
    for (const m of models) this.models.set(m.id, m);
  }

  get(id: string): ModelInfo {
    const m = this.models.get(id);
    if (!m) throw new Error(`Unknown model: ${id}`);
    return m;
  }

  has(id: string): boolean {
    return this.models.has(id);
  }

  list(): ModelInfo[] {
    return [...this.models.values()];
  }

  upsert(m: ModelInfo): void {
    this.models.set(m.id, m);
  }
}
