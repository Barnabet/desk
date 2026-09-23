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
    expect(StreamClientMessage.parse({ subscribe: { project_id: '*', after_seq: 0 } }).subscribe.project_id).toBe('*');
    expect(() => StreamClientMessage.parse({ subscribe: { project_id: 'p', after_seq: -1 } })).toThrow();
  });
});
