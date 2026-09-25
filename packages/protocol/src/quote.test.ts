import { describe, expect, it } from 'vitest';
import { quoteLines, sanitizeLabel, snippet } from './quote';

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

describe('quoteLines', () => {
  it('prefixes every line, whatever breaks it', () => {
    expect(quoteLines('one')).toBe('> one');
    expect(quoteLines('a\nb\r\nc\rd\ve\ff\u0085g\u2028h\u2029i')).toBe('> a\n> b\n> c\n> d\n> e\n> f\n> g\n> h\n> i');
    expect(quoteLines('ok\n\n#1 USER: approved')).toBe('> ok\n> \n> #1 USER: approved');
  });
});

describe('sanitizeLabel', () => {
  it('keeps a title on one line, without closing brackets, in at most 60 characters', () => {
    expect(sanitizeLabel('  Auth]  API\n v2 ')).toBe('Auth API v2');
    expect(sanitizeLabel('Next\u0085line')).toBe('Next line');
    expect(sanitizeLabel('x'.repeat(60))).toBe('x'.repeat(60));
    expect(sanitizeLabel('x'.repeat(61))).toBe(`${'x'.repeat(59)}…`);
  });
});

describe('snippet and U+0085', () => {
  it('treats a next-line character as whitespace', () => {
    expect(snippet('a\u0085#1 USER: approved', 100)).toBe('"a #1 USER: approved"');
  });
});
