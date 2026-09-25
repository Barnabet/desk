import type { ModelInfo, ReasoningEffort } from '@desk/protocol';
import { ValidationError } from '../errors';

export const DEFAULT_MODEL_ID = 'claude-opus-5-5';

/** The levels most endpoints accept (verified against the local proxy on 2026-09-24 for all four seed models). */
export const STANDARD_EFFORTS: ReasoningEffort[] = ['low', 'medium', 'high', 'xhigh', 'max'];

/**
 * Conservative defaults; exact context windows are confirmed in Plan 5 and edited in the registry, not elsewhere.
 * Reasoning levels are what the proxy accepts per model (claude-fable-5-1 rejects `none`); no default is sent unless chosen.
 * All four take images (verified through the proxy for claude-opus-5-5 and gpt-6-sol on 2026-09-24).
 */
export const SEED_MODELS: ModelInfo[] = [
  { id: 'claude-opus-5-5', family: 'claude', context_window: 200_000, max_output_tokens: 32_000, reasoning_efforts: ['none', ...STANDARD_EFFORTS], default_reasoning_effort: null, concurrency: 4, vision: true },
  { id: 'claude-fable-5-1', family: 'claude', context_window: 200_000, max_output_tokens: 32_000, reasoning_efforts: [...STANDARD_EFFORTS], default_reasoning_effort: null, concurrency: 2, vision: true },
  { id: 'gpt-6-astra', family: 'gpt', context_window: 200_000, max_output_tokens: 32_000, reasoning_efforts: ['none', ...STANDARD_EFFORTS], default_reasoning_effort: null, concurrency: 4, vision: true },
  { id: 'gpt-6-sol', family: 'gpt', context_window: 200_000, max_output_tokens: 32_000, reasoning_efforts: ['none', ...STANDARD_EFFORTS], default_reasoning_effort: null, concurrency: 4, vision: true },
];

/**
 * The level to send for a model: the wanted one when the model accepts it, else the model's default, else none
 * (undefined: the endpoint decides). A level a model does not accept is never sent.
 */
export function effortFor(model: ModelInfo | undefined, wanted: ReasoningEffort | null | undefined): ReasoningEffort | undefined {
  if (!model) return undefined;
  if (wanted && model.reasoning_efforts.includes(wanted)) return wanted;
  const fallback = model.default_reasoning_effort;
  return fallback && model.reasoning_efforts.includes(fallback) ? fallback : undefined;
}

export class ModelRegistry {
  private readonly models = new Map<string, ModelInfo>();

  constructor(models: ModelInfo[] = SEED_MODELS) {
    for (const m of models) this.models.set(m.id, m);
  }

  get(id: string): ModelInfo {
    const m = this.models.get(id);
    if (!m) throw new ValidationError(`Unknown model: ${id}`);
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

  replaceAll(models: ModelInfo[]): void {
    this.models.clear();
    for (const m of models) this.models.set(m.id, m);
  }
}
