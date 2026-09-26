import { chmodSync, existsSync, lstatSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve, sep } from 'node:path';
import {
  CatalogFile,
  CatalogInstallRequest,
  type CatalogEntry,
  type CatalogInstall,
  type CatalogInstallResult,
  type CatalogInstallState,
  type CatalogItem,
  type CatalogReview,
  type RuntimeState,
  type SkillScope,
  type StoredEvent,
  type WritableSkillScope,
} from '@desk/protocol';
import catalogData from './catalog.json' with { type: 'json' };
import { ConflictError, NotFoundError, ValidationError } from '../errors';
import type { EventStore } from '../events/store';
import type { Runtime } from '../runtime/runtime';
import type { RuntimeSpec } from '../skills/builtins';
import { parseSkillMd, SKILL_FILE } from '../skills/store';
import { listProjects } from '../state/queries';
import { readTree, treeDigest } from './digest';
import { isScript, scanSkill } from './review';
import { DEFAULT_CAPS, extractSubtree, type ExtractedFile } from './tar';

/** Returns the response body of a GET; throws on a non-2xx status. */
export type CatalogFetch = (url: string) => Promise<AsyncIterable<Uint8Array>>;

export type SkillRef = { scope: SkillScope; name: string; projectId?: string };
/** A skill the catalog installs: never a built-in one. */
type CatalogRef = SkillRef & { scope: WritableSkillScope };

/** What the catalog needs from the runtime manager (Plan 13 Task 3); absent means "no runtime". */
export type CatalogRuntimes = {
  state(ref: SkillRef): { state: RuntimeState; reason: string | null };
  setup(ref: SkillRef, entry: RuntimeSpec, updated: string): void;
};

export type CatalogServiceOptions = {
  runtime: Runtime;
  store: EventStore;
  dataDir: string;
  catalog?: CatalogFile;
  /** Directory holding the first-party skills (catalog/skills in the repo, Resources/catalog/skills when packaged). */
  builtinRoot: string;
  fetch?: CatalogFetch;
  /** Base URL serving `/<owner>/<repo>/tar.gz/<sha>` (codeload.github.com; a fixture server in tests). */
  archiveBase?: string;
  /** Base URL serving `/<owner>/<repo>/<sha>/<path>` (raw.githubusercontent.com), for entries that list their files. */
  rawBase?: string;
  runtimes?: CatalogRuntimes;
};

const LICENSE_FILE = /^(LICEN[CS]E|COPYING)(\.[A-Za-z]+)?$/i;

/** The catalog shipped with this build. */
export function loadCatalog(data: unknown = catalogData): CatalogFile {
  return CatalogFile.parse(data);
}

/** GET with retries on 429 and 5xx (jittered backoff); the body as an async iterable. */
export const httpFetch: CatalogFetch = async (url) => {
  for (let attempt = 1; ; attempt++) {
    const res = await fetch(url, { redirect: 'follow', headers: { 'user-agent': 'Desk skill catalog' } });
    if (res.ok && res.body) return res.body as unknown as AsyncIterable<Uint8Array>;
    if ((res.status === 429 || res.status >= 500) && attempt < 4) {
      await res.body?.cancel();
      await new Promise((r) => setTimeout(r, 500 * 2 ** attempt * (0.5 + Math.random())));
      continue;
    }
    throw new ValidationError(`Download failed (${res.status}) from ${new URL(url).host}`);
  }
};

/** The version marker a catalog install records in its origin: the commit, or the digest for builtin skills. */
export function catalogMarker(entry: CatalogEntry): string {
  return entry.source.type === 'github' ? entry.source.sha : `builtin-${entry.digest.slice(7, 19)}`;
}

type Staged = { dir: string; files: ExtractedFile[]; licenseText: string | null; skillMd: string };

/**
 * The skill catalog: lists entries with their install state per scope, fetches and verifies a pinned entry into a
 * staging directory for review, and installs it through the skill store (origin `catalog:<id>@<marker>`).
 */
