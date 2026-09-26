import { describe, expect, it } from 'vitest';
import { tokens } from './UsageTab';

describe('tokens', () => {
  it('keeps small counts exact and scales large ones to k, M and B', () => {
    expect(tokens(236)).toBe('236');
    expect(tokens(7_700)).toBe('7.7k');
    expect(tokens(5_811_000)).toBe('5.8M');
    expect(tokens(402_266_000)).toBe('402M');
    expect(tokens(3_200_000_000)).toBe('3.2B');
  });

  it('moves to the next unit instead of printing 1000k', () => {
    expect(tokens(999_700)).toBe('1.0M');
    expect(tokens(9_960)).toBe('10k');
  });
});
