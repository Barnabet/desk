import { existsSync, readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

export type ModelConfig = { baseURL: string; apiKey: string };

export function parseEnvFile(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const raw of text.split('\n')) {
    const line = raw.trim().replace(/^export\s+/, '');
    if (!line || line.startsWith('#')) continue;
    const eq = line.indexOf('=');
    if (eq <= 0) continue;
    const key = line.slice(0, eq).trim();
    let value = line.slice(eq + 1).trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    out[key] = value;
  }
  return out;
}

export function normalizeBaseURL(url: string): string {
  const trimmed = url.replace(/\/+$/, '');
  return trimmed.endsWith('/v1') ? trimmed : `${trimmed}/v1`;
}

export function loadModelConfig(env: NodeJS.ProcessEnv = process.env, home: string = homedir()): ModelConfig {
  if (env.DESK_OPENAI_BASE_URL && env.DESK_OPENAI_API_KEY) {
    return { baseURL: normalizeBaseURL(env.DESK_OPENAI_BASE_URL), apiKey: env.DESK_OPENAI_API_KEY };
  }
  const file = join(home, '.config', 'cliproxyapi.env');
  if (existsSync(file)) {
    const vars = parseEnvFile(readFileSync(file, 'utf8'));
    if (vars.CLIPROXY_BASE_URL && vars.CLIPROXY_API_KEY) {
      return { baseURL: normalizeBaseURL(vars.CLIPROXY_BASE_URL), apiKey: vars.CLIPROXY_API_KEY };
    }
  }
  throw new Error(
    'No model access configured: set DESK_OPENAI_BASE_URL and DESK_OPENAI_API_KEY, or create ~/.config/cliproxyapi.env with CLIPROXY_BASE_URL and CLIPROXY_API_KEY',
  );
}
