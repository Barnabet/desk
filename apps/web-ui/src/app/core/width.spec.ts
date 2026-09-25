import { Component, viewChild, type ElementRef } from '@angular/core';
import { render, screen } from '@testing-library/angular';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { injectWidth } from './width';

@Component({ selector: 'desk-probe', template: '<div #box></div><p>{{ width() }}</p>' })
class Probe {
  private readonly box = viewChild<ElementRef<HTMLElement>>('box');
  readonly width = injectWidth(() => this.box()?.nativeElement, 900);
}

class FakeObserver {
  static last: FakeObserver | undefined;
  target: Element | undefined;
  readonly disconnect = vi.fn();
  constructor(readonly callback: () => void) {
    FakeObserver.last = this;
  }
  observe(el: Element): void {
    this.target = el;
  }
}

afterEach(() => {
  vi.unstubAllGlobals();
  FakeObserver.last = undefined;
});

describe('injectWidth', () => {
  it('falls back where layout is unknown (jsdom reports 0)', async () => {
    await render(Probe);
    expect(screen.getByText('900')).toBeTruthy();
  });

  it("follows the element's width with a ResizeObserver", async () => {
    vi.stubGlobal('ResizeObserver', FakeObserver);
    const view = await render(Probe);
    await view.fixture.whenStable();
    const observer = FakeObserver.last!;
    Object.defineProperty(observer.target!, 'clientWidth', { configurable: true, value: 640 });
    observer.callback();
    view.detectChanges();
    expect(screen.getByText('640')).toBeTruthy();
    view.fixture.destroy();
    expect(observer.disconnect).toHaveBeenCalled();
  });
});
