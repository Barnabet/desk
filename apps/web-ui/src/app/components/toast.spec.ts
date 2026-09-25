import { TestBed } from '@angular/core/testing';
import { fireEvent, render, screen } from '@testing-library/angular';
import { describe, expect, it } from 'vitest';
import { DeskCallError } from '../core/desk-bridge';
import { FakeDeskBridge } from '../testing/fake-bridge';
import { describeError, Toaster, ToastService } from './toast';

describe('toasts', () => {
  it('describes errors, offering the logs for server failures only', () => {
    expect(describeError(new DeskCallError({ code: 'conflict', message: 'Already decided', status: 409 }))).toEqual({ message: 'Already decided', revealLogs: false });
    expect(describeError(new DeskCallError({ code: 'internal', message: 'Something went wrong' }))).toEqual({ message: 'Something went wrong', revealLogs: true });
    expect(describeError(new DeskCallError({ code: 'x', message: 'boom', status: 502 })).revealLogs).toBe(true);
  });

  it('shows a toast with a Reveal logs action', async () => {
    const bridge = new FakeDeskBridge({ 'app.revealLogs': () => ({ ok: true }) });
    const view = await render(Toaster, { providers: bridge.providers });
    TestBed.inject(ToastService).error(new DeskCallError({ code: 'internal', message: 'Something went wrong' }));
    view.detectChanges();
    expect(screen.getByText('Something went wrong')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Reveal logs' }));
    expect(bridge.calls.map((c) => c.channel)).toEqual(['app.revealLogs']);
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(screen.queryByText('Something went wrong')).toBeNull();
  });
});
