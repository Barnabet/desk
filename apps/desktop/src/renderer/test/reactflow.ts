/** jsdom lacks what React Flow measures with. These shims (after React Flow's testing guide) let canvases render in component tests. */
class ResizeObserverShim {
  constructor(private readonly cb: ResizeObserverCallback) {}
  observe(target: Element): void {
    // React Flow's pan-zoom reads the entry's contentRect (@xyflow/system's extent observer).
    const contentRect = { x: 0, y: 0, top: 0, left: 0, width: 800, height: 600, right: 800, bottom: 600 } as DOMRectReadOnly;
    this.cb([{ target, contentRect } as ResizeObserverEntry], this as unknown as ResizeObserver);
  }
  unobserve(): void {}
  disconnect(): void {}
}

class DOMMatrixReadOnlyShim {
  m22: number;
  constructor(transform?: string) {
    const scale = transform?.match(/scale\(([0-9.]+)\)/)?.[1];
    this.m22 = scale !== undefined ? Number(scale) : 1;
  }
}

let installed = false;

export function installReactFlowShims(): void {
  if (installed) return;
  installed = true;
  globalThis.ResizeObserver = ResizeObserverShim as unknown as typeof ResizeObserver;
  (globalThis as { DOMMatrixReadOnly?: unknown }).DOMMatrixReadOnly = DOMMatrixReadOnlyShim;
  Object.defineProperties(HTMLElement.prototype, {
    offsetHeight: { configurable: true, get: () => 64 },
    offsetWidth: { configurable: true, get: () => 200 },
  });
  (SVGElement.prototype as unknown as { getBBox(): DOMRect }).getBBox = () => ({ x: 0, y: 0, width: 0, height: 0 }) as DOMRect;
}
