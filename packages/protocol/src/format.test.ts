import { describe, expect, it } from 'vitest';
import { clip, summarizeToolArgs } from '@desk/protocol';

describe('format', () => {
  it('clips to a single line with an ellipsis', () => {
    expect(clip('a\n  b', 10)).toBe('a b');
    expect(clip('x'.repeat(20), 10)).toBe('xxxxxxxxx…');
  });

  it('summarises tool arguments by their first string value', () => {
    expect(summarizeToolArgs('{"path":"emails/04.md","content":"x"}')).toBe('emails/04.md');
    expect(summarizeToolArgs('{"n":1}')).toBe('');
    expect(summarizeToolArgs('not json')).toBe('not json');
    expect(summarizeToolArgs(JSON.stringify({ command: 'a\n  b' }))).toBe('a b');
    expect(summarizeToolArgs(JSON.stringify({ command: 'x'.repeat(200) }), 10)).toBe('xxxxxxxxx…');
  });
});
