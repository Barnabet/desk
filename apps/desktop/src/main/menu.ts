import { Menu, type MenuItemConstructorOptions } from 'electron';

export function buildAppMenu(o: { navigate(route: string): void; dev: boolean }): Menu {
  const mac = process.platform === 'darwin';
  const go = (label: string, accelerator: string, route: string): MenuItemConstructorOptions => ({ label, accelerator, click: () => o.navigate(route) });
  const template: MenuItemConstructorOptions[] = [
    ...(mac ? [{ role: 'appMenu' } as MenuItemConstructorOptions] : []),
    { label: 'File', submenu: [go('New Project…', 'CmdOrCtrl+N', '#/map?new=1'), { type: 'separator' }, mac ? { role: 'close' } : { role: 'quit' }] },
    { role: 'editMenu' },
    {
      label: 'View',
      submenu: [...(o.dev ? ([{ role: 'reload' }, { role: 'toggleDevTools' }, { type: 'separator' }] as MenuItemConstructorOptions[]) : []), { role: 'togglefullscreen' }],
    },
    { label: 'Go', submenu: [go('Map', 'CmdOrCtrl+1', '#/map'), go('Attention', 'CmdOrCtrl+2', '#/attention'), go('Skills', 'CmdOrCtrl+3', '#/skills'), go('System', 'CmdOrCtrl+4', '#/system')] },
    { role: 'windowMenu' },
  ];
  return Menu.buildFromTemplate(template);
}
