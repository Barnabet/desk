import { describe, expect, it } from 'vitest';
import type { ToolCallView } from '@desk/client';
import { toolNames } from './tool-names';

const call = (id: string, name: string): ToolCallView => ({ id, name, arguments: '{}', status: 'ok', content: null });

describe('toolNames', () => {
  it('names each tool once, in first-use order, with a count when it ran more than once', () => {
    expect(toolNames([call('1', 'read_file'), call('2', 'skill_run'), call('3', 'read_file')])).toBe('read_file ×2 · skill_run');
    expect(toolNames([])).toBe('');
  });
});
