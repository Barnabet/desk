import { useLayoutEffect, useRef, useState, type PointerEvent, type ReactNode, type WheelEvent } from 'react';

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

/** A pannable, zoomable surface (drag the background, scroll to pan, pinch or ⌘-scroll to zoom). */
export function MapCanvas({ children, label }: { children(size: { width: number; height: number }): ReactNode; label: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 1000, height: 700 });
  const [view, setView] = useState({ x: 0, y: 0, k: 1 });
  const drag = useRef<{ px: number; py: number; x: number; y: number } | null>(null);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setSize({ width: el.clientWidth || 1000, height: el.clientHeight || 700 });
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const zoomAt = (px: number, py: number, factor: number) =>
    setView((v) => {
      const k = clamp(v.k * factor, 0.5, 2.5);
      return { k, x: px - ((px - v.x) * k) / v.k, y: py - ((py - v.y) * k) / v.k };
    });

  const onWheel = (e: WheelEvent) => {
    if (e.ctrlKey || e.metaKey) {
      const rect = ref.current!.getBoundingClientRect();
      zoomAt(e.clientX - rect.left, e.clientY - rect.top, Math.exp(-e.deltaY * 0.01));
    } else setView((v) => ({ ...v, x: v.x - e.deltaX, y: v.y - e.deltaY }));
  };
  const onPointerDown = (e: PointerEvent) => {
    if ((e.target as Element).closest('a, button')) return;
    drag.current = { px: e.clientX, py: e.clientY, x: view.x, y: view.y };
    (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
  };
  const onPointerMove = (e: PointerEvent) => {
    const d = drag.current;
    if (d) setView((v) => ({ ...v, x: d.x + e.clientX - d.px, y: d.y + e.clientY - d.py }));
  };
  const endDrag = () => {
    drag.current = null;
  };
  const moved = view.x !== 0 || view.y !== 0 || view.k !== 1;

  return (
    <div className="map-canvas" ref={ref} aria-label={label} role="group" onWheel={onWheel} onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={endDrag} onPointerCancel={endDrag}>
      <div className="map-layer" style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.k})` }}>
        {children(size)}
      </div>
      <div className="map-zoom" role="group" aria-label="Zoom">
        <button type="button" aria-label="Zoom in" onClick={() => zoomAt(size.width / 2, size.height / 2, 1.2)}>
          +
        </button>
        <button type="button" aria-label="Zoom out" onClick={() => zoomAt(size.width / 2, size.height / 2, 1 / 1.2)}>
          −
        </button>
        {moved ? (
          <button type="button" onClick={() => setView({ x: 0, y: 0, k: 1 })}>
            Reset
          </button>
        ) : null}
      </div>
    </div>
  );
}
