import { render, screen } from '@testing-library/angular';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { FakeDeskBridge } from '../testing/fake-bridge';
import { SignedOut } from './signed-out';

const renderSignedOut = (bridge: FakeDeskBridge) => render(`<div deskSignedOut></div>`, { imports: [SignedOut], providers: bridge.providers });

describe('SignedOut', () => {
  it('says how to sign in from the terminal on this computer, with no pairing form', async () => {
    const bridge = new FakeDeskBridge();
    bridge.signOut();
    await renderSignedOut(bridge);
    expect(screen.getByRole('heading', { name: 'Open Desk from your terminal' })).toBeTruthy();
    expect(screen.queryByLabelText('Pairing code')).toBeNull();
  });

  it('pairs a phone with the code desk web pair printed', async () => {
    const bridge = new FakeDeskBridge();
    bridge.remote = true;
    bridge.signOut();
    await renderSignedOut(bridge);
    expect(screen.getByRole('heading', { name: 'Pair this phone' })).toBeTruthy();
    const button = screen.getByRole('button', { name: 'Pair this phone' }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    await userEvent.type(screen.getByLabelText('Pairing code'), ' abcd-efgh ');
    await userEvent.click(button);
    expect(bridge.pairCodes).toEqual(['abcd-efgh']);
    expect(bridge.signedOut()).toBe(false);
  });

  it('shows why a code was refused', async () => {
    const bridge = new FakeDeskBridge();
    bridge.remote = true;
    bridge.signOut();
    bridge.pairWith = () => {
      throw { code: 'bad_code', message: 'That code is wrong or has expired. Run desk web pair on your Mac for a new one.' };
    };
    await renderSignedOut(bridge);
    await userEvent.type(screen.getByLabelText('Pairing code'), 'ZZZZ-ZZZZ');
    await userEvent.click(screen.getByRole('button', { name: 'Pair this phone' }));
    expect((await screen.findByRole('alert')).textContent).toContain('That code is wrong or has expired.');
    expect(bridge.signedOut()).toBe(true);
  });
});
