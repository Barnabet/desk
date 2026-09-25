// @vitest-environment jsdom
import { cleanup, render } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import type { ToolCallView } from '@desk/client';
import { ToolGroup } from './ToolGroup';

afterEach(cleanup);

const call = (id: string, name: string, args: unknown): ToolCallView => ({
  id,
  name,
  arguments: typeof args === 'string' ? args : JSON.stringify(args),
  status: 'ok',
  content: null,
});
/** Each call's line: its name, then its arguments on one line. */
const lines = () => [...document.querySelectorAll('.toolgroup li .mono')].map((el) => el.textContent);

describe('ToolGroup', () => {
  it('names a thread by the title titleOf gives, and summarises everything else as before', () => {
    const calls = [
      call('1', 'read_thread', { thread_id: 'a', mode: 'full' }),
      call('2', 'stop_thread', { thread_id: 'a', reason: 'Duplicate work' }),
      call('3', 'review_diff', { thread_id: 'zz' }),
      call('4', 'read_file', { path: 'notes.md' }),
      call('5', 'bash', 'not json'),
    ];
    render(<ToolGroup calls={calls} titleOf={(id) => (id === 'a' ? 'Auth API' : undefined)} />);
    expect(lines()).toEqual(['read_thread Auth API', 'stop_thread Auth API', 'review_diff zz', 'read_file notes.md', 'bash not json']);
  });

  it('shows the arguments unchanged without titleOf', () => {
    render(<ToolGroup calls={[call('1', 'read_thread', { thread_id: 'a' })]} />);
    expect(lines()).toEqual(['read_thread a']);
  });
});
