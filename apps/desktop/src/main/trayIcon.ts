/**
 * The menu-bar glyph from design C (a ring with a centre dot, drawn on a 14-unit grid) as a BGRA
 * template bitmap: black with coverage in alpha, so macOS tints it for light and dark menu bars.
 */
export function trayIconBitmap(size: number): Buffer {
  const buf = Buffer.alloc(size * size * 4);
  const unit = size / 14;
  const c = size / 2;
  const clamp = (v: number) => Math.max(0, Math.min(1, v));
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const d = Math.hypot(x + 0.5 - c, y + 0.5 - c) / unit;
      const ring = clamp((0.75 - Math.abs(d - 5.5)) * unit + 0.5);
      const dot = clamp((2 - d) * unit + 0.5);
      buf[(y * size + x) * 4 + 3] = Math.round(Math.max(ring, dot) * 255);
    }
  }
  return buf;
}
