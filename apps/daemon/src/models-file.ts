import { existsSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { z } from 'zod';
import { ModelInfo } from '@desk/protocol';
import { SEED_MODELS } from '@desk/core';

/** Registry from models.json, or the seed list when the file is missing or invalid. */
export function loadModels(path: string, warn: (msg: string) => void = () => {}): ModelInfo[] {
  if (!existsSync(path)) return SEED_MODELS;
  try {
    return z.array(ModelInfo).min(1).parse(JSON.parse(readFileSync(path, 'utf8')));
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
