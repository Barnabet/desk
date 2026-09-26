import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, sep } from 'node:path';
import { describe, expect, it } from 'vitest';
import { channels } from '@desk/bff/contract';
import { PROJECT_TABS, type Route } from '@desk/ui-core';
import { webChannels } from '@desk/web-server/contract';
import { screenFor } from './screen-for';

/** apps/web-ui/src (`ng test` runs in apps/web-ui). */
const SRC = existsSync(join(process.cwd(), 'src', 'app')) ? join(process.cwd(), 'src') : join(process.cwd(), 'apps', 'web-ui', 'src');
/** The React renderer the web UI keeps parity with (spec §6). */
const RENDERER = join(SRC, '..', '..', 'desktop', 'src', 'renderer');
/** What only a desktop host offers: the tray's "Open Desk" and reinstalling the LaunchAgent (spec §3, §6). */
const HOST_ONLY = ['app.openMain', 'daemon.repair'];
/** Every operation either UI can call: the bff's and desk web's own. */
const OPERATIONS = new Set([...Object.keys(channels), ...Object.keys(webChannels)]);

/** One or more routes of each name: a route name added to `Route` without an entry here does not compile. */
const ROUTES: { [N in Route['name']]: Array<Extract<Route, { name: N }>> } = {
  onboarding: [{ name: 'onboarding' }],
  tray: [{ name: 'tray' }],
  map: [{ name: 'map' }, { name: 'map', newProject: true }],
  attention: [{ name: 'attention' }, { name: 'attention', item: 'approval:1' }],
  skills: [{ name: 'skills' }, { name: 'skills', skill: 'global:weekly-report' }],
  catalog: [{ name: 'catalog' }, { name: 'catalog', review: 'pdf-toolkit' }],
  system: [{ name: 'system' }],
  project: PROJECT_TABS.map((tab) => ({ name: 'project' as const, id: 'p1', tab })),
};

function walk(dir: string, keep: (path: string) => boolean): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? walk(path, keep) : keep(path) ? [path] : [];
  });
}

const rel = (root: string, file: string) => relative(root, file).split(sep).join('/');
/** The web UI's components, services and templates: not its specs, not `testing/`. */
const webSources = () => walk(SRC, (p) => /\.(ts|html)$/.test(p) && !p.endsWith('.spec.ts') && !p.includes(`${sep}testing${sep}`));
/** The React renderer's modules: not its `*.test.ts(x)`, not `test/`. */
const reactSources = () => walk(RENDERER, (p) => /\.tsx?$/.test(p) && !/\.test\.tsx?$/.test(p) && !p.includes(`${sep}test${sep}`));

/** The operations a source calls: every quoted operation name in the first argument of a `call(…)`. */
function calledOps(text: string): string[] {
  const found = new Set<string>();
  for (const call of text.matchAll(/\bcall\s*(?:<[^>]*>)?\(\s*([^,)]*)/g)) {
    for (const name of call[1]!.matchAll(/(['"`])([A-Za-z][\w.]*)\1/g)) if (OPERATIONS.has(name[2]!)) found.add(name[2]!);
  }
  return [...found].sort();
}

/** Calls whose operation name is a template with a substitution: the scan cannot see which operations they reach. */
function dynamicCalls(text: string): string[] {
  return [...text.matchAll(/\bcall\s*(?:<[^>]*>)?\(\s*`[^`]*\$\{[^`]*`/g)].map((m) => m[0]);
}

const opsIn = (files: string[]) => new Set(files.flatMap((f) => calledOps(readFileSync(f, 'utf8'))));

describe('parity with the desktop app (spec §6)', () => {
  it('reads operation names from calls, and spots names built at run time', () => {
    expect(calledOps("void call('daemon.status', {}).then(setS);")).toEqual(['daemon.status']);
    expect(calledOps("this.bridge\n  .call(op === 'start' ? 'daemon.start' : 'daemon.restart', {})")).toEqual(['daemon.restart', 'daemon.start']);
    expect(calledOps("await this.bridge.call(\n  'models.replace',\n  { models },\n);")).toEqual(['models.replace']);
    expect(calledOps("call<'usage'>('usage', {}); fn.call(this, 'usage'); call(op, input); call('not.an.op', {});")).toEqual(['usage']);
    expect(dynamicCalls('setS(await call(`daemon.${what}`, {}));')).toEqual(['call(`daemon.${what}`']);
    expect(dynamicCalls("setS(await call('daemon.stop', {}));")).toEqual([]);
  });

  it('reads the sources of both UIs, and no tests', () => {
    const web = webSources().map((f) => rel(SRC, f));
    const react = reactSources().map((f) => rel(RENDERER, f));
    expect(web).toContain('app/system/system-screen.ts');
    expect(web).toContain('app/core/desk-bridge.ts');
    expect(web.filter((f) => f.endsWith('.spec.ts') || f.startsWith('app/testing/'))).toEqual([]);
    expect(react).toContain('system/SystemScreen.tsx');
    expect(react).toContain('tray/TrayPopover.tsx');
    expect(react.filter((f) => /\.test\.tsx?$/.test(f) || f.startsWith('test/'))).toEqual([]);
  });

  it('calls every operation the React renderer calls, but the desktop-only ones', () => {
    const web = opsIn(webSources());
    const missing = [...opsIn(reactSources())].filter((op) => !web.has(op) && !HOST_ONLY.includes(op));
    expect(missing).toEqual([]);
  });

  it('keeps the desktop-only list honest: the renderer calls each, the web UI none', () => {
    const react = opsIn(reactSources());
    const web = opsIn(webSources());
    expect(HOST_ONLY.filter((op) => !react.has(op))).toEqual([]);
    expect(HOST_ONLY.filter((op) => web.has(op))).toEqual([]);
  });

  it("calls every web-only operation (desk web's webChannels)", () => {
    const web = opsIn(webSources());
    expect(Object.keys(webChannels).filter((op) => !web.has(op))).toEqual([]);
  });

  it('names every operation literally, in both UIs', () => {
    const found = [
      ...reactSources().map((f) => ({ root: RENDERER, f })),
      ...webSources().map((f) => ({ root: SRC, f })),
    ].flatMap(({ root, f }) => dynamicCalls(readFileSync(f, 'utf8')).map((call) => `${rel(root, f)}: ${call}`));
    expect(found).toEqual([]);
  });

  it('shows a screen for every route but the tray', () => {
    for (const [name, routes] of Object.entries(ROUTES)) {
      for (const route of routes) {
        if (name === 'tray') expect(screenFor(route)).toBeNull();
        else expect(screenFor(route), JSON.stringify(route)).not.toBeNull();
      }
    }
  });
});
