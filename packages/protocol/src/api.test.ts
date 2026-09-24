import { describe, expect, it } from 'vitest';
import { CreateProjectRequest, LibraryUploadRequest, ModelsPutRequest, ResolveApprovalRequest, StreamClientMessage } from '@desk/protocol';

describe('API schemas', () => {
  it('validates project creation', () => {
    expect(CreateProjectRequest.parse({ name: 'P' })).toMatchObject({ name: 'P', goal: '' });
    expect(CreateProjectRequest.parse({ name: 'P', settings: { check_in: 'minimal' }, sources: [{ path: '/x' }] }).sources).toHaveLength(1);
    expect(() => CreateProjectRequest.parse({ name: '' })).toThrow();
    expect(() => CreateProjectRequest.parse({ name: 'P', settings: { check_in: 'loud' } })).toThrow();
  });

  it('validates approvals, uploads and models', () => {
    expect(() => ResolveApprovalRequest.parse({ decision: 'maybe' })).toThrow();
    expect(LibraryUploadRequest.parse({ name: 'a.txt', content_base64: 'aGk=' }).name).toBe('a.txt');
    expect(() => ModelsPutRequest.parse([])).toThrow();
  });

  it('validates stream subscriptions', () => {
    expect(StreamClientMessage.parse({ subscribe: { project_id: '*', after_seq: 0 } })).toEqual({ subscribe: { project_id: '*', after_seq: 0 } });
    expect(() => StreamClientMessage.parse({ subscribe: { project_id: 'p', after_seq: -1 } })).toThrow();
  });
});

import { AttentionItem, DaemonConfigPatch, ModelEndpointPutRequest } from '@desk/protocol';

describe('UI API schemas', () => {
  it('parses an attention item', () => {
    const item = AttentionItem.parse({
      id: 'approval:ap1', kind: 'approval', project_id: 'p1', project_name: 'Demo', agent_id: 't1',
      title: 'Signup checklist wants to run bash', detail: 'rule 1', created_at: '2026-09-24T10:00:00.000Z',
      ref: { approval_id: 'ap1', thread_id: 't1' },
    });
    expect(item.kind).toBe('approval');
  });

  it('accepts hello and subscribe stream messages', () => {
    expect(StreamClientMessage.parse({ hello: { client: 'desktop', notifications: true } })).toEqual({ hello: { client: 'desktop', notifications: true } });
    expect(StreamClientMessage.parse({ subscribe: { project_id: '*', after_seq: 0 } })).toMatchObject({ subscribe: { project_id: '*' } });
    expect(() => StreamClientMessage.parse({ nope: 1 })).toThrow();
  });

  it('rejects API keys with whitespace, quotes or backslashes', () => {
    const ok = { base_url: 'http://127.0.0.1:8317/v1', api_key: 'sk-abc_123.XYZ' };
    expect(ModelEndpointPutRequest.parse(ok)).toEqual(ok);
    for (const bad of ['has space', 'quote"d', "single'q", 'back\\slash', 'new\nline', '']) {
      expect(() => ModelEndpointPutRequest.parse({ ...ok, api_key: bad })).toThrow();
    }
    expect(() => ModelEndpointPutRequest.parse({ ...ok, base_url: 'not a url' })).toThrow();
  });

  it('patches daemon config', () => {
    expect(DaemonConfigPatch.parse({ notifications: 'off' })).toEqual({ notifications: 'off' });
    expect(() => DaemonConfigPatch.parse({ notifications: 'sometimes' })).toThrow();
  });
});
