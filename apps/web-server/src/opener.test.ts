import { describe, expect, it } from 'vitest';
import { openerCommand } from './opener';

describe('openerCommand', () => {
  it("uses the platform's opener with the target as its only argument", () => {
    expect(openerCommand('/tmp/Desk logs', 'darwin')).toEqual(['open', ['/tmp/Desk logs']]);
    expect(openerCommand('C:\\Desk\\logs', 'win32')).toEqual(['explorer.exe', ['C:\\Desk\\logs']]);
    expect(openerCommand('/home/u/.local/share/desk/logs', 'linux')).toEqual(['xdg-open', ['/home/u/.local/share/desk/logs']]);
  });
});
