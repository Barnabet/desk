import { describe, expect, it } from 'vitest';
import type { AttentionItem } from '@desk/protocol';
import { ev } from '../testing';
import { affectsAttention, affectsOverview, groupAttention } from './attention';

const item = (kind: AttentionItem['kind'], id: string): AttentionItem => ({ id, kind, project_id: 'p', project_name: 'P', agent_id: null, title: id, detail: '', created_at: '', ref: {} });

describe('attention helpers', () => {
  it('groups items into the four bays', () => {
    const bays = groupAttention([item('approval', 'a'), item('question', 'q'), item('needs_you', 'n'), item('stalled', 's'), item('failed', 'f')]);
    expect(Object.fromEntries(Object.entries(bays).map(([k, v]) => [k, v.map((i) => i.id)]))).toEqual({ clearance: ['a'], queries: ['q'], handoffs: ['n'], holding: ['s', 'f'] });
  });

  it('knows which events change attention and the overview', () => {
    expect(affectsAttention(ev(1, 'approval.requested', { approval_id: 'a', run_id: 'r', tool_call_id: 'c', tool: 'bash', arguments: '{}', reason: 'r', delegate_to_desk: false }))).toBe(true);
    expect(affectsAttention(ev(2, 'usage', { run_id: 'r', model: 'm', prompt_tokens: 1, completion_tokens: 1, estimated: false }))).toBe(false);
    expect(affectsOverview(ev(3, 'tool.call', { run_id: 'r', tool_call_id: 'c', name: 'bash', arguments: '{}' }))).toBe(true);
    expect(affectsOverview(ev(4, 'usage', { run_id: 'r', model: 'm', prompt_tokens: 1, completion_tokens: 1, estimated: false }))).toBe(false);
  });
});
