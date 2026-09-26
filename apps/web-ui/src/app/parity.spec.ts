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

/** `text` without its comments. Strings (quoted or template) are matched first and kept, so a `//` inside one stays. */
function withoutComments(text: string): string {
  return text.replace(/('(?:\\.|[^'\\\n])*'|"(?:\\.|[^"\\\n])*"|`(?:\\.|[^`\\])*`)|\/\/[^\n]*|\/\*[\s\S]*?\*\//g, (_comment, kept?: string) => kept ?? ' ');
}

/** Every `call(…)` outside comments (type arguments may nest once): the text matched and its first argument, up to its first `,` or `)`. */
function callsIn(text: string): Array<{ call: string; first: string }> {
  return [...withoutComments(text).matchAll(/\bcall\s*(?:<(?:[^<>]|<[^<>]*>)*>)?\(\s*([^,)]*)/g)].map((m) => ({ call: m[0], first: m[1]!.trim() }));
}

/** The names a first argument holds when the scan can read it: one quoted name, or a conditional between two; else null. */
function namesIn(first: string): string[] | null {
  const one = /^(['"`])([A-Za-z][\w.]*)\1$/.exec(first);
  if (one) return [one[2]!];
  const either = /^[^?]+\?\s*(['"`])([A-Za-z][\w.]*)\1\s*:\s*(['"`])([A-Za-z][\w.]*)\3$/.exec(first);
  return either ? [either[2]!, either[4]!] : null;
}

/** A declaration's first parameter (`op: C`, `op?: C`: signatures and overloads), or `fn.call(this | null, …)`: no operation call. */
const notACall = (first: string) => /^[A-Za-z_$][\w$]*\??:/.test(first) || first === 'this' || first === 'null';

/** The operations a source calls: the names of every readable first argument of a `call(…)` that `channels` or `webChannels` has. */
function calledOps(text: string): string[] {
  const found = new Set<string>();
  for (const { first } of callsIn(text)) for (const name of namesIn(first) ?? []) if (OPERATIONS.has(name)) found.add(name);
  return [...found].sort();
}

/**
 * Calls the scan cannot read: every `call(…)` whose first argument is neither one quoted operation name nor a
 * conditional between two, declarations and `this`/`null` aside. A name built at run time (a template, a variable,
 * a table, a call) lands here, and so does a quoted name that is no operation.
 */
function dynamicCalls(text: string): string[] {
  return callsIn(text)
    .filter(({ first }) => !notACall(first) && !(namesIn(first)?.every((name) => OPERATIONS.has(name)) ?? false))
    .map(({ call }) => call);
}

const opsIn = (files: string[]) => new Set(files.flatMap((f) => calledOps(readFileSync(f, 'utf8'))));

describe('parity with the desktop app (spec §6)', () => {
  it('reads operation names from calls, ignores comments, and flags any name it cannot read', () => {
    expect(calledOps("void call('daemon.status', {}).then(setS);")).toEqual(['daemon.status']);
    expect(calledOps("this.bridge\n  .call(op === 'start' ? 'daemon.start' : 'daemon.restart', {})")).toEqual(['daemon.restart', 'daemon.start']);
    expect(calledOps("await this.bridge.call(\n  'models.replace',\n  { models },\n);")).toEqual(['models.replace']);
    expect(calledOps("call<'usage'>('usage', {}); fn.call(this, 'usage'); call(op, input); call('not.an.op', {});")).toEqual(['usage']);
    expect(calledOps("call<ChannelOutput<'usage'>>('usage', {});")).toEqual(['usage']);
    expect(calledOps("// React: this.bridge.call('projects.archive', { id })\n/* call('projects.delete', {}) */")).toEqual([]);
    expect(dynamicCalls('setS(await call(`daemon.${what}`, {}));')).toEqual(['call(`daemon.${what}`']);
    expect(dynamicCalls("setS(await call('daemon.stop', {}));")).toEqual([]);
    expect(dynamicCalls("this.bridge.call(op === 'start' ? 'daemon.start' : 'daemon.restart', {})")).toEqual([]);
    for (const text of [
      "const op = what === 'stop' ? 'daemon.stop' : 'daemon.repair'; call(op, {})",
      "const OPS = { stop: 'daemon.stop', start: 'daemon.start' } as const; call(OPS[what], {})",
      "const f = (op: 'daemon.stop' | 'daemon.start') => call(op, {})",
      "call(isOn() ? 'daemon.stop' : 'daemon.repair', {})",
      "call('not.an.op', {})",
    ]) {
      expect(dynamicCalls(text), text).toHaveLength(1);
    }
    expect(dynamicCalls('call<C extends Channel>(op: C, input: ChannelInput<C>): Promise<ChannelOutput<C>>;\ncall(op?: string): void;')).toEqual([]);
    expect(dynamicCalls("fn.call(this, 'usage'); fn.call(null, 1); // this.bridge.call(op, {})")).toEqual([]);
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
