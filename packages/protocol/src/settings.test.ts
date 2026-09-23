import { describe, expect, it } from 'vitest';
import { DEFAULT_POLICY, EventBody, ProjectSettings, resolveSettings } from '@desk/protocol';

describe('ProjectSettings', () => {
  it('resolves defaults', () => {
    const s = resolveSettings();
    expect(s).toMatchObject({
      desk_model: 'claude-opus-5-5',
      thread_model: 'claude-opus-5-5',
      fallback_model: null,
      max_concurrent_threads: 4,
      check_in: 'normal',
      autonomy: 'dispatch-freely',
      review_rounds: 2,
    });
    expect(s.policy).toEqual(DEFAULT_POLICY);
  });

  it('keeps provided values and rejects invalid ones', () => {
    expect(resolveSettings({ check_in: 'minimal', max_concurrent_threads: 2 })).toMatchObject({ check_in: 'minimal', max_concurrent_threads: 2 });
    expect(() => ProjectSettings.parse({ check_in: 'loud' })).toThrow();
    expect(() => ProjectSettings.parse({ max_concurrent_threads: 0 })).toThrow();
  });

  it('default policy follows spec order', () => {
    expect(DEFAULT_POLICY.map((r) => `${r.tool}:${r.action}`)).toEqual([
      'bash:ask',
      'bash_background:ask',
      'bash_readonly:ask',
      'git_push:allow',
      'git_push:deny',
      'open_pr:ask',
      'web_fetch:allow',
      'web_search:allow',
    ]);
  });

  it('validates project.updated events', () => {
    expect(EventBody.parse({ type: 'project.updated', payload: { settings: { check_in: 'detailed' } } }).type).toBe('project.updated');
    expect(() => EventBody.parse({ type: 'project.updated', payload: { settings: { check_in: 'nope' } } })).toThrow();
  });
});
