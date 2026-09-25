import { describe, expect, it } from 'vitest';
import { base64ToBytes, bytesToBase64, decodeBytes, encodeBytes } from './codec';

describe('$bytes codec', () => {
  it('tags every Uint8Array at any depth and restores it after JSON', () => {
    const value = { ok: true, value: { name: 'a.bin', data: new Uint8Array([0, 1, 2, 250, 255]), list: [new Uint8Array([7]), 'x', 3, null] } };
    const encoded = encodeBytes(value);
    expect(encoded).toEqual({ ok: true, value: { name: 'a.bin', data: { $bytes: 'AAEC+v8=' }, list: [{ $bytes: 'Bw==' }, 'x', 3, null] } });
    expect(decodeBytes(JSON.parse(JSON.stringify(encoded)))).toEqual(value);
  });

  it('encodes Node Buffers, empty arrays and large arrays', () => {
    expect(encodeBytes(Buffer.from('hi'))).toEqual({ $bytes: 'aGk=' });
    expect(encodeBytes(new Uint8Array())).toEqual({ $bytes: '' });
    expect(decodeBytes({ $bytes: '' })).toEqual(new Uint8Array());
    const big = new Uint8Array(200_000).map((_, i) => (i * 7) % 256);
    expect(bytesToBase64(big)).toBe(Buffer.from(big).toString('base64'));
    expect(decodeBytes(JSON.parse(JSON.stringify(encodeBytes(big))))).toEqual(big);
  });

  it('leaves every other value alone', () => {
    const at = new Date('2026-09-25T10:00:00Z');
    const withDate = { at, n: 1 };
    expect(encodeBytes(withDate)).toEqual({ at, n: 1 });
    expect(decodeBytes({ $bytes: 'AAE=', other: 1 })).toEqual({ $bytes: 'AAE=', other: 1 });
    expect(decodeBytes({ $bytes: 5 })).toEqual({ $bytes: 5 });
    expect(decodeBytes('text')).toBe('text');
    expect(decodeBytes(null)).toBeNull();
  });

  it('refuses invalid base64', () => {
    expect(() => decodeBytes({ $bytes: '%%%' })).toThrow();
    expect(base64ToBytes('AAEC')).toEqual(new Uint8Array([0, 1, 2]));
  });
});
