import { describe, expect, it } from 'vitest';
import { ev } from '../testing';
import { reduceSystem, systemFromHealth } from './system';

describe('reduceSystem', () => {
  it('tracks proxy state and keeps a bounded notice log', () => {
    let s = systemFromHealth({ version: '1', protocol_version: 1, proxy: 'up', uptime_s: 3 });
    expect(s.proxy).toBe('up');
    s = reduceSystem(s, ev(1, 'system.notice', { level: 'error', code: 'proxy_down', message: 'paused' }));
    expect(s.proxy).toBe('down');
    s = reduceSystem(s, ev(2, 'system.notice', { level: 'info', code: 'proxy_up', message: 'back' }));
    expect(s.proxy).toBe('up');
    s = reduceSystem(s, ev(3, 'message.user', { text: 'x' }));
    expect(s.notices.map((n) => n.code)).toEqual(['proxy_down', 'proxy_up']);
    for (let i = 4; i < 300; i++) s = reduceSystem(s, ev(i, 'system.notice', { level: 'info', code: 'n', message: String(i) }));
    expect(s.notices).toHaveLength(200);
    expect(s.notices.at(-1)!.message).toBe('299');
    expect(systemFromHealth({ version: '1', protocol_version: 1 }).proxy).toBe('unknown');
  });
});
