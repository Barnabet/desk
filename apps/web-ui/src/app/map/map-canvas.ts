import { ChangeDetectionStrategy, Component, DestroyRef, ElementRef, ViewEncapsulation, afterNextRender, computed, inject, input, output, signal } from '@angular/core';

/** A canvas size in CSS pixels. */
export type CanvasSize = { width: number; height: number };

/** The size MapCanvas assumes until it has measured itself, and keeps where nothing has a size (jsdom). */
export const DEFAULT_CANVAS_SIZE: CanvasSize = { width: 1000, height: 700 };

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

/**
 * A pannable, zoomable surface (drag the background, scroll to pan, pinch or ⌘-scroll to zoom), ported from
 * MapCanvas.tsx. The content is projected into the moving `.map-layer`. React handed the measured size to a render
 * prop; here the parent gets it from `resized`, emitted whenever it differs from the last size (the first one being
 * DEFAULT_CANVAS_SIZE). A browser zooms the whole page on a pinch or ⌘-scroll, so those wheel events are cancelled.
 */
@Component({
  selector: 'div[deskMapCanvas]',
  encapsulation: ViewEncapsulation.None,
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    class: 'map-canvas',
    role: 'group',
    '[attr.aria-label]': 'label()',
    '[attr.label]': 'null',
    '(wheel)': 'onWheel($event)',
    '(pointerdown)': 'onPointerDown($event)',
    '(pointermove)': 'onPointerMove($event)',
    '(pointerup)': 'endDrag()',
    '(pointercancel)': 'endDrag()',
  },
  template: `
    <div class="map-layer" [style.transform]="transform()"><ng-content /></div>
    <div class="map-zoom" role="group" aria-label="Zoom">
      <button type="button" aria-label="Zoom in" (click)="zoomBy(1.2)">+</button>
      <button type="button" aria-label="Zoom out" (click)="zoomBy(1 / 1.2)">−</button>
      @if (moved()) {
        <button type="button" (click)="reset()">Reset</button>
      }
    </div>
  `,
})
export class MapCanvas {
  /** The surface's accessible name. */
  readonly label = input.required<string>();
  /** The measured size, each time it changes. */
  readonly resized = output<CanvasSize>();

  private readonly host: HTMLElement = inject<ElementRef<HTMLElement>>(ElementRef).nativeElement;
  private readonly size = signal<CanvasSize>(DEFAULT_CANVAS_SIZE);
  private readonly view = signal({ x: 0, y: 0, k: 1 });
  private drag: { px: number; py: number; x: number; y: number } | null = null;

  protected readonly transform = computed(() => {
    const v = this.view();
    return `translate(${v.x}px, ${v.y}px) scale(${v.k})`;
  });
  protected readonly moved = computed(() => {
    const v = this.view();
    return v.x !== 0 || v.y !== 0 || v.k !== 1;
  });

  constructor() {
    const destroyRef = inject(DestroyRef);
    afterNextRender(() => {
      const measure = () => {
        const next = { width: this.host.clientWidth || DEFAULT_CANVAS_SIZE.width, height: this.host.clientHeight || DEFAULT_CANVAS_SIZE.height };
        const current = this.size();
        if (next.width === current.width && next.height === current.height) return;
        this.size.set(next);
        this.resized.emit(next);
      };
      measure();
      if (typeof ResizeObserver === 'undefined') return;
      const ro = new ResizeObserver(measure);
      ro.observe(this.host);
      destroyRef.onDestroy(() => ro.disconnect());
    });
  }

  private zoomAt(px: number, py: number, factor: number): void {
    this.view.update((v) => {
      const k = clamp(v.k * factor, 0.5, 2.5);
      return { k, x: px - ((px - v.x) * k) / v.k, y: py - ((py - v.y) * k) / v.k };
    });
  }

  protected zoomBy(factor: number): void {
    const { width, height } = this.size();
    this.zoomAt(width / 2, height / 2, factor);
  }

  protected reset(): void {
    this.view.set({ x: 0, y: 0, k: 1 });
  }

  protected onWheel(e: WheelEvent): void {
    if (e.ctrlKey || e.metaKey) {
      e.preventDefault();
      const rect = this.host.getBoundingClientRect();
      this.zoomAt(e.clientX - rect.left, e.clientY - rect.top, Math.exp(-e.deltaY * 0.01));
    } else this.view.update((v) => ({ ...v, x: v.x - e.deltaX, y: v.y - e.deltaY }));
  }

  protected onPointerDown(e: PointerEvent): void {
    if ((e.target as Element).closest('a, button')) return;
    const v = this.view();
    this.drag = { px: e.clientX, py: e.clientY, x: v.x, y: v.y };
    // jsdom has no pointer capture.
    (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
  }

  protected onPointerMove(e: PointerEvent): void {
    const d = this.drag;
    if (d) this.view.update((v) => ({ ...v, x: d.x + e.clientX - d.px, y: d.y + e.clientY - d.py }));
  }

  protected endDrag(): void {
    this.drag = null;
  }
}
