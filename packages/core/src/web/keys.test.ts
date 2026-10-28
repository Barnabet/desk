import { describe, expect, it } from 'vitest';
import { macKeychain, type Keychain } from '../model/endpoint';
import { createWebKeys, WEB_KEYCHAIN_SERVICES } from './keys';

const memKeychain = (secret: string | null = null): Keychain & { secret: string | null } => {
  const k = { secret, get: () => k.secret, set: (s: string) => void (k.secret = s), delete: () => void (k.secret = null) };
  return k;
};
const keychains = () => ({ serper: memKeychain(), firecrawl: memKeychain() });

describe('macKeychain for another service', () => {
  it('stores under the given service, with the secret on stdin only', () => {
    const calls: Array<{ args: string[]; input?: string }> = [];
    const kc = macKeychain((args, input) => {
      calls.push({ args, input });
      return '';
    }, WEB_KEYCHAIN_SERVICES.serper);
    kc.set('abc12345');
    expect(calls).toHaveLength(1);
    expect(calls[0]?.args.join(' ')).not.toContain('abc12345');
    expect(calls[0]?.input).toContain('-s "Desk Serper API key"');
    expect(calls[0]?.input).toContain('-w "abc12345"');
  });

  it('deletes the item without the secret in the arguments, and ignores a missing item', () => {
    const calls: string[][] = [];
    const kc = macKeychain((args) => {
      calls.push(args);
      throw new Error('The specified item could not be found in the keychain.');
    }, 'Desk Firecrawl API key');
    expect(() => kc.delete()).not.toThrow();
    expect(calls[0]).toEqual(['delete-generic-password', '-s', 'Desk Firecrawl API key', '-a', 'default']);
  });
});

describe('createWebKeys', () => {
  it('prefers the env key over the Keychain', () => {
    const kcs = keychains();
    kcs.serper.secret = 'from-keychain';
    const keys = createWebKeys({ env: { DESK_SERPER_API_KEY: 'from-env' }, keychains: kcs });
    expect(keys.get('serper')).toBe('from-env');
    expect(keys.status('serper')).toEqual({ set: true, source: 'env' });
    expect(keys.get('firecrawl')).toBeNull();
    expect(keys.status('firecrawl')).toEqual({ set: false, source: null });
  });

  it('stores, reads and removes a Keychain key', () => {
    const kcs = keychains();
    const keys = createWebKeys({ env: {}, keychains: kcs });
    expect(keys.set('firecrawl', 'fc-12345678')).toEqual({ set: true, source: 'keychain' });
    expect(keys.get('firecrawl')).toBe('fc-12345678');
    expect(kcs.firecrawl.secret).toBe('fc-12345678');
    expect(keys.remove('firecrawl')).toEqual({ set: false, source: null });
    expect(keys.get('firecrawl')).toBeNull();
  });

  it('still reports an env key after the Keychain one is removed', () => {
    const keys = createWebKeys({ env: { DESK_FIRECRAWL_API_KEY: 'env-key' }, keychains: keychains() });
    expect(keys.remove('firecrawl')).toEqual({ set: true, source: 'env' });
  });

  it('cannot store a key without a Keychain, but still reads env keys', () => {
    const keys = createWebKeys({ env: { DESK_SERPER_API_KEY: 'env-key' }, keychains: null });
    expect(keys.get('serper')).toBe('env-key');
    expect(() => keys.set('serper', 'abc12345')).toThrow(/Keychain/);
  });

  it('treats a blank env value as unset', () => {
    const keys = createWebKeys({ env: { DESK_SERPER_API_KEY: '  ' }, keychains: keychains() });
    expect(keys.status('serper')).toEqual({ set: false, source: null });
  });
});
