import { describe, expect, it, vi } from 'vitest';
import { FakeDeskBridge } from './fake-bridge';

describe('FakeDeskBridge', () => {
  it("records calls and answers from its handlers, like the desktop's installBridge", async () => {
    const bridge = new FakeDeskBridge({ 'projects.get': ({ id }: { id: string }) => ({ id }) });
    await expect(bridge.call('projects.get', { id: 'p' })).resolves.toEqual({ id: 'p' });
    await expect(bridge.call('overview', {})).rejects.toMatchObject({ name: 'DeskCallError', code: 'unknown_channel', message: 'overview' });
    bridge.handle('attention.dismiss', () => {
      throw { code: 'conflict', message: 'Already decided', status: 409 };
    });
    await expect(bridge.call('attention.dismiss', { id: 'a' })).rejects.toMatchObject({ name: 'DeskCallError', code: 'conflict', status: 409 });
    expect(bridge.calls).toEqual([
      { channel: 'projects.get', input: { id: 'p' } },
      { channel: 'overview', input: {} },
      { channel: 'attention.dismiss', input: { id: 'a' } },
    ]);
  });

  it('pushes to listeners until they unsubscribe, and fires reconnects', () => {
    const bridge = new FakeDeskBridge();
    const seen: unknown[] = [];
    const off = bridge.onPush('desk:event', (e) => seen.push(e));
    bridge.emit('desk:event', 1);
    off();
    bridge.emit('desk:event', 2);
    expect(seen).toEqual([1]);
    const again = vi.fn();
    bridge.onReconnect(again);
    bridge.reconnect();
    expect(again).toHaveBeenCalledTimes(1);
    bridge.signOut();
    bridge.setPushStatus('reconnecting');
    expect([bridge.signedOut(), bridge.pushStatus()]).toEqual([true, 'reconnecting']);
  });

  it('asks for a folder like the real bridge when no handler answers app.pickFolder', async () => {
    const bridge = new FakeDeskBridge();
    const picked = bridge.call('app.pickFolder', { purpose: 'source' });
    expect(bridge.folderRequest()).toEqual({ purpose: 'source' });
    bridge.answerFolder('/Users/me/code');
    await expect(picked).resolves.toBe('/Users/me/code');
    expect(bridge.folderRequest()).toBeNull();
  });
});
