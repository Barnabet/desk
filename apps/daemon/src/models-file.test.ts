import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { SEED_MODELS } from '@desk/core';
import { loadModels, saveModels } from './models-file';

let dir: string;
afterEach(() => rmSync(dir, { recursive: true, force: true }));

describe('models.json', () => {
  it('upgrades the old reasoning checkbox: a ticked model gets its seed levels, an unticked one gets none', () => {
    dir = mkdtempSync(join(tmpdir(), 'desk-models-'));
    const path = join(dir, 'models.json');
    const legacy = (id: string, on: boolean) => ({ id, family: 'claude', context_window: 500000, max_output_tokens: 32000, supports_reasoning_effort: on, concurrency: 4 });
    writeFileSync(path, JSON.stringify([legacy('claude-opus-5-5', true), legacy('claude-fable-5-1', false), legacy('my-model', true)]));
    const models = loadModels(path);
    expect(models.map((m) => [m.id, m.reasoning_efforts, m.default_reasoning_effort, m.context_window])).toEqual([
      ['claude-opus-5-5', SEED_MODELS[0]!.reasoning_efforts, null, 500000],
      ['claude-fable-5-1', [], null, 500000],
      ['my-model', ['low', 'medium', 'high', 'xhigh', 'max'], null, 500000],
    ]);
    saveModels(path, models);
    expect(readFileSync(path, 'utf8')).not.toContain('supports_reasoning_effort');
    expect(loadModels(path)).toEqual(models);
  });

  it('falls back to the seeds when a default level is not one of the model’s levels', () => {
    dir = mkdtempSync(join(tmpdir(), 'desk-models-'));
    const path = join(dir, 'models.json');
    writeFileSync(path, JSON.stringify([{ id: 'x', family: 'gpt', context_window: 1, max_output_tokens: 1, reasoning_efforts: ['low'], default_reasoning_effort: 'max', concurrency: 1 }]));
    const warnings: string[] = [];
    expect(loadModels(path, (w) => warnings.push(w))).toBe(SEED_MODELS);
    expect(warnings[0]).toMatch(/default reasoning effort must be one of the model/);
  });
});
