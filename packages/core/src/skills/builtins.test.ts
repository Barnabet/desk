import { cpSync, existsSync, mkdtempSync, readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { GLOBAL_PROJECT_ID } from '@desk/protocol';
import { describe, expect, it } from 'vitest';
import { readTree, treeDigest } from '../catalog/digest';
import { loadCatalog } from '../catalog/service';
import { openDb } from '../db/open';
import { EventStore } from '../events/store';
import { BuiltinSkills, builtinEnabled, loadBuiltinsManifest, runtimeKey } from './builtins';

const SKILLS = join(import.meta.dirname, '..', '..', '..', '..', 'catalog', 'skills');

describe('built-in skill switch', () => {
  it('is on by default and follows the latest toggle', () => {
    const { db, close } = openDb(':memory:');
    const store = new EventStore(db);
    expect(builtinEnabled(db, 'pdf-toolkit')).toBe(true);
    store.append({ project_id: GLOBAL_PROJECT_ID, agent_id: null, type: 'skill.builtin_toggled', payload: { name: 'pdf-toolkit', enabled: false } });
    expect(builtinEnabled(db, 'pdf-toolkit')).toBe(false);
    store.append({ project_id: GLOBAL_PROJECT_ID, agent_id: null, type: 'skill.builtin_toggled', payload: { name: 'pdf-toolkit', enabled: true } });
    expect(builtinEnabled(db, 'pdf-toolkit')).toBe(true);
    expect(builtinEnabled(db, 'images')).toBe(true);
    close();
  });
});

describe('the built-in manifest', () => {
  const manifest = loadBuiltinsManifest();

  it('lists exactly the skills in catalog/skills', () => {
    const dirs = readdirSync(SKILLS)
      .filter((d) => existsSync(join(SKILLS, d, 'SKILL.md')))
      .sort();
    expect(manifest.skills.map((s) => s.name).sort()).toEqual(dirs);
    expect(dirs).toHaveLength(12);
  });

  it('matches every tree (run pnpm builtins:pin after editing a skill)', () => {
    for (const s of manifest.skills) {
      const files = readTree(join(SKILLS, s.name));
      expect(treeDigest(files), s.name).toBe(s.digest);
      expect(files.length, s.name).toBe(s.files);
    }
  });

  it("uses each skill's packages.txt", () => {
    for (const s of manifest.skills) {
      const lines = readFileSync(join(SKILLS, s.name, 'packages.txt'), 'utf8')
        .split('\n')
        .map((l) => l.trim())
        .filter((l) => l && !l.startsWith('#'));
      expect(s.runtime.python?.packages, s.name).toEqual(lines);
    }
  });

  it('is no longer in the catalog', () => {
    expect(loadCatalog().entries.some((e) => e.source.type === 'builtin')).toBe(false);
  });
});

describe('BuiltinSkills', () => {
  const manifest = loadBuiltinsManifest();

  it('verifies trees and hides tampered, missing or switched-off skills', () => {
    const root = mkdtempSync(join(tmpdir(), 'desk-builtins-'));
    cpSync(join(SKILLS, 'file-inspector'), join(root, 'file-inspector'), { recursive: true });
    cpSync(join(SKILLS, 'images'), join(root, 'images'), { recursive: true });
    writeFileSync(join(root, 'images', 'SKILL.md'), `${readFileSync(join(root, 'images', 'SKILL.md'), 'utf8')}\ntampered\n`);
    const off = new Set(['data-files']);
    const b = new BuiltinSkills({ root, manifest, enabled: (n) => !off.has(n) });
    b.verify();
    expect(b.broken('file-inspector')).toBeNull();
    expect(b.usable('file-inspector')).toBe(true);
    expect(b.broken('images')).toMatch(/does not match/);
    expect(b.usable('images')).toBe(false);
    expect(b.broken('pdf-toolkit')).toMatch(/cannot be read/);
    expect(b.enabled('data-files')).toBe(false);
    expect(b.usable('nope')).toBe(false);
  });

  it('keys runtimes by what gets installed, not by the scripts', () => {
    const b = new BuiltinSkills({ root: SKILLS, manifest });
    const entry = manifest.skills.find((s) => s.name === 'pdf-toolkit')!;
    const spec = b.runtimeSpec('pdf-toolkit');
    expect(spec.digest).toBe(runtimeKey(entry.runtime));
    expect(spec.digest).not.toBe(entry.digest);
  });
});
