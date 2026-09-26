import { Component } from '@angular/core';
import { render } from '@testing-library/angular';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { injectMediaQuery } from './media';

@Component({ selector: 'desk-probe', template: '{{ narrow() }}' })
class Probe {
  readonly narrow = injectMediaQuery('(max-width: 1279px)');
}

afterEach(() => Reflect.deleteProperty(window, 'matchMedia'));

describe('injectMediaQuery', () => {
  it('is false where matchMedia is missing (jsdom)', async () => {
    const view = await render(Probe);
    expect(view.container.textContent).toBe('false');
  });

  it('needs an injection context, also where matchMedia is missing', () => {
    expect(() => injectMediaQuery('(max-width: 1279px)')).toThrow(/NG0203/);
  });

  it('follows the query until the component goes away', async () => {
    let onChange: (() => void) | undefined;
    const mql = { matches: true, addEventListener: (_: string, l: () => void) => (onChange = l), removeEventListener: vi.fn() };
    const matchMedia = vi.fn(() => mql);
    Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: matchMedia });
    const view = await render(Probe);
    expect(matchMedia).toHaveBeenCalledWith('(max-width: 1279px)');
    expect(view.container.textContent).toBe('true');
    mql.matches = false;
    onChange?.();
    view.detectChanges();
    expect(view.container.textContent).toBe('false');
    view.fixture.destroy();
    expect(mql.removeEventListener).toHaveBeenCalledWith('change', onChange);
  });
});
