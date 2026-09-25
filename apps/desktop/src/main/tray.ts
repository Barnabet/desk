import { Menu, nativeImage, Tray, type Rectangle } from 'electron';
import type { GlobalState } from '@desk/bff/contract';
import { trayIconBitmap } from './trayIcon';
import { trayModel } from './trayModel';

/** The menu-bar item. The app keeps running here after its last window closes. */
export class DeskTray {
  private readonly tray: Tray;
  private menu: Menu | null = null;
  title = '';

  /** Left click toggles the popover; right click (or ctrl-click) shows the menu. */
  constructor(private readonly o: { open(route?: string): void; quit(): void; toggle(bounds: Rectangle): void }) {
    const icon = nativeImage.createFromBitmap(trayIconBitmap(32), { width: 32, height: 32, scaleFactor: 2 });
    icon.setTemplateImage(true);
    this.tray = new Tray(icon);
    this.tray.on('click', (e, bounds) => (e.ctrlKey ? this.popUpMenu() : this.o.toggle(bounds)));
    this.tray.on('right-click', () => this.popUpMenu());
  }

  private popUpMenu(): void {
    if (this.menu) this.tray.popUpContextMenu(this.menu);
  }

  update(state: GlobalState): void {
    const m = trayModel(state);
    this.title = m.title;
    if (process.platform === 'darwin') this.tray.setTitle(m.title ? ` ${m.title}` : '');
    this.tray.setToolTip(m.tooltip);
    this.menu = Menu.buildFromTemplate(
      m.items.map((item) =>
        'separator' in item
          ? { type: 'separator' as const }
          : { label: item.label, enabled: item.enabled ?? true, click: () => (item.action === 'quit' ? this.o.quit() : this.o.open(item.route)) },
      ),
    );
  }
}
