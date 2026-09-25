import type { Type } from '@angular/core';
import type { Route } from '@desk/ui-core';
import { NotYet } from './screens/not-yet';

/** A screen for App to render with NgComponentOutlet: the component, and the inputs this route gives it. */
export type ScreenView = { component: Type<unknown>; inputs: Record<string, unknown> };

/** Which screen a route shows: a crashed screen resets when this changes, not on navigation inside one; App remounts on it. */
export function screenKey(route: Route): string {
  if (route.name === 'project') return `project/${route.id}/${route.tab}`;
  return route.name === 'catalog' ? 'skills' : route.name;
}

const notYet = (label: string): ScreenView => ({ component: NotYet, inputs: { label } });

/**
 * The screen each route shows, with the inputs the desktop's `Screen` switch (App.tsx) passes. Screens arrive phase by
 * phase (spec §6); until then a route shows NotYet. The tray has no web screen: App sends it to the map.
 */
export function screenFor(route: Route): ScreenView | null {
  switch (route.name) {
    case 'tray':
      return null;
    case 'onboarding':
      return notYet('Onboarding');
    case 'map':
      return notYet('The map');
    case 'attention':
      return notYet('Attention');
    case 'skills':
      return notYet('Skills');
    case 'catalog':
      return notYet('The skill catalog');
    case 'system':
      return notYet('System');
    case 'project':
      switch (route.tab) {
        case 'conversation':
          return notYet('Conversation');
        case 'threads':
          return notYet('Threads');
        case 'library':
          return notYet('Library');
        case 'memory':
          return notYet('Memory');
        case 'settings':
          return notYet('Project settings');
      }
  }
}
