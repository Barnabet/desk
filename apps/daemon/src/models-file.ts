import { existsSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { z } from 'zod';
import { ModelInfo } from '@desk/protocol';
import { SEED_MODELS, STANDARD_EFFORTS } from '@desk/core';

/**
 * Files written before reasoning levels existed have `supports_reasoning_effort: boolean`: a model that had it
 * gets its seed levels (or the standard ones), and one that did not gets none. The next save writes the new shape.
 */
function upgradeLegacy(raw: unknown): unknown {
  if (!Array.isArray(raw)) return raw;
  return raw.map((m) => {
    if (!m || typeof m !== 'object' || !('supports_reasoning_effort' in m) || 'reasoning_efforts' in m) return m;
    const { supports_reasoning_effort, ...rest } = m as Record<string, unknown>;
    const seed = SEED_MODELS.find((s) => s.id === rest.id);
    return { ...rest, reasoning_efforts: supports_reasoning_effort === true ? [...(seed?.reasoning_efforts ?? STANDARD_EFFORTS)] : [], default_reasoning_effort: null };
  });
}

/** Registry from models.json, or the seed list when the file is missing or invalid. */
export function loadModels(path: string, warn: (msg: string) => void = () => {}): ModelInfo[] {
  if (!existsSync(path)) return SEED_MODELS;
  try {
    return z.array(ModelInfo).min(1).parse(upgradeLegacy(JSON.parse(readFileSync(path, 'utf8'))));
  } catch (err) {
    warn(`Ignoring invalid ${path}: ${err instanceof Error ? err.message : String(err)}`);
    return SEED_MODELS;
  }
}

export function saveModels(path: string, models: ModelInfo[]): void {
  const tmp = `${path}.tmp`;
  writeFileSync(tmp, `${JSON.stringify(models, null, 2)}\n`);
  renameSync(tmp, path);
}
