import { randomBytes } from 'node:crypto';
import { chmodSync, existsSync, lstatSync, mkdirSync, readdirSync, readFileSync, realpathSync, renameSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { basename, dirname, join, normalize, relative, sep } from 'node:path';
import { SkillName, type SkillScope } from '@desk/protocol';
import { parse as parseYaml, stringify as stringifyYaml } from 'yaml';
import { NotFoundError, ValidationError } from '../errors';
import { readAgentFileSync } from '../tools/agent-files';
import type { SandboxGuard } from '../tools/sandbox';
import type { BuiltinSkills } from './builtins';

export const SKILL_FILE = 'SKILL.md';
const HISTORY_DIR = '.history';
const STAGING_DIR = '.staging';
const MAX_DESCRIPTION = 1024;
const MAX_SKILL_MD = 100_000;
const MAX_FILE_BYTES = 2 * 1024 * 1024;
const MAX_TOTAL_BYTES = 10 * 1024 * 1024;
const MAX_FILES = 200;
const SKIPPED_DIRS = new Set(['.git', 'node_modules', '__pycache__', '.venv', HISTORY_DIR, STAGING_DIR]);

export type SkillSummary = {
  name: string;
  scope: SkillScope;
  description: string;
  dir: string;
  version: number;
  /** Set when SKILL.md is missing or malformed; the skill is listed but cannot be used until fixed. */
  error?: string;
};

export type SkillDetail = SkillSummary & {
  instructions: string;
  frontmatter: Record<string, unknown>;
  files: Array<{ path: string; size: number }>;
};

export type SkillFileInput = { path: string; content: string | Buffer };

export type SkillSaveInput = {
  scope: SkillScope;
  projectId?: string;
  name: string;
  description?: string;
  instructions?: string;
  files?: SkillFileInput[];
  removeFiles?: string[];
  /** A directory (e.g. a thread's draft) whose content replaces the skill's before the other changes apply. */
  fromDir?: string;
};

export type ParsedSkillMd = { frontmatter: Record<string, unknown>; instructions: string };

export function parseSkillMd(text: string): ParsedSkillMd {
  const m = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?([\s\S]*)$/.exec(text);
  if (!m) throw new ValidationError('SKILL.md must start with YAML frontmatter between --- lines');
  let fm: unknown;
  try {
    fm = parseYaml(m[1]!);
  } catch (e) {
    throw new ValidationError(`SKILL.md frontmatter is not valid YAML: ${(e as Error).message}`);
  }
  if (!fm || typeof fm !== 'object' || Array.isArray(fm)) throw new ValidationError('SKILL.md frontmatter must be a mapping');
  return { frontmatter: fm as Record<string, unknown>, instructions: m[2]!.trim() };
}

export function serializeSkillMd(frontmatter: Record<string, unknown>, instructions: string): string {
  return `---\n${stringifyYaml(frontmatter, { lineWidth: 0 }).trim()}\n---\n\n${instructions.trim()}\n`;
}

function checkDescription(description: unknown): string {
  if (typeof description !== 'string' || !description.trim()) throw new ValidationError('A skill needs a description (what it does and when to use it)');
  if (description.length > MAX_DESCRIPTION) throw new ValidationError(`Skill description is longer than ${MAX_DESCRIPTION} characters`);
  return description.trim();
}

export function checkSkillName(name: string): string {
  const r = SkillName.safeParse(name);
  if (!r.success) throw new ValidationError(`Invalid skill name "${name}": ${r.error.issues[0]?.message}`);
  return name;
}

/** Normalises a path relative to a skill directory, refusing anything that could escape it. */
export function checkRelativePath(path: string): string {
  const p = normalize(path).replace(/^\.\/+/, '');
  if (!p || p === '.' || p.startsWith('/') || p.includes('\0') || p.split(/[\\/]/).some((s) => s === '..')) {
    throw new ValidationError(`Invalid skill file path "${path}"`);
  }
  if (p.split(/[\\/]/).some((s) => SKIPPED_DIRS.has(s))) throw new ValidationError(`Reserved path "${path}"`);
  return p;
}

/** Files of a skill directory: SKILL.md first, then by path. */
function listFiles(dir: string): Array<{ path: string; size: number }> {
  const rank = (p: string) => (p === SKILL_FILE ? 0 : 1);
  return walkFiles(dir).sort((a, b) => rank(a.path) - rank(b.path) || (a.path < b.path ? -1 : a.path > b.path ? 1 : 0));
}

function walkFiles(dir: string, base = dir): Array<{ path: string; size: number }> {
  const out: Array<{ path: string; size: number }> = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (!SKIPPED_DIRS.has(entry.name)) out.push(...walkFiles(full, base));
    } else if (entry.isFile()) {
      out.push({ path: relative(base, full), size: statSync(full).size });
    }
  }
  return out;
}

