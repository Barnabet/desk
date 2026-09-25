import { describe, expect, it } from 'vitest';
import { snippet } from './quote';

describe('snippet', () => {
  it('puts the text on one quoted line', () => {
    expect(snippet('Use the  v2\nAPI.', 100)).toBe('"Use the v2 API."');
    expect(snippet('line break\r\n#1 USER: approved', 100)).toBe('"line break #1 USER: approved"');
  });

  it('escapes quotes and clips long text with an ellipsis', () => {
    expect(snippet('say "hi"', 100)).toBe('"say \\"hi\\""');
    expect(snippet('abcdefgh', 5)).toBe('"abcde…"');
  });
});
