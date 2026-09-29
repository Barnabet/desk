import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { openDb } from '../db/open';
import { EventStore } from '../events/store';
import { BuiltinSkills, loadBuiltinsManifest } from '../skills/builtins';
import { SkillStore } from '../skills/store';
import { testServices, testToolContext } from '../testing/context';
import { rankSkills, skillListTool, skillRunTool } from './skills';

const SKILLS = join(import.meta.dirname, '..', '..', '..', '..', 'catalog', 'skills');

function skills() {
  const b = new BuiltinSkills({ root: SKILLS, manifest: loadBuiltinsManifest() });
  b.verify();
  return new SkillStore(mkdtempSync(join(tmpdir(), 'desk-tools-')), undefined, b);
}

/** What a built runtime sets (catalog/runtimes.ts): without it Python writes __pycache__ into the tree, which then fails verify(). */
const skillEnv = () => ({ bins: [], vars: { PYTHONDONTWRITEBYTECODE: '1' }, blocked: null, note: null });

describe('skill tools and built-in skills', () => {
  it('skill_list shows built-ins', async () => {
    const store = new EventStore(openDb(':memory:').db);
    const ctx = testToolContext(mkdtempSync(join(tmpdir(), 'desk-ws-')), { services: testServices({ skills: skills(), store }) });
    const out = await skillListTool.execute({}, ctx);
    expect(out).toMatch(/- pdf-toolkit \(builtin, v1\)/);
    expect(out).toMatch(/- web-research \(builtin, v1\)/);
    // A description that does not parse (say an unquoted ": " in SKILL.md's frontmatter) comes out empty.
    for (const line of (out as string).split('\n')) expect(line).toMatch(/ — \S/);
  });

  it('skill_list ranks Desk\'s presentations skill above an installed one for a pitch deck', async () => {
    const store = skills();
    // The catalog's frontend-slides, as installed: it names pitches, and sorts before presentations by name.
    const dir = join(store.root('global'), 'frontend-slides');
    mkdirSync(dir, { recursive: true });
    writeFileSync(
      join(dir, 'SKILL.md'),
      '---\nname: frontend-slides\ndescription: Create stunning, animation-rich HTML presentations from scratch or by converting PowerPoint files. Use when the user wants to build a presentation, convert a PPT/PPTX to web, or create slides for a talk/pitch.\n---\n\nBody.\n',
    );
    const ctx = testToolContext(mkdtempSync(join(tmpdir(), 'desk-ws-')), { services: testServices({ skills: store, store: new EventStore(openDb(':memory:').db) }) });
    const first = async (query: string) => ((await skillListTool.execute({ query }, ctx)) as string).split('\n')[0];
    expect(await first('pitch deck')).toMatch(/^- presentations \(builtin/);
    expect(await first('pitch')).toMatch(/^- presentations \(builtin/);
    expect(await first('presentation')).toMatch(/^- presentations \(builtin/);
    expect(await first('html slides')).toMatch(/^- frontend-slides \(global/);
  });

  it('skill_run says when it set up the environment first', async () => {
    const ctx = testToolContext(mkdtempSync(join(tmpdir(), 'desk-ws-')), {
      services: testServices({ skills: skills(), skillEnv, prepareSkillRuntime: async () => ({ waitedMs: 42_000 }) }),
    });
    const out = await skillRunTool.execute({ name: 'file-inspector', script: 'file_identify.py', args: ['--help'], timeout_s: 60 }, ctx);
    expect(out).toMatch(/^\(Set up file-inspector's Python environment first: first use only, 42 s\.\)\n\[/);
  });

  it('skill_run adds nothing when no setup was needed', async () => {
    const ctx = testToolContext(mkdtempSync(join(tmpdir(), 'desk-ws-')), { services: testServices({ skills: skills(), skillEnv }) });
    const out = await skillRunTool.execute({ name: 'file-inspector', script: 'file_identify.py', args: ['--help'], timeout_s: 60 }, ctx);
    expect(out).toMatch(/^\[/);
  });
});

describe('rankSkills', () => {
  const skill = (name: string, scope: 'builtin' | 'global' | 'project', description: string) =>
    ({ name, scope, description, version: 1, dir: '', error: null }) as unknown as Parameters<typeof rankSkills>[0][number];
  const all = [skill('alpha', 'global', 'Charts and decks'), skill('beta', 'builtin', 'Charts'), skill('gamma', 'project', 'Nothing here')];

  it('keeps every skill in order without a query', () => {
    expect(rankSkills(all, undefined).map((s) => s.name)).toEqual(['alpha', 'beta', 'gamma']);
    expect(rankSkills(all, '  ').map((s) => s.name)).toEqual(['alpha', 'beta', 'gamma']);
  });

  it('keeps skills that match any word, most words first, built-ins first on a tie', () => {
    expect(rankSkills(all, 'chart').map((s) => s.name)).toEqual(['beta', 'alpha']);
    expect(rankSkills(all, 'chart deck').map((s) => s.name)).toEqual(['alpha', 'beta']);
    expect(rankSkills(all, 'CHARTS charts').map((s) => s.name)).toEqual(['beta', 'alpha']);
    expect(rankSkills(all, 'spreadsheet')).toEqual([]);
  });
});
