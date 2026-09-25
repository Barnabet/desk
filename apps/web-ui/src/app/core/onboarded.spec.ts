import { beforeEach, describe, expect, it } from 'vitest';
import { isOnboarded, markOnboarded } from './onboarded';

beforeEach(() => localStorage.clear());

describe('the onboarding flag', () => {
  it('is off until onboarding marks it, under the desktop app key', () => {
    expect(isOnboarded()).toBe(false);
    markOnboarded();
    expect(isOnboarded()).toBe(true);
    expect(localStorage.getItem('desk.onboarded')).toBe('1');
  });
});
