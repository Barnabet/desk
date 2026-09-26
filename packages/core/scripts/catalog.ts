/**
 * Catalog curation (spec §7).
 *
 *   pnpm catalog:pin [ids…] [--ref <ref>]   resolve refs to commits and (re)compute digests in catalog.json
 *   pnpm catalog:check [ids…]               install each entry for real, build its runtime, run its smoke command in the sandbox
 *   pnpm catalog:sync                       copy catalog/shared/*.py over the copies inside first-party skills' scripts/
 *
 * pin works on the raw JSON so new entries can start with `"sha": "HEAD"` and `"digest": "pending"`. Entries whose
 * repository archive is too large switch to files mode (the skill's files listed and fetched one by one). A Node
 * runtime with `lock_from` gets its lock (re)generated: from a package-lock.json inside the skill, or with
 * `npm:<spec> …` from npm itself (resolved as of the catalog date, no scripts run).
 */
import { execFileSync } from 'node:child_process';
import { existsSync, mkdtempSync, readdirSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { CatalogFile, type CatalogEntry } from '@desk/protocol';
import { detectLicense, flattenLock, shellWord as shq } from '../src/catalog/curation';
import {
  CatalogService,
  createModelAdapter,
  detectSandbox,
  EventStore,
  extractSubtree,
  httpFetch,
  isScript,
  ModelRegistry,
  openDb,
  parseSkillMd,
  readTree,
  runProcess,
  Runtime,
  scrubbedEnv,
  shellInvocation,
  SkillRuntimes,
  treeDigest,
  withSkillEnv,
  type ExtractedFile,
} from '../src/index';

const here = dirname(fileURLToPath(import.meta.url));
const CATALOG = join(here, '..', 'src', 'catalog', 'catalog.json');
const BUILTIN = join(here, '..', '..', '..', 'catalog', 'skills');
const SHARED = join(here, '..', '..', '..', 'catalog', 'shared');
const LICENSE_FILE = /^(LICEN[CS]E|COPYING)(\.[A-Za-z]+)?$/i;

const [command, ...rest] = process.argv.slice(2);
const refFlag = rest.indexOf('--ref');
const ref = refFlag >= 0 ? rest[refFlag + 1] : undefined;
const ids = rest.filter((a, i) => !a.startsWith('--') && (refFlag < 0 || i !== refFlag + 1));

if (command === 'pin') await pin();
else if (command === 'check') process.exit((await check()) ? 0 : 1);
else if (command === 'sync') sync();
else {
  console.error('usage: catalog.ts pin|check|sync [ids…] [--ref <ref>]');
  process.exit(2);
}

// ── sync ────────────────────────────────────────────────────────────────

/** First-party skills keep their own copies of catalog/shared modules (each skill installs alone); refresh them. */
function sync(): void {
  const shared = readdirSync(SHARED).filter((f) => f.endsWith('.py'));
  let changed = 0;
  for (const skill of readdirSync(BUILTIN)) {
    for (const name of shared) {
      const copy = join(BUILTIN, skill, 'scripts', name);
      if (!existsSync(copy)) continue;
      const want = readFileSync(join(SHARED, name), 'utf8');
      if (readFileSync(copy, 'utf8') === want) continue;
      writeFileSync(copy, want);
      changed++;
      console.log(`synced ${skill}/scripts/${name}`);
    }
  }
  console.log(changed ? `${changed} copies updated; run pnpm catalog:pin for the skills listed` : 'all copies are current');
}

// ── pin ─────────────────────────────────────────────────────────────────

type RawEntry = Omit<CatalogEntry, 'source' | 'digest' | 'runtime'> & {
  runtime?: CatalogEntry['runtime'];
  digest: string;
  source: { type: 'github'; repo: string; path: string; sha: string; files?: string[]; executable?: string[]; license_file?: string } | { type: 'builtin'; path: string };
};

async function pin(): Promise<void> {
  const raw = JSON.parse(readFileSync(CATALOG, 'utf8')) as { version: 1; updated: string; entries: RawEntry[] };
  raw.updated = new Date().toISOString().slice(0, 10);
  const selected = raw.entries.filter((e) => (ids.length ? ids.includes(e.id) : e.digest === 'pending' || (e.source.type === 'github' && !/^[0-9a-f]{40}$/.test(e.source.sha))));
  for (const e of selected) {
    try {
      const files = e.source.type === 'builtin' ? readTree(join(BUILTIN, e.source.path)) : await pinGithub(e.source);
      const junk = files.find((f) => /(^|\/)(__pycache__|\.DS_Store)(\/|$)|\.pyc$/.test(f.path));
      if (junk) throw new Error(`remove ${junk.path} first: it would become part of the pinned skill`);
      const md = files.find((f) => f.path === 'SKILL.md');
      if (!md) throw new Error('no SKILL.md');
      const fm = parseSkillMd(md.content.toString('utf8')).frontmatter;
      if (fm.name !== undefined && fm.name !== e.id) throw new Error(`SKILL.md names itself "${String(fm.name)}"`);
      const node = e.runtime?.node;
      if (node?.lock_from) {
        node.lock = lockFor(node.lock_from, files, raw.updated);
        console.log(`  ${e.id}: ${node.lock.length} Node packages locked from ${node.lock_from}`);
      }
      e.digest = treeDigest(files);
      e.files = files.length;
      e.bytes = files.reduce((n, f) => n + f.content.length, 0);
      e.scripts = files.filter((f) => isScript(f.path, f.mode, f.content)).length;
      console.log(`pinned ${e.id}: ${e.source.type === 'github' ? `${e.source.repo}@${e.source.sha.slice(0, 7)}${e.source.files ? ' (files mode)' : ''}` : 'builtin'} · ${e.files} files · ${e.bytes} bytes`);
    } catch (err) {
      console.error(`FAILED ${e.id}: ${(err as Error).message}`);
      process.exitCode = 1;
    }
  }
  writeFileSync(CATALOG, `${JSON.stringify(raw, null, 2)}\n`);
  const parsed = CatalogFile.safeParse(raw);
  if (!parsed.success) {
    console.error('catalog.json does not validate yet:', parsed.error.issues.slice(0, 5));
    process.exitCode = 1;
  }
}

async function pinGithub(src: Extract<RawEntry['source'], { type: 'github' }>): Promise<ExtractedFile[]> {
  const target = ref ?? (/^[0-9a-f]{40}$/.test(src.sha) ? src.sha : src.sha || 'HEAD');
  src.sha = /^[0-9a-f]{40}$/.test(target) ? target : resolveRef(src.repo, target);
  if (!src.files) {
    try {
      const { files } = await extractSubtree(await httpFetch(`https://codeload.github.com/${src.repo}/tar.gz/${src.sha}`), src.path, { caps: { maxDownload: 50 * 1024 * 1024 } });
      delete src.files;
      delete src.executable;
      delete src.license_file;
      return files;
    } catch (err) {
      if (!/download is larger/.test((err as Error).message)) throw err;
      console.log(`  ${src.repo} is too large to download whole; switching to files mode`);
    }
  }
  // Files mode: one tree listing, then each file from raw.githubusercontent.com.
  const res = await fetch(`https://api.github.com/repos/${src.repo}/git/trees/${src.sha}?recursive=1`, { headers: { accept: 'application/vnd.github+json', 'user-agent': 'Desk catalog curation' } });
  if (!res.ok) throw new Error(`tree listing failed (${res.status})`);
  const tree = (await res.json()) as { truncated: boolean; tree: Array<{ path: string; type: string; mode: string }> };
  if (tree.truncated) throw new Error('the tree listing is truncated');
  const prefix = src.path ? `${src.path}/` : '';
  const blobs = tree.tree.filter((t) => t.type === 'blob' && t.path.startsWith(prefix));
  if (tree.tree.some((t) => t.path.startsWith(prefix) && t.mode === '120000')) throw new Error('the skill contains symbolic links');
  src.files = blobs.map((b) => b.path.slice(prefix.length)).sort();
  const executable = blobs.filter((b) => b.mode === '100755').map((b) => b.path.slice(prefix.length));
  if (executable.length) src.executable = executable;
  else delete src.executable;
  const license = tree.tree.find((t) => t.type === 'blob' && !t.path.includes('/') && LICENSE_FILE.test(t.path));
  if (license && !src.files.some((f) => LICENSE_FILE.test(f))) src.license_file = license.path;
  const out: ExtractedFile[] = [];
  for (const rel of src.files) {
    const body = await httpFetch(`https://raw.githubusercontent.com/${src.repo}/${src.sha}/${(prefix + rel).split('/').map(encodeURIComponent).join('/')}`);
    const parts: Buffer[] = [];
    for await (const c of body) parts.push(Buffer.from(c));
    out.push({ path: rel, content: Buffer.concat(parts), mode: executable.includes(rel) ? 0o755 : 0o644 });
  }
  return out;
}

function lockFor(from: string, files: ExtractedFile[], updated: string): NonNullable<CatalogEntry['runtime']['node']>['lock'] {
  if (!from.startsWith('npm:')) {
    const file = files.find((f) => f.path === from);
    if (!file) throw new Error(`lock_from: ${from} is not in the skill`);
    return flattenLock(JSON.parse(file.content.toString('utf8')));
  }
  const dir = mkdtempSync(join(tmpdir(), 'desk-lock-'));
  try {
    writeFileSync(join(dir, 'package.json'), JSON.stringify({ name: 'desk-catalog-lock', version: '0.0.0', private: true }));
    const specs = from.slice(4).trim().split(/\s+/);
    execFileSync('npm', ['install', '--package-lock-only', '--ignore-scripts', '--no-audit', '--no-fund', '--save-exact', `--before=${updated}T23:59:59Z`, ...specs], { cwd: dir, stdio: ['ignore', 'ignore', 'inherit'] });
    return flattenLock(JSON.parse(readFileSync(join(dir, 'package-lock.json'), 'utf8')));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

function resolveRef(repo: string, name: string): string {
  const out = execFileSync('git', ['ls-remote', `https://github.com/${repo}`, name], { encoding: 'utf8' });
  const sha = out.split(/\s/)[0];
  if (!sha || !/^[0-9a-f]{40}$/.test(sha)) throw new Error(`cannot resolve ${repo}@${name}`);
  return sha;
}

// ── check ───────────────────────────────────────────────────────────────

async function check(): Promise<boolean> {
  const catalog = CatalogFile.parse(JSON.parse(readFileSync(CATALOG, 'utf8')));
  const entries = catalog.entries.filter((e) => !ids.length || ids.includes(e.id));
  const sandbox = await detectSandbox();
  if (!sandbox) console.warn('sandbox-exec is unavailable: smoke commands run unsandboxed');
  const dataDir = realpathSync(mkdtempSync(join(tmpdir(), 'desk-catalog-check-')));
  const { db, close } = openDb(':memory:');
  const store = new EventStore(db);
  const models = new ModelRegistry();
  let runtime: Runtime | null = null;
  const runtimes = new SkillRuntimes({ dataDir, store, uv: process.env.DESK_UV ?? which('uv'), nodeExec: process.execPath, exists: (r) => !!runtime?.skills.get(r.scope, r.name, r.projectId) });
  runtime = new Runtime({ store, models, dataDir, adapter: createModelAdapter({ baseURL: 'http://127.0.0.1:9/v1', apiKey: 'unused' }, models), skillEnv: runtimes, sandboxAvailable: sandbox });
  const service = new CatalogService({ runtime, store, dataDir, catalog, builtinRoot: BUILTIN, runtimes });
  let ok = true;
  for (const e of entries) {
    const ref = { scope: 'global' as const, name: e.id };
    const t0 = Date.now();
    try {
      const review = await service.prepare(e.id);
      const license = detectLicense(review.license_text, review.skill_md);
      if (license && license !== e.license) throw new Error(`licence is ${license}, catalog says ${e.license}`);
      if (!license) console.warn(`  ${e.id}: could not detect the licence; catalog says ${e.license}`);
      for (const w of review.warnings) console.warn(`  ${e.id}: warning ${w.kind} at ${w.file}:${w.line}: ${w.excerpt}`);
      await service.install(e.id);
      await runtimes.settled(ref);
      const rt = runtimes.state(ref);
      if (rt.state === 'failed') throw new Error(`runtime failed: ${rt.reason}`);
      let smoke = 'no smoke command';
      if (e.smoke?.length) {
        const skill = runtime.skills.get('global', e.id)!;
        const ws = mkdtempSync(join(dataDir, 'ws-'));
        const cmd = e.smoke.map(shq).join(' ');
        const r = await runProcess({
          ...shellInvocation(`cd ${shq(skill.dir)} && ${cmd}`, { enabled: sandbox, writable: [ws] }),
          cwd: ws,
          env: { ...withSkillEnv(scrubbedEnv(ws), runtimes.env(ref)), SKILL_DIR: skill.dir, SKILL_NAME: e.id },
          timeoutMs: 180_000,
        });
        if (r.exitCode !== 0) throw new Error(`smoke exited ${r.exitCode}: ${r.output.trim().split('\n').slice(-8).join('\n')}`);
        smoke = `smoke ok (${r.output.trim().split('\n')[0]?.slice(0, 80) ?? ''})`;
      }
      console.log(`PASS ${e.id} · runtime ${rt.state} · ${smoke} · ${((Date.now() - t0) / 1000).toFixed(1)}s`);
    } catch (err) {
      ok = false;
      console.log(`FAIL ${e.id}: ${(err as Error).message}`);
    }
  }
  await runtime.shutdown();
  close();
  if (!process.env.DESK_CATALOG_KEEP) rmSync(dataDir, { recursive: true, force: true });
  else console.log(`kept ${dataDir}`);
  return ok;
}

function which(bin: string): string | null {
  try {
    return execFileSync('/usr/bin/which', [bin], { encoding: 'utf8' }).trim() || null;
  } catch {
    return null;
  }
}
