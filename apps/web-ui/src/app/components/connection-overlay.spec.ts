import { fireEvent, render, screen, waitFor } from '@testing-library/angular';
import { describe, expect, it } from 'vitest';
import { initialGlobalState, type ConnectionStatus, type GlobalState } from '@desk/bff/contract';
import { FakeDeskBridge, provideGlobal } from '../testing/fake-bridge';
import { ConnectionOverlay } from './connection-overlay';

const at = (status: ConnectionStatus, detail?: string): GlobalState => ({ ...initialGlobalState(), connection: detail ? { status, detail } : { status } });

async function renderOverlay(state: GlobalState, bridge = new FakeDeskBridge()) {
  await render(ConnectionOverlay, { providers: [...bridge.providers, provideGlobal(state)] });
  return bridge;
}

describe('ConnectionOverlay', () => {
  it('says nothing while deskd is live', async () => {
    await renderOverlay(at('live'));
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('shows a calm banner while deskd reconnects', async () => {
    await renderOverlay(at('reconnecting'));
    const banner = screen.getByRole('status');
    expect(banner.classList.contains('banner')).toBe(true);
    expect(banner.textContent).toBe('Reconnecting to deskd… Your threads keep running.');
  });

  it('offers Start Desk when deskd is down, and says why starting failed', async () => {
    const bridge = await renderOverlay(
      at('offline'),
      new FakeDeskBridge({
        'daemon.start': () => {
          throw { code: 'daemon_not_running', message: 'deskd did not answer within 15 s.' };
        },
      }),
    );
    const dialog = screen.getByRole('alertdialog', { name: 'Desk isn’t running' });
    expect(dialog.classList.contains('overlay')).toBe(true);
    expect(dialog.textContent).toContain('Your projects and threads are safe. Start the daemon to pick up where they left off.');
    fireEvent.click(screen.getByRole('button', { name: 'Start Desk' }));
    expect((await screen.findByRole('alert')).textContent).toBe('deskd did not answer within 15 s.');
    expect(bridge.calls.map((c) => c.channel)).toEqual(['daemon.start']);
  });

  it('offers a restart when deskd speaks another protocol', async () => {
    const bridge = await renderOverlay(at('mismatch', 'deskd speaks protocol 3; this Desk needs 4.'), new FakeDeskBridge({ 'daemon.restart': () => ({ running: true }) }));
    const dialog = screen.getByRole('alertdialog', { name: 'Desk and deskd are out of step' });
    expect(dialog.textContent).toContain('deskd speaks protocol 3; this Desk needs 4. Restart the daemon from this install, or update Desk.');
    const restart = screen.getByRole('button', { name: 'Restart deskd' });
    fireEvent.click(restart);
    expect(restart.getAttribute('aria-busy')).toBe('true');
    // The call is recorded at once; wait until it has settled before saying no error came back.
    await waitFor(() => expect(restart.hasAttribute('aria-busy')).toBe(false));
    expect(bridge.calls).toEqual([{ channel: 'daemon.restart', input: {} }]);
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('reveals the logs', async () => {
    const bridge = await renderOverlay(at('offline'), new FakeDeskBridge({ 'app.revealLogs': () => ({ ok: true }) }));
    fireEvent.click(screen.getByRole('button', { name: 'Reveal logs' }));
    expect(bridge.calls).toEqual([{ channel: 'app.revealLogs', input: {} }]);
  });

  it('says when the page lost desk web itself', async () => {
    const bridge = new FakeDeskBridge();
    bridge.setPushStatus('reconnecting');
    await renderOverlay(at('live'), bridge);
    expect(screen.getByRole('status').textContent).toBe('Reconnecting to desk web… If you stopped it, run desk web again.');
    expect(screen.queryByRole('alertdialog')).toBeNull();
  });

  it('says desk web is gone rather than offering Start while the stale state still reads deskd offline', async () => {
    const bridge = new FakeDeskBridge();
    bridge.setPushStatus('reconnecting');
    await renderOverlay(at('offline'), bridge);
    expect(screen.getByRole('status').textContent).toBe('Reconnecting to desk web… If you stopped it, run desk web again.');
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Start Desk' })).toBeNull();
  });
});
