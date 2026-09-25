import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it } from 'vitest';
import { LastProject } from './last-project';

beforeEach(() => localStorage.clear());

describe('LastProject', () => {
  it('remembers the last project across reloads, and forgets it on reset', () => {
    TestBed.inject(LastProject).remember('p1');
    expect(localStorage.getItem('desk.lastProject')).toBe('p1');
    TestBed.resetTestingModule();
    const again = TestBed.inject(LastProject);
    expect(again.id()).toBe('p1');
    again.reset();
    expect(again.id()).toBeNull();
    expect(localStorage.getItem('desk.lastProject')).toBeNull();
  });
});
