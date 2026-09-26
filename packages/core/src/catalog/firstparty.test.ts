import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { parseSkillMd } from '../skills/store';
import { readTree } from './digest';
import { scanSkill } from './review';

/** Structural checks on Desk's first-party catalog skills (catalog/skills), which must also run on Windows. */
const ROOT = join(import.meta.dirname, '..', '..', '..', '..', 'catalog');
const SKILLS = join(ROOT, 'skills');
const SHARED = join(ROOT, 'shared');

const skills = readdirSync(SKILLS).filter((d) => existsSync(join(SKILLS, d, 'SKILL.md')));
const scriptsOf = (skill: string) => {
  const dir = join(SKILLS, skill, 'scripts');
  return existsSync(dir) ? readdirSync(dir).filter((f) => f.endsWith('.py')) : [];
};

/** Code that breaks on Windows (or on another Mac). A line may opt out with a `# portable-ok: <why>` comment. */
const NOT_PORTABLE: Array<[RegExp, string]> = [
  [/\bos\.fork\s*\(/, 'os.fork (no fork on Windows)'],
  [/^\s*(import|from)\s+(fcntl|resource|pwd|grp|termios|pty)\b/, 'a POSIX-only module'],
  [/\bsignal\.SIG(KILL|HUP|USR1|USR2|ALRM)\b/, 'a POSIX-only signal'],
  [/shell\s*=\s*True/, 'shell=True (no POSIX shell on Windows; pass an argument list)'],
  [/["'](\/tmp|\/private\/tmp|\/var\/folders)\b/, 'a hard-coded temp dir (use tempfile)'],
  [/["']\/(usr|opt)\/(local\/)?bin\//, 'a hard-coded binary path (use find_tool)'],
  [/\bos\.(getuid|geteuid|getgid|setsid|killpg|getpgid)\s*\(/, 'a POSIX-only os call'],
  [/\b(pyobjc|Foundation|AppKit|Quartz|Vision)\b.*\bimport\b|^\s*import\s+(objc|Foundation|AppKit|Quartz|Vision)\b/, 'a macOS-only framework'],
  [/["'](qlmanage|textutil|sips|afconvert|mdls|osascript|pbcopy)["']/, 'a macOS-only command'],
];

describe('first-party catalog skills', () => {
  it('keep their copies of catalog/shared modules identical (run pnpm catalog:sync)', () => {
    const shared = readdirSync(SHARED).filter((f) => f.endsWith('.py'));
    for (const skill of skills)
      for (const name of shared) {
        const copy = join(SKILLS, skill, 'scripts', name);
        if (existsSync(copy)) expect(readFileSync(copy, 'utf8') === readFileSync(join(SHARED, name), 'utf8'), `${skill}/scripts/${name}`).toBe(true);
      }
  });

  it('have valid SKILL.md frontmatter matching the folder', () => {
    for (const skill of skills) {
      const { frontmatter } = parseSkillMd(readFileSync(join(SKILLS, skill, 'SKILL.md'), 'utf8'));
      expect(frontmatter.name, skill).toBe(skill);
      expect(String(frontmatter.description ?? '').length, skill).toBeGreaterThan(20);
      expect(String(frontmatter.description ?? '').length, skill).toBeLessThanOrEqual(1024);
    }
  });

  it('only mention scripts that exist', () => {
    for (const skill of skills) {
      const md = readFileSync(join(SKILLS, skill, 'SKILL.md'), 'utf8');
      const have = new Set(scriptsOf(skill));
      for (const m of md.matchAll(/scripts\/([\w.-]+\.py)/g)) expect(have.has(m[1]!), `${skill}: SKILL.md names scripts/${m[1]}`).toBe(true);
    }
  });

  it('carry no bytecode or Finder files (the pinned digest covers every file on disk)', () => {
    const walk = (dir: string): string[] =>
      readdirSync(dir, { withFileTypes: true }).flatMap((d) => (d.isDirectory() && d.name !== '__pycache__' ? walk(join(dir, d.name)) : [join(dir, d.name)]));
    const junk = skills.flatMap((skill) => walk(join(SKILLS, skill))).filter((f) => /(^|\/)(__pycache__|\.DS_Store)$|\.pyc$/.test(f));
    expect(junk, 'delete these; run scripts with PYTHONDONTWRITEBYTECODE=1').toEqual([]);
  });

  it('show no warnings in the install review sheet (escape invisible characters, split long literals)', () => {
    for (const skill of skills) expect(scanSkill(readTree(join(SKILLS, skill))), skill).toEqual([]);
  });

  it('guard every entry script with __main__', () => {
    for (const skill of skills)
      for (const f of scriptsOf(skill)) {
        if (f.startsWith('_')) continue;
        expect(readFileSync(join(SKILLS, skill, 'scripts', f), 'utf8'), `${skill}/scripts/${f}`).toMatch(/if __name__ == ["']__main__["']:/);
      }
  });

  it('avoid code that does not run on Windows', () => {
    const problems: string[] = [];
    for (const skill of skills)
      for (const f of scriptsOf(skill)) {
        const lines = readFileSync(join(SKILLS, skill, 'scripts', f), 'utf8').split('\n');
        lines.forEach((line, i) => {
          if (/#\s*portable-ok\b/.test(line) || /^\s*#/.test(line)) return;
          for (const [re, why] of NOT_PORTABLE) if (re.test(line)) problems.push(`${skill}/scripts/${f}:${i + 1}: ${why}: ${line.trim()}`);
        });
      }
    expect(problems).toEqual([]);
  });
});