export class CatalogService {
  readonly catalog: CatalogFile;
  private readonly fetch: CatalogFetch;
  private readonly archiveBase: string;
  private readonly rawBase: string;

  constructor(private readonly o: CatalogServiceOptions) {
    this.catalog = o.catalog ?? loadCatalog();
    this.fetch = o.fetch ?? httpFetch;
    this.archiveBase = (o.archiveBase ?? 'https://codeload.github.com').replace(/\/+$/, '');
    this.rawBase = (o.rawBase ?? 'https://raw.githubusercontent.com').replace(/\/+$/, '');
  }

  entry(id: string): CatalogEntry {
    const e = this.catalog.entries.find((x) => x.id === id);
    if (!e) throw new NotFoundError(`Unknown catalog skill: ${id}`);
    return e;
  }

  list(): CatalogItem[] {
    const saves = this.o.store.list({ types: ['skill.saved', 'skill.deleted'] });
    const projects = listProjects(this.o.store.db).map((p) => p.id);
    return this.catalog.entries.map((entry) => {
      const installs: CatalogInstall[] = [];
      const global = this.installOf(entry, { scope: 'global', name: entry.id }, saves);
      if (global) installs.push(global);
      for (const projectId of projects) {
        const p = this.installOf(entry, { scope: 'project', name: entry.id, projectId }, saves);
        if (p) installs.push(p);
      }
      return { ...entry, installs };
    });
  }

  /** Downloads (or reads) the pinned entry, verifies it and stages it; returns what the user reviews. */
  async prepare(id: string): Promise<CatalogReview> {
    const entry = this.entry(id);
    const staged = await this.stage(entry);
    const src = entry.source;
    return {
      entry,
      source_url: src.type === 'github' ? `https://github.com/${src.repo}/tree/${src.sha}${src.path ? `/${src.path}` : ''}` : null,
      files: staged.files.map((f) => ({ path: f.path, size: f.content.length, script: isScript(f.path, f.mode, f.content) })),
      skill_md: staged.skillMd,
      license_text: staged.licenseText,
      warnings: scanSkill(staged.files),
    };
  }

  /** One file of an entry as staged for review (staging it again if needed), so the user can read it before installing. */
  async file(id: string, path: string): Promise<Buffer> {
    const entry = this.entry(id);
    let dir = this.stageDir(entry);
    if (!existsSync(join(dir, SKILL_FILE))) dir = (await this.stage(entry)).dir;
    const target = resolve(dir, path);
    if (!target.startsWith(`${dir}${sep}`)) throw new ValidationError('The path must stay inside the skill');
    if (!existsSync(target) || !lstatSync(target).isFile()) throw new NotFoundError(`${entry.id} has no file ${path}`);
    return readFileSync(target);
  }

  async install(id: string, request: CatalogInstallRequest = {}): Promise<CatalogInstallResult> {
    const entry = this.entry(id);
    const req = CatalogInstallRequest.parse(request);
    if (req.scope === 'project' && !req.project_id) throw new ValidationError('A project install needs project_id');
    const ref: CatalogRef = { scope: req.scope, name: entry.id, ...(req.scope === 'project' ? { projectId: req.project_id! } : {}) };
    const current = this.installOf(entry, ref, this.o.store.list({ types: ['skill.saved', 'skill.deleted'] }));
    if (current?.state === 'name_taken') {
      throw new ConflictError(`A skill named ${entry.id} already exists here and did not come from the catalog. Rename or delete it first.`);
    }
    if (current?.state === 'modified' && !req.replace_modified) {
      throw new ConflictError(`${entry.id} was edited after it was installed from the catalog. Confirm to replace your changes.`);
    }

    const staged = await this.stage(entry);
    const marker = catalogMarker(entry);
    const where = entry.source.type === 'github' ? `${entry.source.repo}@${marker.slice(0, 7)}` : 'Desk';
    const saved = this.o.runtime.saveSkill(
      { scope: ref.scope, name: entry.id, fromDir: staged.dir, ...(ref.projectId ? { projectId: ref.projectId } : {}) },
      { origin: `catalog:${entry.id}@${marker}`, changeNote: `${current ? 'Updated' : 'Installed'} from the catalog (${where})` },
    );
    rmSync(staged.dir, { recursive: true, force: true });
    this.o.runtimes?.setup(ref, entry, this.catalog.updated);
    const rt = this.o.runtimes?.state(ref) ?? { state: 'none' as const, reason: null };
    return {
      skill: { name: entry.id, scope: ref.scope, version: saved.version, project_id: ref.projectId ?? null },
      state: 'installed',
      runtime: rt.state,
    };
  }