/**
 * Copies a directory tree, skipping symlinks and tooling dirs, enforcing size limits. The tree may be a thread's draft,
 * so each file is read through `readAgentFileSync`: a symlink swapped in after the lstat cannot redirect the copy.
 */
function copyTree(from: string, dest: string, guard: SandboxGuard | undefined): void {
  const src = realpathSync(from);
  let files = 0;
  let bytes = 0;
  const walk = (from: string, to: string) => {
    mkdirSync(to, { recursive: true });
    for (const entry of readdirSync(from, { withFileTypes: true })) {
      const s = join(from, entry.name);
      const d = join(to, entry.name);
      const st = lstatSync(s);
      if (st.isSymbolicLink()) continue;
      if (st.isDirectory()) {
        if (!SKIPPED_DIRS.has(entry.name)) walk(s, d);
      } else if (st.isFile()) {
        if (st.size > MAX_FILE_BYTES) throw new ValidationError(`${relative(src, s)} is larger than ${MAX_FILE_BYTES / 1024 / 1024} MB`);
        files++;
        bytes += st.size;
        if (files > MAX_FILES) throw new ValidationError(`A skill can hold at most ${MAX_FILES} files`);
        if (bytes > MAX_TOTAL_BYTES) throw new ValidationError(`A skill can hold at most ${MAX_TOTAL_BYTES / 1024 / 1024} MB`);
        writeFileSync(d, readAgentFileSync(s, st, guard));
      }
    }
  };
  walk(src, dest);
}

function makeScriptsExecutable(dir: string): void {
  for (const f of listFiles(dir)) {
    const executable = f.path.startsWith(`scripts${sep}`) || f.path.startsWith('scripts/');
    chmodSync(join(dir, f.path), executable ? 0o755 : 0o644);
  }
}

/**
 * Skills on disk (the source of truth, so users can hand-edit them):
 * global `<dataDir>/skills/<name>/`, project `<dataDir>/projects/<id>/skills/<name>/`.
 * Every change keeps the previous version under `<root>/.history/<name>/<version>/`.
 */
export class SkillStore {
  /** `guard` keeps Desk's secrets out of skills copied from agents' folders. */
  constructor(
    private readonly dataDir: string,
    private readonly guard?: SandboxGuard,
    /** Desk's own skills, resolved after project and global ones (read-only). */
    private readonly builtins?: BuiltinSkills,
  ) {}

  root(scope: SkillScope, projectId?: string): string {
    if (scope === 'builtin') {
      if (!this.builtins) throw new ValidationError('This daemon has no built-in skills');
      return this.builtins.root;
    }
    if (scope === 'global') return join(this.dataDir, 'skills');
    if (!projectId) throw new ValidationError('Project skills need a project id');
    return join(this.dataDir, 'projects', projectId, 'skills');
  }

  private writable(scope: SkillScope): void {
    if (scope === 'builtin') throw new ValidationError('Built-in skills are read-only. Duplicate one to make your own version.');
  }

  /** Directories an agent of the project may read skills from. */
  roots(projectId: string): string[] {
    return [this.root('project', projectId), this.root('global')];
  }

  private historyDir(scope: SkillScope, name: string, projectId?: string): string {
    return join(this.root(scope, projectId), HISTORY_DIR, name);
  }

  private versions(scope: SkillScope, name: string, projectId?: string): number[] {
    const dir = this.historyDir(scope, name, projectId);
    if (!existsSync(dir)) return [];
    return readdirSync(dir)
      .map(Number)
      .filter((n) => Number.isInteger(n) && n > 0)
      .sort((a, b) => a - b);
  }

  private currentVersion(scope: SkillScope, name: string, projectId?: string): number {
    return (this.versions(scope, name, projectId).at(-1) ?? 0) + 1;
  }

  private summarize(scope: SkillScope, name: string, projectId?: string): SkillSummary {
    const dir = join(this.root(scope, projectId), name);
    const base = { name, scope, dir, version: this.currentVersion(scope, name, projectId) };
    const damage = scope === 'builtin' ? (this.builtins?.broken(name) ?? null) : null;
    try {
      const { frontmatter } = parseSkillMd(readFileSync(join(dir, SKILL_FILE), 'utf8'));
      const description = checkDescription(frontmatter.description);
      return damage ? { ...base, description, error: damage } : { ...base, description };
    } catch (e) {
      const error = damage ?? (existsSync(join(dir, SKILL_FILE)) ? (e as Error).message : 'SKILL.md is missing');
      return { ...base, description: '', error };
    }
  }

