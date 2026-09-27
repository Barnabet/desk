import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { PROJECT_TABS } from '@desk/ui-core';
import { AttentionScreen } from './attention/attention-screen';
import { AutomationsPlaceholder } from './automations/automations-placeholder';
import { ConversationScreen } from './conversation/conversation-screen';
import { LibraryScreen } from './knowledge/library-screen';
import { MemoryScreen } from './knowledge/memory-screen';
import { MapScreen } from './map/map-screen';
import { screenFor, screenKey } from './screen-for';
import { Onboarding } from './screens/onboarding';
import { SettingsScreen } from './settings/settings-screen';
import { SkillsScreen } from './skills/skills-screen';
import { SystemScreen } from './system/system-screen';
import { ThreadsScreen } from './threads/threads-screen';

/** apps/web-ui/src/app (`ng test` runs in apps/web-ui). */
const APP = existsSync(join(process.cwd(), 'src', 'app')) ? join(process.cwd(), 'src', 'app') : join(process.cwd(), 'apps', 'web-ui', 'src', 'app');

describe('screenFor', () => {
  it('names the screen, with the inputs the desktop passes, for every route but the tray', () => {
    expect(screenFor({ name: 'tray' })).toBeNull();
    expect(screenFor({ name: 'onboarding' })).toEqual({ component: Onboarding, inputs: {} });
    expect(screenFor({ name: 'map', newProject: true })).toEqual({ component: MapScreen, inputs: { newProject: true } });
    expect(screenFor({ name: 'map' })).toEqual({ component: MapScreen, inputs: { newProject: false } });
    expect(screenFor({ name: 'attention', item: 'approval:1' })).toEqual({ component: AttentionScreen, inputs: { itemId: 'approval:1' } });
    expect(screenFor({ name: 'skills', skill: 'global:weekly-report' })).toEqual({ component: SkillsScreen, inputs: { skill: 'global:weekly-report', catalog: false, review: undefined } });
    expect(screenFor({ name: 'catalog', review: 'pdf-toolkit' })).toEqual({ component: SkillsScreen, inputs: { skill: undefined, catalog: true, review: 'pdf-toolkit' } });
    expect(screenFor({ name: 'system' })).toEqual({ component: SystemScreen, inputs: {} });
    expect(screenFor({ name: 'project', id: 'p', tab: 'conversation', at: 3 })).toEqual({ component: ConversationScreen, inputs: { projectId: 'p', at: 3 } });
    expect(screenFor({ name: 'project', id: 'p', tab: 'threads', threadId: 't', at: 7 })).toEqual({ component: ThreadsScreen, inputs: { projectId: 'p', threadId: 't', at: 7 } });
    expect(screenFor({ name: 'project', id: 'p', tab: 'automations', automationId: 'a1', view: 'runs' })).toEqual({ component: AutomationsPlaceholder, inputs: { projectId: 'p' } });
    expect(screenFor({ name: 'project', id: 'p', tab: 'library', file: 'notes/a.md' })).toEqual({ component: LibraryScreen, inputs: { projectId: 'p', file: 'notes/a.md' } });
    expect(screenFor({ name: 'project', id: 'p', tab: 'memory', q: 'auth' })).toEqual({ component: MemoryScreen, inputs: { projectId: 'p', q: 'auth' } });
    expect(screenFor({ name: 'project', id: 'p', tab: 'settings' })).toEqual({ component: SettingsScreen, inputs: { projectId: 'p' } });
    for (const tab of PROJECT_TABS) expect(screenFor({ name: 'project', id: 'p', tab }), tab).not.toBeNull();
  });

  it('keys screens by place, so moving inside one keeps it', () => {
    expect(screenKey({ name: 'system' })).toBe('system');
    expect(screenKey({ name: 'catalog', review: 'x' })).toBe(screenKey({ name: 'skills', skill: 'global:x' }));
    expect(screenKey({ name: 'attention', item: 'a' })).toBe('attention');
    expect(screenKey({ name: 'project', id: 'p', tab: 'threads', threadId: 't' })).toBe('project/p/threads');
    expect(screenKey({ name: 'project', id: 'q', tab: 'threads' })).not.toBe(screenKey({ name: 'project', id: 'p', tab: 'threads' }));
  });

  it('leaves no placeholder screen behind', () => {
    expect(existsSync(join(APP, 'screens', 'not-yet.ts'))).toBe(false);
    expect(readFileSync(join(APP, 'screen-for.ts'), 'utf8')).not.toMatch(/notYet|NotYet/);
  });
});
