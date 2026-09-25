import { BrowserWindow, nativeTheme } from 'electron';
import type { Appearance } from '../shared/ipc';

/** Window fills matching `--ground` (main) and `--soft` (tray popover), so a window never flashes the other theme. */
const FILL = { main: { light: '#EFEAE0', dark: '#171613' }, popover: { light: '#FBFAF7', dark: '#22201C' } } as const;

export function windowFill(kind: keyof typeof FILL): string {
  return FILL[kind][nativeTheme.shouldUseDarkColors ? 'dark' : 'light'];
}

/** Applies the Appearance setting. Renderers theme themselves from prefers-color-scheme, which follows `themeSource`. */
export function applyAppearance(appearance: Appearance): void {
  nativeTheme.themeSource = appearance;
}

/** Repaints native window fills when the theme flips: a setting change, or macOS switching under `system`. */
export function repaintOnThemeChange(isPopover: (w: BrowserWindow) => boolean): void {
  nativeTheme.on('updated', () => {
    for (const w of BrowserWindow.getAllWindows()) if (!w.isDestroyed()) w.setBackgroundColor(windowFill(isPopover(w) ? 'popover' : 'main'));
  });
}
