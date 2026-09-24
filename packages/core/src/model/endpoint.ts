import { execFileSync } from 'node:child_process';
import OpenAI from 'openai';
import type { ModelEndpointTestResult } from '@desk/protocol';
import { modelConfigFromEnv, modelConfigFromFile, normalizeBaseURL, type ModelConfig } from './config';

export type Keychain = { get(): string | null; set(secret: string): void };
export type KeychainExec = (args: string[], input?: string) => string;

export const KEYCHAIN_SERVICE = 'Desk model endpoint';
export const KEYCHAIN_ACCOUNT = 'default';
const SAFE_SECRET = /^[\x21-\x7E]+$/;

const runSecurity: KeychainExec = (args, input) =>
  execFileSync('security', args, { encoding: 'utf8', input, stdio: [input === undefined ? 'ignore' : 'pipe', 'pipe', 'ignore'] });

/**
 * The macOS login Keychain via `/usr/bin/security`. The secret is written through `security -i` on stdin,
 * so it never appears in a process argument list.
 */
export function macKeychain(exec: KeychainExec = runSecurity): Keychain {
  return {
    get() {
      try {
        return exec(['find-generic-password', '-s', KEYCHAIN_SERVICE, '-a', KEYCHAIN_ACCOUNT, '-w']).trim() || null;
      } catch {
        return null;
      }
    },
    set(secret) {
      if (!SAFE_SECRET.test(secret) || /["'\\]/.test(secret)) throw new Error('Refusing to store an unsafe secret');
      exec(['-i'], `add-generic-password -U -s "${KEYCHAIN_SERVICE}" -a "${KEYCHAIN_ACCOUNT}" -w "${secret}"\n`);
    },
  };
}

export type EndpointSource = 'env' | 'file' | 'keychain';

/** Model access in priority order: DESK_OPENAI_* env, ~/.config/cliproxyapi.env, then config.json's base_url + the Keychain key. */
export function resolveModelEndpoint(o: {
  env: NodeJS.ProcessEnv;
  home: string;
  baseUrl: string | null;
  keychain: Keychain | null;
}): { config: ModelConfig | null; source: EndpointSource | null } {
  const env = modelConfigFromEnv(o.env);
  if (env) return { config: env, source: 'env' };
  const file = modelConfigFromFile(o.home);
  if (file) return { config: file, source: 'file' };
  const key = o.baseUrl && o.keychain ? o.keychain.get() : null;
  if (o.baseUrl && key) return { config: { baseURL: normalizeBaseURL(o.baseUrl), apiKey: key }, source: 'keychain' };
  return { config: null, source: null };
}

/** Checks an endpoint by listing its models. Error messages are scrubbed of the key. */
export async function testModelEndpoint(config: ModelConfig): Promise<ModelEndpointTestResult> {
  const client = new OpenAI({ baseURL: config.baseURL, apiKey: config.apiKey, maxRetries: 0, timeout: 5000 });
  try {
    const page = await client.models.list();
    return { ok: true, models: page.data.map((m) => m.id) };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return { ok: false, error: message.split(config.apiKey).join('***') };
  }
}