  listScope(scope: SkillScope, projectId?: string): SkillSummary[] {
    if (scope === 'builtin') return this.builtins ? this.builtins.names().map((n) => this.summarize('builtin', n)) : [];
    const root = this.root(scope, projectId);
    if (!existsSync(root)) return [];
    return readdirSync(root, { withFileTypes: true })
      .filter((e) => e.isDirectory() && !e.name.startsWith('.') && SkillName.safeParse(e.name).success)
      .map((e) => this.summarize(scope, e.name, projectId))
      .sort((a, b) => a.name.localeCompare(b.name));
  }

  /**
   * Skills visible to a project (project skills shadow global ones), or only global skills without a project.
   * With `builtins`, as agents see them: plus Desk's usable built-in skills that no own skill shadows.
   */
  list(projectId?: string, opts: { builtins?: boolean } = {}): SkillSummary[] {
    const project = projectId ? this.listScope('project', projectId) : [];
    const own = new Set(project.map((s) => s.name));
    const mine = [...project, ...this.listScope('global').filter((s) => !own.has(s.name))];
    if (opts.builtins && this.builtins) {
      const taken = new Set(mine.map((s) => s.name));
      for (const n of this.builtins.names()) if (!taken.has(n) && this.builtins.usable(n)) mine.push(this.summarize('builtin', n));
    }
    return mine.sort((a, b) => a.name.localeCompare(b.name));
  }

  resolve(name: string, projectId?: string): SkillSummary | undefined {
    if (!SkillName.safeParse(name).success) return undefined;
    for (const scope of projectId ? (['project', 'global'] as const) : (['global'] as const)) {
      if (existsSync(join(this.root(scope, projectId), name))) return this.summarize(scope, name, projectId);
    }
    return this.builtins?.usable(name) ? this.summarize('builtin', name) : undefined;
  }

  get(scope: SkillScope, name: string, projectId?: string): SkillDetail | undefined {
    checkSkillName(name);
    if (scope === 'builtin' && !this.builtins?.entry(name)) return undefined;
    const dir = join(this.root(scope, projectId), name);
    if (!existsSync(dir)) return undefined;
    const summary = this.summarize(scope, name, projectId);
    let parsed: ParsedSkillMd = { frontmatter: {}, instructions: '' };
    try {
      parsed = parseSkillMd(readFileSync(join(dir, SKILL_FILE), 'utf8'));
    } catch {
      // Reported through summary.error.
    }
    return { ...summary, ...parsed, files: listFiles(dir) };
  }

  /** A skill as it was at `version` (history), or the live skill when `version` is current. */
  getVersion(scope: SkillScope, name: string, version: number, projectId?: string): SkillDetail | undefined {
    checkSkillName(name);
    const live = join(this.root(scope, projectId), name);
    if (version === this.currentVersion(scope, name, projectId) && existsSync(live)) return this.get(scope, name, projectId);
    const dir = join(this.historyDir(scope, name, projectId), String(version));
    if (!existsSync(dir)) return undefined;
    let parsed: ParsedSkillMd = { frontmatter: {}, instructions: '' };
    let description = '';
    let error: string | undefined;
    try {
      parsed = parseSkillMd(readFileSync(join(dir, SKILL_FILE), 'utf8'));
      description = checkDescription(parsed.frontmatter.description);
    } catch (e) {
      error = (e as Error).message;
    }
    return { name, scope, dir, version, description, ...(error ? { error } : {}), ...parsed, files: listFiles(dir) };
  }

  /** Resolves a file inside a skill, refusing paths (or symlinks) that lead outside it. */
  filePath(skill: SkillSummary, path: string): string {
    const rel = checkRelativePath(path);
    const full = join(skill.dir, rel);
    if (!existsSync(full)) throw new NotFoundError(`Skill ${skill.name} has no file ${rel}`);
    const real = realpathSync(full);
    const realDir = realpathSync(skill.dir);
    if (real !== realDir && !real.startsWith(realDir + sep)) throw new ValidationError(`${rel} leads outside the skill`);
    return real;
  }

