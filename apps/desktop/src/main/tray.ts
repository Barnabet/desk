import { Menu, nativeImage, Tray } from 'electron';
import type { GlobalState } from '../shared/state';
import { trayIconBitmap } from './trayIcon';
import { trayModel } from './trayModel';

/** The menu-bar item. The app keeps running here after its last window closes. */
export class DeskTray {
  private readonly tray: Tray;
  title = '';

  constructor(private readonly o: { open(route?: string): void; quit(): void }) {
    const icon = nativeImage.createFromBitmap(trayIconBitmap(32), { width: 32, height: 32, scaleFactor: 2 });
    icon.setTemplateImage(true);
    this.tray = new Tray(icon);
  }

  update(state: GlobalState): void {
    const m = trayModel(state);
    this.title = m.title;
    if (process.platform === 'darwin') this.tray.setTitle(m.title ? ` ${m.title}` : '');
    this.tray.setToolTip(m.tooltip);
    this.tray.setContextMenu(
      Menu.buildFromTemplate(
        m.items.map((item) =>
          'separator' in item
            ? { type: 'separator' as const }
            : { label: item.label, enabled: item.enabled ?? true, click: () => (item.action === 'quit' ? this.o.quit() : this.o.open(item.route)) },
        ),
      ),
    );
  }
}
