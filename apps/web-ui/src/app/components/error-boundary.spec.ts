import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { screen } from '@testing-library/angular';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { screenKey } from '../screen-for';
import { ErrorBoundary, provideErrorBoundaries } from './error-boundary';

let broken = true;

@Component({ selector: 'desk-flaky', template: '<p>{{ text() }}</p>' })
class Flaky {
  text(): string {
    if (broken) throw new Error("Cannot read properties of undefined (reading 'includes')");
    return 'Registry';
  }
}

@Component({
  selector: 'desk-host',
  imports: [ErrorBoundary, Flaky],
  template: `<div deskErrorBoundary [scope]="scope()" [resetKey]="key()"><ng-template><desk-flaky /></ng-template></div>`,
})
class Host {
  readonly scope = signal<'screen' | 'whole'>('screen');
  readonly key = signal('system');
}

async function mount(scope: 'screen' | 'whole') {
  TestBed.configureTestingModule({ imports: [Host], providers: provideErrorBoundaries(), rethrowApplicationErrors: false });
  const fixture = TestBed.createComponent(Host);
  fixture.componentInstance.scope.set(scope);
  await fixture.whenStable();
  return fixture;
}

afterEach(() => vi.restoreAllMocks());

describe('ErrorBoundary', () => {
  it('shows the error in place of a crashed screen, and Try again re-renders it', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    broken = true;
    const fixture = await mount('screen');
    const alert = screen.getByRole('alert');
    expect(alert.textContent).toContain('This screen hit an error');
    expect(alert.textContent).toContain("reading 'includes'");
    expect(alert.classList.contains('error-boundary')).toBe(true);
    broken = false;
    screen.getByRole('button', { name: 'Try again' }).click();
    await fixture.whenStable();
    expect(screen.getByText('Registry')).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('offers only a reload when the whole window failed', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    broken = true;
    await mount('whole');
    expect(screen.getByText('Desk hit an error')).toBeTruthy();
    expect(screen.getByRole('alert').classList.contains('error-boundary-whole')).toBe(true);
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Reload Desk' })).toBeTruthy();
  });

  it('resets per screen, not on navigation inside one', () => {
    expect(screenKey({ name: 'system' })).toBe('system');
    expect(screenKey({ name: 'catalog' })).toBe(screenKey({ name: 'skills', skill: 'x' }));
    expect(screenKey({ name: 'project', id: 'p', tab: 'threads', threadId: 't' })).toBe(screenKey({ name: 'project', id: 'p', tab: 'threads' }));
    expect(screenKey({ name: 'project', id: 'p', tab: 'settings' })).not.toBe(screenKey({ name: 'project', id: 'p', tab: 'threads' }));
  });

  it('starts clean when the screen key changes', async () => {
    const error = vi.spyOn(console, 'error').mockImplementation(() => {});
    broken = true;
    const fixture = await mount('screen');
    expect(screen.getByRole('alert')).toBeTruthy();
    expect(error).toHaveBeenCalledWith('Desk screen error', expect.any(Error));
    broken = false;
    fixture.componentInstance.key.set('project/p/threads');
    await fixture.whenStable();
    expect(screen.getByText('Registry')).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('renders a crashed screen again when the key comes back to it, as a remounted React boundary would', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    broken = true;
    const fixture = await mount('screen');
    expect(screen.getByRole('alert')).toBeTruthy();
    broken = false;
    fixture.componentInstance.key.set('project/p/threads');
    await fixture.whenStable();
    fixture.componentInstance.key.set('system');
    await fixture.whenStable();
    expect(screen.getByText('Registry')).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
  });
});
