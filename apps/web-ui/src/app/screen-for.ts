import type { Type } from '@angular/core';
import type { Route } from '@desk/ui-core';
import { AttentionScreen } from './attention/attention-screen';
import { ConversationScreen } from './conversation/conversation-screen';
import { LibraryScreen } from './knowledge/library-screen';
import { MemoryScreen } from './knowledge/memory-screen';
import { MapScreen } from './map/map-screen';
import { Onboarding } from './screens/onboarding';
import { SettingsScreen } from './settings/settings-screen';
import { SkillsScreen } from './skills/skills-screen';
import { SystemScreen } from './system/system-screen';
import { ThreadsScreen } from './threads/threads-screen';

/** A screen for App to render with NgComponentOutlet: the component, and the inputs this route gives it. */
export type ScreenView = { component: Type<unknown>; inputs: Record<string, unknown> };

/** Which screen a route shows: a crashed screen resets when this changes, not on navigation inside one; App remounts on it. */
export function screenKey(route: Route): string {
  if (route.name === 'project') return `project/${route.id}/${route.tab}`;
  return route.name === 'catalog' ? 'skills' : route.name;
}

/**
 * The screen each route shows, with the inputs the desktop's `Screen` switch (App.tsx) passes. Every input key is present
 * even when undefined: NgComponentOutlet resets an input that disappears from its inputs object. The tray is the
 * desktop's menu-bar popover and has no web screen: App sends it to the map. `parity.spec.ts` checks every route name.
 */
export function screenFor(route: Route): ScreenView | null {
  switch (route.name) {
    case 'tray':
      return null;
    case 'onboarding':
      return { component: Onboarding, inputs: {} };
    case 'map':
      return { component: MapScreen, inputs: { newProject: route.newProject ?? false } };
    case 'attention':
      return { component: AttentionScreen, inputs: { itemId: route.item } };
    case 'skills':
      return { component: SkillsScreen, inputs: { skill: route.skill, catalog: false, review: undefined } };
    case 'catalog':
      return { component: SkillsScreen, inputs: { skill: undefined, catalog: true, review: route.review } };
    case 'system':
      return { component: SystemScreen, inputs: {} };
    case 'project':
      switch (route.tab) {
        case 'conversation':
          return { component: ConversationScreen, inputs: { projectId: route.id, at: route.at } };
        case 'threads':
          return { component: ThreadsScreen, inputs: { projectId: route.id, threadId: route.threadId, at: route.at } };
        case 'library':
          return { component: LibraryScreen, inputs: { projectId: route.id, file: route.file } };
        case 'memory':
          return { component: MemoryScreen, inputs: { projectId: route.id, q: route.q } };
        case 'settings':
          return { component: SettingsScreen, inputs: { projectId: route.id } };
      }
  }
}
