import { TestBed } from '@angular/core/testing';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { NowService } from './now.service';

afterEach(() => vi.useRealTimers());

describe('NowService', () => {
  it('is the current time, refreshed every 15 seconds', () => {
    vi.useFakeTimers({ now: new Date('2026-09-25T10:00:00.000Z') });
    const now = TestBed.inject(NowService).now;
    expect(now()).toBe(Date.parse('2026-09-25T10:00:00.000Z'));
    vi.advanceTimersByTime(14_999);
    expect(now()).toBe(Date.parse('2026-09-25T10:00:00.000Z'));
    vi.advanceTimersByTime(1);
    expect(now()).toBe(Date.parse('2026-09-25T10:00:15.000Z'));
  });
});
