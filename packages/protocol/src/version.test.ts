import { describe, expect, it } from 'vitest';
import { PROTOCOL_VERSION } from '@desk/protocol';

describe('protocol', () => {
  it('exposes the protocol version', () => {
    expect(PROTOCOL_VERSION).toBe(1);
  });
});
