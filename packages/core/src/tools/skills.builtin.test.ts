import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { openDb } from '../db/open';
import { EventStore } from '../events/store';
import { BuiltinSkills, loadBuiltinsManifest } from '../skills/builtins';
import { SkillStore } from '../skills/store';
import { testServices, testToolContext } from '../testing/context';
import { skillListTool, skillRunTool } from './skills';

const SKILLS = join(import.meta.dirname, '..', '..', '..', '..', 'catalog', 'skills');

function skills() {
  const b = new BuiltinSkills({ root: SKILLS, manifest: loadBuiltinsManifest() });
  b.verify();
  return new SkillStore(mkdtempSync(join(tmpdir(), 'desk-tools-')), undefined, b);
}

describe('skill tools and built-in skills', () => {
  it('skill_list shows built-ins', async () => {
    const store = new EventStore(openDb(':memory:').db);
    const ctx = testToolContext(mkdtempSync(join(tmpdir(), 'desk-ws-')), { services: testServices({ skills: skills(), store }) });
    const out = await skillListTool.execute({}, ctx);
    expect(out).toMatch(/- pdf-toolkit \(builtin, v1\)/);
    expect(out).toMatch(/- web-research \(builtin, v1\)/);
  });

  it('skill_run says when it set up the environment first', async () => {
    const ctx = testToolContext(mkdtempSync(join(tmpdir(), 'desk-ws-')), {
      services: testServices({ skills: skills(), prepareSkillRuntime: async () => ({ waitedMs: 42_000 }) }),
    });
    const out = await skillRunTool.execute({ name: 'file-inspector', script: 'file_identify.py', args: ['--help'], timeout_s: 60 }, ctx);
    expect(out).toMatch(/^\(Set up file-inspector's Python environment first: first use only, 42 s\.\)\n\[/);
  });

  it('skill_run adds nothing when no setup was needed', async () => {
    const ctx = testToolContext(mkdtempSync(join(tmpdir(), 'desk-ws-')), { services: testServices({ skills: skills() }) });
    const out = await skillRunTool.execute({ name: 'file-inspector', script: 'file_identify.py', args: ['--help'], timeout_s: 60 }, ctx);
    expect(out).toMatch(/^\[/);
  });
});
