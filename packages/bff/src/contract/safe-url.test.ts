import { describe, expect, it } from 'vitest';
import { safeExternalUrl, UnsafeUrlError } from './safe-url';

const refusal = (raw: string): unknown => {
  try {
    safeExternalUrl(raw);
  } catch (err) {
    return err;
  }
  return null;
};

describe('safeExternalUrl', () => {
  it('passes web and mail links through, normalised', () => {
    expect(safeExternalUrl('https://example.com/a?b=1')).toBe('https://example.com/a?b=1');
    expect(safeExternalUrl('HTTP://Example.com')).toBe('http://example.com/');
    expect(safeExternalUrl('mailto:a@b.c')).toBe('mailto:a@b.c');
  });

  it('refuses anything else with a message people can read', () => {
    expect(refusal('not a url')).toMatchObject({ name: 'UnsafeUrlError', code: 'invalid_url', message: 'That is not a valid link.' });
    for (const url of ['javascript:alert(1)', 'file:///etc/passwd', 'data:text/html,hi']) {
      const err = refusal(url);
      expect(err).toBeInstanceOf(UnsafeUrlError);
      expect(err).toMatchObject({ code: 'invalid_url', message: 'Only web and mail links can be opened.' });
    }
  });
});