  /** Rebuilds the runtime of an installed catalog skill (after a failure, or when its environment went missing). */
  retryRuntime(ref: CatalogRef): { state: RuntimeState; reason: string | null } {
    const entry = this.catalog.entries.find((e) => e.id === ref.name);
    const install = entry ? this.installOf(entry, ref, this.o.store.list({ types: ['skill.saved', 'skill.deleted'] })) : null;
    if (!entry || !install || install.state === 'name_taken') throw new NotFoundError(`${ref.name} was not installed from the catalog`);
    if (!this.o.runtimes) throw new ConflictError('Skill runtimes are not available in this daemon');
    this.o.runtimes.setup(ref, entry, this.catalog.updated);
    return this.o.runtimes.state(ref);
  }

  /** The install state of an entry in one scope, from the skill.saved log; null when no skill of that name exists there. */
  private installOf(entry: CatalogEntry, ref: CatalogRef, log: StoredEvent[]): CatalogInstall | null {
    let history: string[] = [];
    for (const e of log) {
      if (e.type !== 'skill.saved' && e.type !== 'skill.deleted') continue;
      if (e.payload.scope !== ref.scope || e.payload.name !== ref.name) continue;
      if (ref.scope === 'project' && e.project_id !== ref.projectId) continue;
      if (e.type === 'skill.deleted') history = [];
      else history.push(e.payload.origin);
    }
    if (!history.length || !this.o.runtime.skills.get(ref.scope, ref.name, ref.projectId)) return null;
    const tag = `catalog:${entry.id}@`;
    const last = history.at(-1)!;
    const lastCatalog = [...history].reverse().find((o) => o.startsWith(tag));
    let state: CatalogInstallState;
    if (last.startsWith(tag)) state = last.slice(tag.length) === catalogMarker(entry) ? 'installed' : 'update_available';
    else state = lastCatalog ? 'modified' : 'name_taken';
    const rt = state === 'name_taken' ? { state: 'none' as const, reason: null } : (this.o.runtimes?.state(ref) ?? { state: 'none' as const, reason: null });
    return {
      scope: ref.scope,
      project_id: ref.projectId ?? null,
      state,
      sha: lastCatalog ? lastCatalog.slice(tag.length) : null,
      runtime: rt.state,
      runtime_reason: rt.reason,
    };
  }

  /** Files mode: each listed file (and the repo licence) from the raw host at the pinned commit, within the store's caps. */
  private async fetchFiles(src: { repo: string; sha: string; path: string; files: string[]; executable?: string[]; license_file?: string }) {
    const exec = new Set(src.executable ?? []);
    const url = (p: string) => `${this.rawBase}/${src.repo}/${src.sha}/${p.split('/').map(encodeURIComponent).join('/')}`;
    const files: ExtractedFile[] = [];
    let total = 0;
    for (const rel of src.files) {
      const content = await collect(await this.fetch(url(src.path ? `${src.path}/${rel}` : rel)), DEFAULT_CAPS.maxFileBytes, rel);
      total += content.length;
      if (total > DEFAULT_CAPS.maxBytes) throw new ValidationError(`The skill is larger than ${DEFAULT_CAPS.maxBytes / 1024 / 1024} MB`);
      files.push({ path: rel, content, mode: exec.has(rel) ? 0o755 : 0o644 });
    }
    const rootFiles: ExtractedFile[] = [];
    if (src.license_file) {
      const content = await collect(await this.fetch(url(src.license_file)), 256 * 1024, src.license_file);
      rootFiles.push({ path: src.license_file.split('/').pop()!, content, mode: 0o644 });
    }
    return { files, rootFiles };
  }

