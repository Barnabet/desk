import { describe, expect, it } from 'vitest';
import { ApiError, DaemonNotRunning, DaemonUnavailable, ProtocolMismatch } from './errors';
import { ev } from './testing';

describe('errors', () => {
  it('carry their details', () => {
    const e = new ApiError(409, 'conflict', 'Already decided', { by: 'desk' });
    expect(e).toBeInstanceOf(Error);
    expect([e.status, e.code, e.message, e.details]).toEqual([409, 'conflict', 'Already decided', { by: 'desk' }]);
    expect(new DaemonUnavailable('http://127.0.0.1:1').message).toContain('Cannot reach deskd');
    expect(new DaemonNotRunning('/data').message).toContain('/data');
    expect(new ProtocolMismatch(2, 1).message).toContain('protocol 2');
  });
});

describe('ev', () => {
  it('builds stored events with defaults', () => {
    expect(ev(3, 'message.user', { text: 'hi' }, { agent: 'd' })).toEqual({ id: 3, project_id: 'p', agent_id: 'd', ts: '2026-09-24T10:00:03.000Z', type: 'message.user', payload: { text: 'hi' } });
  });
});
