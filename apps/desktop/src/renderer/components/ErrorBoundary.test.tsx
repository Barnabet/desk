// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { screenKey } from '../App';
import { ErrorBoundary } from './ErrorBoundary';

afterEach(cleanup);

let broken = true;
function Flaky() {
  if (broken) throw new Error("Cannot read properties of undefined (reading 'includes')");
  return <p>Registry</p>;
}

describe('ErrorBoundary', () => {
  it('shows the error in place of a crashed screen, and Try again re-renders it', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    broken = true;
    render(
      <ErrorBoundary>
        <Flaky />
      </ErrorBoundary>,
    );
    const alert = screen.getByRole('alert');
    expect(alert.textContent).toContain('This screen hit an error');
    expect(alert.textContent).toContain("reading 'includes'");
    broken = false;
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(screen.getByText('Registry')).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('offers only a reload when the whole window failed', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    broken = true;
    render(
      <ErrorBoundary scope="whole">
        <Flaky />
      </ErrorBoundary>,
    );
    expect(screen.getByText('Desk hit an error')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Reload Desk' })).toBeTruthy();
  });

  it('resets per screen, not on navigation inside one', () => {
    expect(screenKey({ name: 'system' })).toBe('system');
    expect(screenKey({ name: 'catalog' })).toBe(screenKey({ name: 'skills', skill: 'x' }));
    expect(screenKey({ name: 'project', id: 'p', tab: 'threads', threadId: 't' })).toBe(screenKey({ name: 'project', id: 'p', tab: 'threads' }));
    expect(screenKey({ name: 'project', id: 'p', tab: 'settings' })).not.toBe(screenKey({ name: 'project', id: 'p', tab: 'threads' }));
  });
});
