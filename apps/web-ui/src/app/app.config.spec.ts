import { createEnvironmentInjector, EnvironmentInjector, ɵPROVIDED_ZONELESS as PROVIDED_ZONELESS } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { describe, expect, it } from 'vitest';
import { appConfig } from './app.config';

describe('appConfig', () => {
  // TestBed is zoneless on its own in Angular 22, so no rendering spec notices appConfig losing provideZonelessChangeDetection().
  // PROVIDED_ZONELESS is the flag that provider sets (in dev mode, as specs run); Angular reads it to catch zone conflicts.
  it('bootstraps zoneless', () => {
    const parent = TestBed.inject(EnvironmentInjector);
    const app = createEnvironmentInjector(appConfig.providers, parent);
    const bare = createEnvironmentInjector([], parent);
    expect(app.get(PROVIDED_ZONELESS)).toBe(true);
    expect(bare.get(PROVIDED_ZONELESS)).toBe(false);
    app.destroy();
    bare.destroy();
  });
});