  private stageDir(entry: CatalogEntry): string {
    return join(this.o.dataDir, 'catalog', 'staging', `${entry.id}@${catalogMarker(entry).slice(0, 12)}`);
  }

  /** Fetches or reads the entry, checks the digest and the SKILL.md, and writes it to a fresh staging directory. */
  private async stage(entry: CatalogEntry): Promise<Staged> {
    let files: ExtractedFile[];
    let rootFiles: ExtractedFile[] = [];
    if (entry.source.type === 'github' && entry.source.files) {
      ({ files, rootFiles } = await this.fetchFiles(entry.source as typeof entry.source & { files: string[] }));
    } else if (entry.source.type === 'github') {
      const { repo, sha, path } = entry.source;
      const body = await this.fetch(`${this.archiveBase}/${repo}/tar.gz/${sha}`);
      ({ files, rootFiles } = await extractSubtree(body, path, { rootFiles: LICENSE_FILE }));
    } else {
      const dir = join(this.o.builtinRoot, entry.source.path);
      if (!existsSync(dir)) throw new NotFoundError(`The builtin skill ${entry.id} is missing from this build`);
      files = readTree(dir);
    }
    const digest = treeDigest(files);
    if (digest !== entry.digest) {
      throw new ValidationError(`${entry.id} does not match the catalog (expected ${entry.digest.slice(0, 19)}…, got ${digest.slice(0, 19)}…). Nothing was installed.`);
    }
    const md = files.find((f) => f.path === SKILL_FILE);
    if (!md) throw new ValidationError(`${entry.id} has no ${SKILL_FILE}`);
    const skillMd = md.content.toString('utf8');
    const fm = parseSkillMd(skillMd).frontmatter;
    if (fm.name !== undefined && fm.name !== entry.id) throw new ValidationError(`${entry.id}'s ${SKILL_FILE} names itself "${String(fm.name)}"`);
    const description = typeof fm.description === 'string' ? fm.description.trim() : '';
    if (!description || description.length > 1024) throw new ValidationError(`${entry.id}'s description must be 1–1024 characters`);

    // Keep the licence with the skill: skills in subfolders carry only the repository's.
    const own = files.find((f) => LICENSE_FILE.test(f.path));
    const repoLicense = own ? null : (rootFiles[0] ?? null);
    const licenseText = (own ?? repoLicense)?.content.toString('utf8') ?? null;

    const dir = this.stageDir(entry);
    rmSync(dir, { recursive: true, force: true });
    for (const f of [...files, ...(repoLicense ? [repoLicense] : [])]) {
      const target = join(dir, f.path);
      mkdirSync(dirname(target), { recursive: true });
      writeFileSync(target, f.content);
      chmodSync(target, f.mode & 0o111 ? 0o755 : 0o644);
    }
    return { dir, files, licenseText, skillMd };
  }
}

/** Reads a response body into memory, refusing more than `max` bytes. */
async function collect(body: AsyncIterable<Uint8Array>, max: number, name: string): Promise<Buffer> {
  const parts: Buffer[] = [];
  let n = 0;
  for await (const chunk of body) {
    n += chunk.length;
    if (n > max) throw new ValidationError(`${name} is larger than ${Math.round(max / 1024 / 1024) || 1} MB`);
    parts.push(Buffer.from(chunk));
  }
  return Buffer.concat(parts);
}
