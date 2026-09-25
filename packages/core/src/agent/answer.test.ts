import { describe, expect, it } from 'vitest';
import { answerText, closureText, WHY } from './answer';

describe('answer texts', () => {
  it('trims an answer and clips it to 4000 characters', () => {
    expect(answerText('  Per seat.\n')).toBe('Per seat.');
    expect(answerText(' \n ')).toBe('');
    expect(answerText('y'.repeat(4000))).toBe('y'.repeat(4000));
    const long = answerText('x'.repeat(5000));
    expect(long).toHaveLength(4000);
    expect(long.endsWith('x\n[… clipped; ask again or use read_thread]')).toBe(true);
  });

  it("writes the runtime's closures as one line in parentheses", () => {
    expect(closureText('Frontend', WHY.stopped)).toBe('(Frontend was stopped before answering.)');
    expect(closureText('Auth]\nAPI', WHY.noAnswer)).toBe('(Auth API did not answer.)');
    expect(closureText(null, WHY.archived)).toBe('(untitled was archived before answering.)');
    expect(closureText('Frontend', WHY.unfinished)).toBe('(Frontend did not finish answering. Ask again or use read_thread.)');
    expect(closureText('Frontend', WHY.restart)).toBe('(Frontend could not answer: Desk was restarting. Ask again if you still need to know.)');
    expect(closureText('Frontend', WHY.noWorkspace)).toBe('(Frontend could not answer: its workspace is missing.)');
    expect(closureText('Frontend', WHY.error(' 401 bad key. '))).toBe('(Frontend could not answer: 401 bad key.)');
  });
});
