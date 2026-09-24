import { describe, expect, it } from 'vitest';
import { popoverPosition } from './popoverPosition';

const size = { width: 400, height: 600 };

describe('popoverPosition', () => {
  it('centres under a menu-bar icon', () => {
    expect(popoverPosition({ x: 1000, y: 0, width: 24, height: 24 }, size, { x: 0, y: 25, width: 1512, height: 920 })).toEqual({ x: 812, y: 31 });
  });

  it('stays on screen at the right edge', () => {
    expect(popoverPosition({ x: 1490, y: 0, width: 22, height: 24 }, size, { x: 0, y: 25, width: 1512, height: 920 }).x).toBe(1512 - 400 - 6);
  });

  it('opens above a taskbar icon at the bottom', () => {
    expect(popoverPosition({ x: 1700, y: 1040, width: 24, height: 40 }, size, { x: 0, y: 0, width: 1920, height: 1040 })).toEqual({ x: 1512, y: 434 });
  });
});