  /** Creates or updates a skill atomically; the previous version (if any) moves to history. */
  save(input: SkillSaveInput): { version: number; dir: string; created: boolean; description: string } {
    this.writable(input.scope);
    const name = checkSkillName(input.name);
    const root = this.root(input.scope, input.projectId);
    const dir = join(root, name);
    const staging = join(root, STAGING_DIR, `${name}-${randomBytes(4).toString('hex')}`);
    const existed = existsSync(dir);
    mkdirSync(dirname(staging), { recursive: true });
    try {
      if (input.fromDir) {
        if (!existsSync(input.fromDir) || !statSync(input.fromDir).isDirectory()) throw new ValidationError(`${input.fromDir} is not a directory`);
        copyTree(input.fromDir, staging, this.guard);
      } else if (existed) {
        copyTree(dir, staging, this.guard);
      } else {
        mkdirSync(staging, { recursive: true });
      }
      for (const p of input.removeFiles ?? []) {
        const rel = checkRelativePath(p);
        if (rel === SKILL_FILE) throw new ValidationError('SKILL.md cannot be removed');
        rmSync(join(staging, rel), { recursive: true, force: true });
      }
      for (const f of input.files ?? []) {
        const rel = checkRelativePath(f.path);
        const content = typeof f.content === 'string' ? Buffer.from(f.content, 'utf8') : f.content;
        if (content.length > MAX_FILE_BYTES) throw new ValidationError(`${rel} is larger than ${MAX_FILE_BYTES / 1024 / 1024} MB`);
        mkdirSync(dirname(join(staging, rel)), { recursive: true });
        writeFileSync(join(staging, rel), content);
      }

      const mdPath = join(staging, SKILL_FILE);
      let parsed: ParsedSkillMd = { frontmatter: {}, instructions: '' };
      if (existsSync(mdPath)) parsed = parseSkillMd(readFileSync(mdPath, 'utf8'));
      else if (input.instructions === undefined) throw new ValidationError('A new skill needs instructions (the body of SKILL.md)');
      const description = checkDescription(input.description ?? parsed.frontmatter.description);
      const instructions = (input.instructions ?? parsed.instructions).trim();
      if (!instructions) throw new ValidationError('Skill instructions cannot be empty');
      const md = serializeSkillMd({ ...parsed.frontmatter, name, description }, instructions);
      if (md.length > MAX_SKILL_MD) throw new ValidationError(`SKILL.md is longer than ${MAX_SKILL_MD} characters; move details into references/ files`);
      writeFileSync(mdPath, md);

      const files = listFiles(staging);
      if (files.length > MAX_FILES) throw new ValidationError(`A skill can hold at most ${MAX_FILES} files`);
      if (files.reduce((n, f) => n + f.size, 0) > MAX_TOTAL_BYTES) throw new ValidationError(`A skill can hold at most ${MAX_TOTAL_BYTES / 1024 / 1024} MB`);
      makeScriptsExecutable(staging);

      const version = this.currentVersion(input.scope, name, input.projectId) + (existed ? 1 : 0);
      if (existed) this.archive(input.scope, name, input.projectId);
      renameSync(staging, dir);
      return { version, dir, created: !existed, description };
    } finally {
      rmSync(staging, { recursive: true, force: true });
    }
  }

  private archive(scope: SkillScope, name: string, projectId?: string): void {
    const history = this.historyDir(scope, name, projectId);
    mkdirSync(history, { recursive: true });
    renameSync(join(this.root(scope, projectId), name), join(history, String(this.currentVersion(scope, name, projectId))));
  }

  /** Removes a skill; its last version stays in history and can be restored. */
  delete(scope: SkillScope, name: string, projectId?: string): void {
    this.writable(scope);
    checkSkillName(name);
    if (!existsSync(join(this.root(scope, projectId), name))) throw new NotFoundError(`No ${scope} skill named ${name}`);
    this.archive(scope, name, projectId);
  }

  history(scope: SkillScope, name: string, projectId?: string): Array<{ version: number; description: string; current: boolean }> {
    checkSkillName(name);
    const past = this.versions(scope, name, projectId).map((version) => {
      let description = '';
      try {
        description = String(parseSkillMd(readFileSync(join(this.historyDir(scope, name, projectId), String(version), SKILL_FILE), 'utf8')).frontmatter.description ?? '');
      } catch {
        // Old versions may be malformed; they are still listed.
      }
      return { version, description, current: false };
    });
    const current = existsSync(join(this.root(scope, projectId), name))
      ? [{ version: this.currentVersion(scope, name, projectId), description: this.summarize(scope, name, projectId).description, current: true }]
      : [];
    return [...past, ...current];
  }

  /** Brings back an earlier version as the newest one (history is never rewritten). */
  restore(scope: SkillScope, name: string, version: number, projectId?: string): { version: number; dir: string; created: boolean; description: string } {
    this.writable(scope);
    const src = join(this.historyDir(scope, name, projectId), String(version));
    if (!existsSync(src)) throw new NotFoundError(`Skill ${name} has no version ${version}`);
    return this.save({ scope, name, fromDir: src, ...(projectId ? { projectId } : {}) });
  }

  /** Reads the skill name a directory declares (frontmatter `name`, else the directory name). */
  static declaredName(dir: string): string {
    try {
      const n = parseSkillMd(readFileSync(join(dir, SKILL_FILE), 'utf8')).frontmatter.name;
      if (typeof n === 'string' && SkillName.safeParse(n).success) return n;
    } catch {
      // Fall back to the directory name.
    }
    return basename(dir);
  }
}
