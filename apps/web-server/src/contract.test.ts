import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { AUTH_TIMEOUT_MS, CLOSE_BAD_FRAME, CLOSE_UNAUTHORIZED, PUSH_OPS, PushClientFrame, SESSION_HEADER, SESSION_STORAGE_KEY, SessionFrame, webChannels } from './contract';

describe('desk web contract', () => {
  it('names the session header, the storage key, the close codes and the /push operations', () => {
    expect(SESSION_HEADER).toBe('x-desk-session');
    expect(SESSION_STORAGE_KEY).toBe('desk.session');
    expect([CLOSE_UNAUTHORIZED, CLOSE_BAD_FRAME, AUTH_TIMEOUT_MS]).toEqual([4401, 4400, 5000]);
    expect(PUSH_OPS).toEqual(['broker.watch', 'broker.unwatch']);
  });

  it('parses /push client frames', () => {
    expect(SessionFrame.safeParse({ session: 's' }).success).toBe(true);
    expect(SessionFrame.safeParse({ session: '' }).success).toBe(false);
    expect(PushClientFrame.parse({ op: 'broker.watch', id: 1, input: { projectId: 'p', afterSeq: 0 } })).toMatchObject({ op: 'broker.watch', id: 1 });
    expect(PushClientFrame.parse({ notifyPermission: 'granted' })).toEqual({ notifyPermission: 'granted' });
    expect(PushClientFrame.safeParse({ notifyPermission: 'maybe' }).success).toBe(false);
    expect(PushClientFrame.safeParse({ op: 'broker.watch', id: -1 }).success).toBe(false);
    expect(PushClientFrame.safeParse({ op: 'broker.watch', id: 1.5 }).success).toBe(false);
  });

  it('validates fs.listDirs input', () => {
    expect(webChannels['fs.listDirs'].safeParse({}).success).toBe(true);
    expect(webChannels['fs.listDirs'].safeParse({ path: '~/code', hidden: true }).success).toBe(true);
    expect(webChannels['fs.listDirs'].safeParse({ hidden: 'yes' }).success).toBe(false);
    expect(webChannels['fs.listDirs'].safeParse({ path: '' }).success).toBe(false);
  });

  it('stays browser-safe: no Node imports or globals in what the web UI imports', () => {
    for (const file of ['contract.ts', 'codec.ts', 'frames.ts', 'web-channels.ts']) {
      const src = readFileSync(new URL(`./${file}`, import.meta.url), 'utf8');
      expect(src, file).not.toMatch(/from '(node:[^']+|ws|hono[^']*|@hono\/[^']+|@desk\/bff\/server|@desk\/client\/node)'/);
      expect(src, file).not.toMatch(/\bBuffer\b|\bNodeJS\.|\bprocess\./);
    }
  });
});
