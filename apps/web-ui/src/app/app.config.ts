import { provideZonelessChangeDetection, type ApplicationConfig } from '@angular/core';
import { provideErrorBoundaries } from './components/error-boundary';

/** The web UI's root providers (main.ts bootstraps App with them). */
export const appConfig: ApplicationConfig = {
  providers: [provideZonelessChangeDetection(), ...provideErrorBoundaries()],
};
