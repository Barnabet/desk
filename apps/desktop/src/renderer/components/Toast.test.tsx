// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { DeskCallError } from '../bridge';
import { installBridge } from '../test/bridge';
import { describeError, toastError, Toaster } from './Toast';

afterEach(cleanup);

describe('toasts', () => {
  it('describes errors, offering the logs for server failures only', () => {
    expect(describeError(new DeskCallError({ code: 'conflict', message: 'Already decided', status: 409 }))).toEqual({ message: 'Already decided', revealLogs: false });
    expect(describeError(new DeskCallError({ code: 'internal', message: 'Something went wrong' }))).toEqual({ message: 'Something went wrong', revealLogs: true });
    expect(describeError(new DeskCallError({ code: 'x', message: 'boom', status: 502 })).revealLogs).toBe(true);
  });

  it('shows a toast with a Reveal logs action', () => {
    const bridge = installBridge({ 'app.revealLogs': () => ({ ok: true }) });
    render(<Toaster />);
    act(() => toastError(new DeskCallError({ code: 'internal', message: 'Something went wrong' })));
    expect(screen.getByText('Something went wrong')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Reveal logs' }));
    expect(bridge.calls.map((c) => c.channel)).toEqual(['app.revealLogs']);
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(screen.queryByText('Something went wrong')).toBeNull();
  });
});
