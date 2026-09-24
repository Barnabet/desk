export type Rect = { x: number; y: number; width: number; height: number };

/**
 * Where the popover goes: centred under the tray icon (macOS menu bar), or above it when the
 * tray sits at the bottom of the screen (the Windows taskbar), kept inside the work area.
 */
export function popoverPosition(anchor: Rect, size: { width: number; height: number }, work: Rect, gap = 6): { x: number; y: number } {
  const below = anchor.y + anchor.height / 2 < work.y + work.height / 2;
  const x = Math.round(anchor.x + anchor.width / 2 - size.width / 2);
  const y = below ? anchor.y + anchor.height + gap : anchor.y - size.height - gap;
  return {
    x: Math.min(Math.max(x, work.x + gap), work.x + work.width - size.width - gap),
    y: Math.min(Math.max(y, work.y + gap), work.y + work.height - size.height - gap),
  };
}
