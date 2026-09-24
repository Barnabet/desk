import { BrowserWindow, screen, type Rectangle } from 'electron';
import { popoverPosition } from './popoverPosition';
import { isAppUrl, rendererUrl } from './windows';

const SIZE = { width: 400, height: 620 };

/** The menu-bar popover: a small frameless window at #/tray that hides when it loses focus. */
export class TrayPopover {
  private win: BrowserWindow | null = null;

  constructor(private readonly o: { preload: string; hideOnBlur: boolean }) {}

  is(w: BrowserWindow | null | undefined): boolean {
    return !!w && w === this.win;
  }

  get visible(): boolean {
    return !!this.win && !this.win.isDestroyed() && this.win.isVisible();
  }

  get window(): BrowserWindow | null {
    return this.win;
  }

  toggle(anchor?: Rectangle): void {
    if (this.visible) this.hide();
    else this.show(anchor);
  }

  show(anchor?: Rectangle): void {
    const win = this.ensure();
    const point = anchor ? { x: anchor.x, y: anchor.y } : screen.getCursorScreenPoint();
    const work = screen.getDisplayNearestPoint(point).workArea;
    const a = anchor ?? { x: point.x, y: point.y, width: 1, height: 1 };
    const { x, y } = popoverPosition(a, SIZE, work);
    win.setBounds({ x, y, ...SIZE });
    win.show();
    win.focus();
  }

  hide(): void {
    if (this.win && !this.win.isDestroyed()) this.win.hide();
  }

  private ensure(): BrowserWindow {
    if (this.win && !this.win.isDestroyed()) return this.win;
    const win = new BrowserWindow({
      ...SIZE,
      show: false,
      frame: false,
      resizable: false,
      movable: false,
      minimizable: false,
      maximizable: false,
      fullscreenable: false,
      skipTaskbar: true,
      alwaysOnTop: true,
      title: 'Desk',
      backgroundColor: '#FBFAF7',
      webPreferences: { preload: this.o.preload, contextIsolation: true, sandbox: true, nodeIntegration: false, webSecurity: true, spellcheck: false },
    });
    win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
    win.webContents.on('will-navigate', (e, url) => {
      if (!isAppUrl(url)) e.preventDefault();
    });
    if (this.o.hideOnBlur) win.on('blur', () => this.hide());
    win.on('closed', () => {
      this.win = null;
    });
    void win.loadURL(`${rendererUrl()}#/tray`);
    this.win = win;
    return win;
  }
}
