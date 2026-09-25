import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, openSync, readFileSync } from 'node:fs';
import { basename, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Command, CommanderError } from 'commander';
import type { CatalogItem, CatalogReview, StreamServerMessage } from '@desk/protocol';
import type { ServiceRow } from '@desk/client';
import { runChat } from './chat';
import { ApiError, clientFromDataDir, DeskClient, platformDataDir, readDaemonInfo } from './client';
import { createRenderer } from './format';
import { install, isInstalled, plistFor, uninstall } from './launchd';

export type CliIO = {
  out(s: string): void;
  err(s: string): void;
  dataDir?: string;
  env?: NodeJS.ProcessEnv;
  /** Asks a yes/no question on a terminal; absent when there is none (then --yes is required). */
  confirm?(question: string): Promise<boolean>;
};

type Project = { id: string; name: string; goal: string; settings: Record<string, unknown>; archived_at: string | null };

const INT_SETTINGS = new Set(['max_concurrent_threads', 'review_rounds']);
const EFFORT_SETTINGS = new Set(['desk_reasoning_effort', 'thread_reasoning_effort']);
const SETTINGS = new Set(['check_in', 'autonomy', 'desk_model', 'thread_model', 'fallback_model', ...EFFORT_SETTINGS, ...INT_SETTINGS]);
const FIELDS = new Set(['name', 'goal', 'instructions']);

const repoRoot = () => fileURLToPath(new URL('../../..', import.meta.url));
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

const CATEGORY_TITLES: Array<[CatalogItem['category'], string]> = [
  ['research', 'Research'],
  ['documents', 'Documents & data'],
  ['writing', 'Writing & diagrams'],
  ['planning', 'Planning'],
  ['code', 'Code'],
];
const CATALOG_STATE: Record<CatalogItem['installs'][number]['state'], string> = {
  installed: 'installed',
  update_available: 'update available',
  modified: 'installed, edited since',
  name_taken: 'name taken by another skill',
  not_installed: 'not installed',
};
const RUNTIME_STATE = { none: '', preparing: 'setting up', ready: 'ready', failed: 'setup failed' } as const;

function runtimeWords(e: CatalogReview['entry']): string {
  const parts = [e.runtime.python ? `Python ${e.runtime.python.version}` : null, e.runtime.node ? 'Node' : null, e.runtime.extras?.includes('playwright-chromium') ? 'Chromium' : null, e.runtime.extras?.includes('browser') ? 'browser' : null].filter(Boolean);
  return parts.length ? parts.join(' + ') : 'nothing';
}

const kb = (n: number) => (n < 1024 ? `${n} B` : n < 1024 * 1024 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`);

/** A catalog review as text: source and pin, licence, what Desk sets up, caveats, warnings and every file. */
export function renderReview(r: CatalogReview): string {
  const e = r.entry;
  const packages = [...(e.runtime.python?.packages ?? []), ...(e.runtime.node?.lock ?? []).filter((l) => l.path === `node_modules/${l.name}`).map((l) => `${l.name}@${l.version}`)];
  const lines = [
    `${e.title} (${e.id}) — ${e.license}`,
    e.summary,
    '',
    `Source:   ${r.source_url ?? 'Desk (first-party, shipped with the app)'}`,
    `Pinned:   ${r.files.length} files, ${kb(e.bytes)}, ${e.digest.slice(0, 19)}…`,
    `Sets up:  ${runtimeWords(e)}${packages.length ? ` (${packages.join(', ')})` : ''}`,
  ];
  if (e.caveats.length) lines.push('', 'Good to know:', ...e.caveats.map((c) => `  - ${c}`));
  if (r.warnings.length) lines.push('', `Worth a look (${r.warnings.length}):`, ...r.warnings.map((w) => `  - ${w.file}:${w.line}  ${w.kind}  ${w.excerpt}`));
  lines.push('', 'Files:', ...r.files.map((f) => `  ${f.path}  ${kb(f.size)}${f.script ? '  script' : ''}`));
  return lines.join('\n');
}

export function defaultDataDir(env: NodeJS.ProcessEnv = process.env): string {
  return platformDataDir(env);
}

async function resolveProject(client: DeskClient, ref: string): Promise<Project> {
  const all = await client.get<Project[]>('/projects?all=1');
  const lower = ref.toLowerCase();
  const hit =
    all.find((p) => p.id === ref) ??
    all.filter((p) => p.name.toLowerCase() === lower).at(0) ??
    (() => {
      const prefixed = all.filter((p) => p.id.startsWith(ref.toUpperCase()));
      return prefixed.length === 1 ? prefixed[0] : undefined;
    })();
  if (!hit) throw new Error(`No project matches "${ref}". See: desk project list`);
  return hit;
}

function parseAssignments(pairs: string[]): { fields: Record<string, string>; settings: Record<string, unknown> } {
  const fields: Record<string, string> = {};
  const settings: Record<string, unknown> = {};
  for (const pair of pairs) {
    const eq = pair.indexOf('=');
    if (eq <= 0) throw new Error(`Expected key=value, got "${pair}"`);
    const key = pair.slice(0, eq);
    const value = pair.slice(eq + 1);
    if (FIELDS.has(key)) fields[key] = value;
    else if (INT_SETTINGS.has(key)) settings[key] = Number(value);
    else if (key === 'fallback_model') settings[key] = value === '' || value === 'none' ? null : value;
    // `none` is a real level (no reasoning); `default` (or empty) goes back to the model's default.
    else if (EFFORT_SETTINGS.has(key)) settings[key] = value === '' || value === 'default' ? null : value;
    else if (SETTINGS.has(key)) settings[key] = value;
    else throw new Error(`Unknown setting "${key}". Settable: ${[...FIELDS, ...SETTINGS].join(', ')}`);
  }
  return { fields, settings };
}

/** Streams a Desk turn: subscribes from "now", sends, renders until Desk stops running. */
async function sayAndFollow(client: DeskClient, project: Project, text: string, io: CliIO, timeoutMs: number): Promise<void> {
  const overview = await client.get(`/projects/${project.id}`);
  const deskId: string = overview.desk.id;
  const render = createRenderer(io.out, { deskId });
  let finish!: () => void;
  const finished = new Promise<void>((r) => (finish = r));
  // Follow until Desk is done: idle/failed/cancelled, or waiting on a question to the user.
  // Waiting on threads keeps following, so the whole coordinated job is shown.
  let running = false;
  let askedUser = false;
  const close = await client.stream(project.id, overview.last_seq, (m: StreamServerMessage) => {
    render(m);
    if (m.kind !== 'event' || m.event.agent_id !== deskId) return;
    if (m.event.type === 'question.asked') askedUser = true;
    if (m.event.type !== 'agent.status_changed') return;
    const status = m.event.payload.status;
    if (status === 'running') {
      running = true;
      askedUser = false;
    } else if (running && (['idle', 'failed', 'cancelled'].includes(status) || (status === 'waiting' && askedUser))) finish();
  });
  try {
    await client.post(`/projects/${project.id}/messages`, { text });
    const timedOut = await Promise.race([finished.then(() => false), sleep(timeoutMs).then(() => true)]);
    if (timedOut) io.out(`\n(still working — follow with: desk chat ${project.name})\n`);
  } finally {
    close();
  }
}

export async function runCli(argv: string[], io: CliIO): Promise<number> {
  const env = io.env ?? process.env;
  const dataDir = io.dataDir ?? defaultDataDir(env);
  const client = () => clientFromDataDir(dataDir);
  const say = (s: string) => io.out(s.endsWith('\n') ? s : `${s}\n`);

  const program = new Command('desk')
    .description('Desk — local project coordinator (dev client for deskd)')
    .exitOverride()
    .configureOutput({ writeOut: io.out, writeErr: io.err });

  // ── daemon ─────────────────────────────────────────────────────────
  program
    .command('up')
    .description('Start deskd in the background (or install it as a login LaunchAgent with --install)')
    .option('--install', 'install as a launchd LaunchAgent (starts at login, restarts on exit)')
    .option('--port <port>', 'port to listen on')
    .action(async (opts: { install?: boolean; port?: string }) => {
      const info = readDaemonInfo(dataDir);
      if (info) {
        try {
          const h = await new DeskClient({ baseUrl: `http://127.0.0.1:${info.port}`, token: info.token }).get('/health');
          say(`deskd ${h.version} is already running on 127.0.0.1:${info.port}`);
          return;
        } catch {}
      }
      const root = repoRoot();
      const loader = join(root, 'node_modules', 'tsx', 'dist', 'loader.mjs');
      const entry = join(root, 'apps', 'daemon', 'src', 'main.ts');
      mkdirSync(join(dataDir, 'logs'), { recursive: true });
      if (opts.install) {
        install(plistFor({ nodePath: process.execPath, loader, entry, dataDir, cwd: root }), dataDir);
      } else {
        const log = openSync(join(dataDir, 'logs', 'deskd.out.log'), 'a');
        spawn(process.execPath, ['--import', loader, entry, '--data-dir', dataDir, ...(opts.port ? ['--port', opts.port] : [])], {
          cwd: root,
          detached: true,
          stdio: ['ignore', log, log],
        }).unref();
      }
      for (let i = 0; i < 60; i++) {
        await sleep(250);
        const started = readDaemonInfo(dataDir);
        if (started) {
          say(`deskd started on 127.0.0.1:${started.port}${opts.install ? ' (LaunchAgent installed)' : ''}`);
          return;
        }
      }
      throw new Error(`deskd did not start; see ${join(dataDir, 'logs')}`);
    });

  program
    .command('down')
    .description('Stop deskd (and uninstall the LaunchAgent if installed)')
    .action(async () => {
      if (isInstalled()) uninstall();
      const info = readDaemonInfo(dataDir);
      if (info) {
        try {
          process.kill(info.pid, 'SIGTERM');
        } catch {}
      }
      for (let i = 0; i < 60 && readDaemonInfo(dataDir); i++) await sleep(250);
      say(readDaemonInfo(dataDir) ? 'deskd did not stop in time' : 'deskd stopped');
    });

  program
    .command('status')
    .description('Show whether deskd is running')
    .action(async () => {
      const info = readDaemonInfo(dataDir);
      if (!info) throw new Error('deskd is not running');
      const c = client();
      const h = await c.get('/health');
      const projects = await c.get<Project[]>('/projects');
      say(`deskd ${h.version} on 127.0.0.1:${info.port} (pid ${info.pid}) — ${projects.length} project(s)${isInstalled() ? ', LaunchAgent installed' : ''}`);
    });

  // ── projects ───────────────────────────────────────────────────────
  const project = program.command('project').description('Manage projects');
  project
    .command('new <name>')
    .option('--goal <goal>', 'project goal', '')
    .option('--instructions <text>', 'standing instructions for Desk')
    .option('--source <path...>', 'folders or git repos to attach')
    .action(async (name: string, opts: { goal: string; instructions?: string; source?: string[] }) => {
      const created = await client().post('/projects', {
        name,
        goal: opts.goal,
        ...(opts.instructions ? { instructions: opts.instructions } : {}),
        ...(opts.source ? { sources: opts.source.map((p) => ({ path: resolve(p) })) } : {}),
      });
      say(`Created project ${created.project.id} "${name}"`);
    });
  project.command('list').action(async () => {
    const all = await client().get<Project[]>('/projects');
    say(all.length ? all.map((p) => `${p.id}  ${p.name}${p.goal ? ` — ${p.goal}` : ''}`).join('\n') : 'No projects. Create one: desk project new <name> --goal "..."');
  });
  project.command('show <project>').action(async (ref: string) => {
    const c = client();
    const p = await resolveProject(c, ref);
    const o = await c.get(`/projects/${p.id}`);
    const s = o.project.settings;
    // The overview includes archived threads; the summary lists the live ones.
    const threads = o.threads.filter((t: any) => !t.archived_at);
    say(
      [
        `${o.project.name} (${o.project.id})${o.project.archived_at ? ' [archived]' : ''}`,
        `Goal: ${o.project.goal || '(none)'}`,
        ...(o.project.instructions ? [`Instructions: ${o.project.instructions}`] : []),
        `Settings: check_in=${s.check_in} autonomy=${s.autonomy} desk_model=${s.desk_model} thread_model=${s.thread_model} desk_reasoning_effort=${s.desk_reasoning_effort ?? 'default'} thread_reasoning_effort=${s.thread_reasoning_effort ?? 'default'} max_concurrent_threads=${s.max_concurrent_threads} review_rounds=${s.review_rounds}`,
        `Desk: ${o.desk.status}`,
        `Sources:${o.sources.length ? '' : ' (none)'}`,
        ...o.sources.map((x: any) => `- ${x.id} ${x.label} (${x.kind}) ${x.path}`),
        `Plan:${o.plan?.items?.length ? '' : ' (none)'}`,
        ...(o.plan?.items ?? []).map((i: any) => `- [${i.status}] ${i.title}`),
        `Threads:${threads.length ? '' : ' (none)'}`,
        ...threads.map((t: any) => `- ${t.id} "${t.title}" [${t.status}]${t.result_summary ? ` — ${t.result_summary.split('\n')[0]}` : ''}`),
        ...(o.approvals.length ? ['Pending approvals:', ...o.approvals.map((a: any) => `- ${a.id} ${a.tool} — ${a.reason}`)] : []),
      ].join('\n'),
    );
  });
  project.command('set <project> <assignments...>').description('Set fields/settings, e.g. check_in=minimal goal="..."').action(async (ref: string, pairs: string[]) => {
    const c = client();
    const p = await resolveProject(c, ref);
    const { fields, settings } = parseAssignments(pairs);
    await c.patch(`/projects/${p.id}`, { ...fields, ...(Object.keys(settings).length ? { settings } : {}) });
    say(`Updated ${p.name}`);
  });
  project.command('archive <project>').action(async (ref: string) => {
    const c = client();
    const p = await resolveProject(c, ref);
    await c.post(`/projects/${p.id}/archive`);
    say(`Archived ${p.name}`);
  });

  const source = program.command('source').description('Attach or detach project sources');
  source
    .command('add <project> <path>')
    .option('--label <label>')
    .action(async (ref: string, path: string, opts: { label?: string }) => {
      const c = client();
      const p = await resolveProject(c, ref);
      const s = await c.post(`/projects/${p.id}/sources`, { path: resolve(path), ...(opts.label ? { label: opts.label } : {}) });
      say(`Added ${s.kind} source ${s.id} ${s.label}`);
    });
  source.command('rm <project> <sourceId>').action(async (ref: string, id: string) => {
    const c = client();
    const p = await resolveProject(c, ref);
    await c.del(`/projects/${p.id}/sources/${id}`);
    say(`Removed source ${id}`);
  });

  // ── conversation ───────────────────────────────────────────────────
  program
    .command('say <project> <text...>')
    .description("Message the project's Desk and stream its turn")
    .option('--no-wait', 'send without waiting for the reply')
    .option('--timeout <seconds>', 'max seconds to follow the turn', '600')
    .action(async (ref: string, words: string[], opts: { wait: boolean; timeout: string }) => {
      const c = client();
      const p = await resolveProject(c, ref);
      const text = words.join(' ');
      if (!opts.wait) {
        await c.post(`/projects/${p.id}/messages`, { text });
        say('Sent.');
        return;
      }
      await sayAndFollow(c, p, text, io, Number(opts.timeout) * 1000);
    });
  program
    .command('chat <project>')
    .description('Interactive chat with Desk (live stream; /help for commands)')
    .action(async (ref: string) => {
      const c = client();
      await runChat(c, await resolveProject(c, ref), io);
    });

  // ── threads & approvals ────────────────────────────────────────────
  program.command('threads <project>').action(async (ref: string) => {
    const c = client();
    const p = await resolveProject(c, ref);
    const threads = await c.get<any[]>(`/projects/${p.id}/threads`);
    say(
      threads.length
        ? threads
            .map((t) => `${t.id} "${t.title}" [${t.status}] ${t.model}${t.git_branch ? ` ${t.git_branch}` : ''}${t.result_summary ? `\n    ${t.result_summary.split('\n')[0]}` : ''}`)
            .join('\n')
        : 'No threads',
    );
  });
  const serviceLine = (x: ServiceRow) =>
    `${x.name}  ${x.status === 'running' ? `running${x.url ? ` ${x.url}` : ''}` : x.status === 'exited' ? `exited (${x.exit_signal ?? x.exit_code})` : `stopped (${x.stop_reason})`}  ${x.command}`;
  program.command('services <project>').description('List the project\'s services').action(async (ref: string) => {
    const c = client();
    const p = await resolveProject(c, ref);
    const all = await c.services.list(p.id);
    say(all.length ? all.map(serviceLine).join('\n') : 'No services');
  });
  program
    .command('service <action> <project> <name>')
    .description('logs | start | stop | restart a project service')
    .option('-n, --lines <n>', 'log lines', '80')
    .action(async (action: string, ref: string, name: string, opts: { lines: string }) => {
      const c = client();
      const p = await resolveProject(c, ref);
      const svc = (await c.services.list(p.id)).find((x) => x.name === name);
      if (!svc) throw new Error(`No service named "${name}" in ${p.name}`);
      if (action === 'logs') return say((await c.services.logs(svc.id, Number(opts.lines) || 80)).text || '(no output)');
      if (action === 'start' || action === 'stop' || action === 'restart') return say(serviceLine(await c.services[action](svc.id)));
      throw new Error(`Unknown action "${action}": use logs, start, stop or restart`);
    });
  program
    .command('tail <thread>')
    .option('-f, --follow', 'keep streaming')
    .action(async (id: string, opts: { follow?: boolean }) => {
      const c = client();
      const t = await c.get(`/threads/${id}`);
      const render = createRenderer(io.out, { deskId: t.id, label: `"${t.title}"`, verbose: true });
      const { events } = await c.get(`/threads/${id}/transcript`);
      for (const event of events) render({ kind: 'event', event });
      if (!opts.follow) return;
      const last = events.at(-1)?.id ?? 0;
      await new Promise<void>((resolveDone) => {
        void c.stream(t.project_id, last, (m) => {
          if ((m.kind === 'event' || m.kind === 'ephemeral') && m.event.agent_id !== t.id) return;
          render(m);
        });
        process.once('SIGINT', () => resolveDone());
      });
    });
  program
    .command('tell <thread> <text...>')
    .description('Message a thread directly')
    .option('--ask', 'ask a question: a finished thread answers from its context and stays finished')
    .action(async (id: string, words: string[], opts: { ask?: boolean }) => {
      await client().post(`/threads/${id}/messages`, { text: words.join(' '), ...(opts.ask ? { question: true } : {}) });
      say(opts.ask ? `Asked ${id}` : `Sent to ${id}`);
    });
  program.command('stop <thread>').action(async (id: string) => {
    await client().post(`/threads/${id}/stop`);
    say(`Stopped ${id}`);
  });
  program.command('approvals <project>').action(async (ref: string) => {
    const c = client();
    const p = await resolveProject(c, ref);
    const list = await c.get<any[]>(`/projects/${p.id}/approvals?status=pending`);
    say(list.length ? list.map((a) => `${a.id}  ${a.tool} ${a.arguments}  from ${a.agent_id} — ${a.reason}`).join('\n') : 'No pending approvals');
  });
  for (const [cmd, decision, verb] of [
    ['approve', 'approved', 'Approved'],
    ['deny', 'denied', 'Denied'],
  ] as const) {
    program.command(`${cmd} <approval> [note...]`).action(async (id: string, note: string[] = []) => {
      await client().post(`/approvals/${id}/resolve`, { decision, ...(note.length ? { note: note.join(' ') } : {}) });
      say(`${verb} ${id}`);
    });
  }

  // ── memory, library, usage ─────────────────────────────────────────
  program.command('memory <project> [query...]').description('List or search project memory').action(async (ref: string, query: string[] = []) => {
    const c = client();
    const p = await resolveProject(c, ref);
    const rows = await c.get<any[]>(`/projects/${p.id}/memory${query.length ? `?q=${encodeURIComponent(query.join(' '))}` : ''}`);
    say(rows.length ? rows.map((m) => `[${m.kind}] ${m.content} (${m.id})`).join('\n') : 'No memory entries');
  });
  program.command('remember <project> <kind> <text...>').description('kind: fact|decision|preference|contact|note').action(async (ref: string, kind: string, words: string[]) => {
    const c = client();
    const p = await resolveProject(c, ref);
    const m = await c.post(`/projects/${p.id}/memory`, { kind, content: words.join(' ') });
    say(`Saved memory ${m.id}`);
  });
  program.command('forget <project> <memoryId>').action(async (ref: string, id: string) => {
    const c = client();
    const p = await resolveProject(c, ref);
    await c.del(`/projects/${p.id}/memory/${id}`);
    say(`Deleted memory ${id}`);
  });
  program.command('library <project>').action(async (ref: string) => {
    const c = client();
    const p = await resolveProject(c, ref);
    const items = await c.get<any[]>(`/projects/${p.id}/library`);
    say(items.length ? items.map((a) => `${a.path} — ${a.title} [${a.kind}] (${a.origin})`).join('\n') : 'The library is empty');
  });
  program.command('upload <project> <file>').option('--title <title>').action(async (ref: string, file: string, opts: { title?: string }) => {
    const c = client();
    const p = await resolveProject(c, ref);
    if (!existsSync(file)) throw new Error(`No such file: ${file}`);
    const a = await c.post(`/projects/${p.id}/library`, {
      name: basename(file),
      content_base64: readFileSync(file).toString('base64'),
      ...(opts.title ? { title: opts.title } : {}),
    });
    say(`Uploaded ${a.path}`);
  });
  const skillsBase = async (c: DeskClient, ref?: string) => (ref ? `/projects/${(await resolveProject(c, ref)).id}/skills` : '/skills');
  const skillLine = (s: any) => `${s.name} (${s.scope}, v${s.version}) — ${s.error ? `BROKEN: ${s.error}` : s.description}`;
  program
    .command('skills [project]')
    .description('List global skills, or every skill visible in a project')
    .action(async (ref?: string) => {
      const c = client();
      const rows = await c.get<any[]>(await skillsBase(c, ref));
      say(rows.length ? rows.map(skillLine).join('\n') : 'No skills');
    });
  const skill = program.command('skill').description('Manage skills (global unless --project is given)');
  skill.command('show <name>').option('-p, --project <project>').action(async (name: string, opts: { project?: string }) => {
    const c = client();
    const s = await c.get(`${await skillsBase(c, opts.project)}/${name}`);
    say([skillLine(s), `Directory: ${s.dir}`, `Files: ${s.files.map((f: any) => f.path).join(', ')}`, '', s.instructions].join('\n'));
  });
  skill
    .command('import <path>')
    .description('Import a skill directory containing a SKILL.md (e.g. from ~/.claude/skills)')
    .option('-p, --project <project>')
    .option('--name <name>')
    .action(async (path: string, opts: { project?: string; name?: string }) => {
      const c = client();
      const r = await c.post(`${await skillsBase(c, opts.project)}/import`, { path: resolve(path), ...(opts.name ? { name: opts.name } : {}) });
      say(`Imported skill v${r.version} to ${r.dir}`);
    });
  skill.command('rm <name>').option('-p, --project <project>').action(async (name: string, opts: { project?: string }) => {
    const c = client();
    await c.del(`${await skillsBase(c, opts.project)}/${name}`);
    say(`Deleted skill ${name} (restorable with desk skill restore)`);
  });
  skill.command('history <name>').option('-p, --project <project>').action(async (name: string, opts: { project?: string }) => {
    const c = client();
    const rows = await c.get<any[]>(`${await skillsBase(c, opts.project)}/${name}/history`);
    say(rows.map((h) => `v${h.version}${h.current ? ' (current)' : ''} — ${h.description}`).join('\n'));
  });
  skill.command('restore <name> <version>').option('-p, --project <project>').action(async (name: string, version: string, opts: { project?: string }) => {
    const c = client();
    const r = await c.post(`${await skillsBase(c, opts.project)}/${name}/restore`, { version: Number(version) });
    say(`Restored ${name} v${version} as v${r.version}`);
  });
  // ── catalog ────────────────────────────────────────────────────────
  const catalogState = (item: CatalogItem, projectNames: Map<string, string>) => {
    const global = item.installs.find((i) => i.scope === 'global');
    const words: string[] = [];
    if (global) words.push(CATALOG_STATE[global.state] + (global.runtime === 'none' || global.state === 'name_taken' ? '' : ` · ${RUNTIME_STATE[global.runtime]}`));
    const inProjects = item.installs.filter((i) => i.scope === 'project' && i.state !== 'name_taken').map((i) => projectNames.get(i.project_id!) ?? i.project_id!);
    if (inProjects.length) words.push(`in ${inProjects.join(', ')}`);
    return words.join('; ');
  };
  const catalogCmd = program
    .command('catalog')
    .description('Browse and install skills from the catalog')
    .action(async () => {
      const c = client();
      const [items, projects] = await Promise.all([c.catalog.list(), c.get<Project[]>('/projects')]);
      const names = new Map(projects.map((p) => [p.id, p.name]));
      const lines: string[] = [];
      for (const [category, title] of CATEGORY_TITLES) {
        const inBay = items.filter((i) => i.category === category);
        if (!inBay.length) continue;
        lines.push(title);
        for (const i of inBay) {
          const state = catalogState(i, names);
          lines.push(`  ${i.id.padEnd(32)} ${i.summary.length > 72 ? `${i.summary.slice(0, 71)}…` : i.summary}${state ? `  [${state}]` : ''}`);
        }
        lines.push('');
      }
      say(`${lines.join('\n').trimEnd()}\n\nReview one with: desk catalog show <id>`);
    });
  catalogCmd
    .command('show <id>')
    .description('Fetch a catalog skill at its pinned version and print what to review before installing')
    .action(async (id: string) => {
      say(renderReview(await client().catalog.prepare(id)));
    });
  catalogCmd
    .command('install <id>')
    .description('Install a catalog skill (global unless --project is given), after showing its review')
    .option('-p, --project <project>')
    .option('-y, --yes', 'install without asking')
    .option('--replace', 'replace local edits to a catalog skill')
    .action(async (id: string, opts: { project?: string; yes?: boolean; replace?: boolean }) => {
      const c = client();
      const project = opts.project ? await resolveProject(c, opts.project) : null;
      const review = await c.catalog.prepare(id);
      say(renderReview(review));
      if (!opts.yes) {
        if (!io.confirm) throw new Error('Pass --yes to install without a prompt.');
        if (!(await io.confirm(`Install ${id} ${project ? `in ${project.name}` : 'for every project'}?`))) {
          say('Not installed.');
          return;
        }
      }
      const r = await c.catalog.install(id, { scope: project ? 'project' : 'global', ...(project ? { project_id: project.id } : {}), ...(opts.replace ? { replace_modified: true } : {}) });
      const where = project ? `in ${project.name}` : 'globally';
      say(
        `Installed ${r.skill.name} ${where} (v${r.skill.version}).` +
          (r.runtime === 'preparing' ? ` Desk is setting up ${runtimeWords(review.entry)}; see progress with: desk catalog` : r.runtime === 'failed' ? ' Its runtime setup failed; see: desk catalog' : ''),
      );
    });

  program.command('usage <project>').action(async (ref: string) => {
    const c = client();
    const p = await resolveProject(c, ref);
    const u = await c.get(`/projects/${p.id}/usage`);
    const byModel = new Map<string, { prompt: number; completion: number }>();
    for (const r of u.rows) {
      const m = byModel.get(r.model) ?? { prompt: 0, completion: 0 };
      m.prompt += r.prompt_tokens;
      m.completion += r.completion_tokens;
      byModel.set(r.model, m);
    }
    say(
      [
        'model                prompt   completion',
        ...[...byModel].map(([model, t]) => `${model.padEnd(20)} ${String(t.prompt).padStart(7)}  ${String(t.completion).padStart(10)}`),
        `${'total'.padEnd(20)} ${String(u.totals.prompt_tokens).padStart(7)}  ${String(u.totals.completion_tokens).padStart(10)}`,
      ].join('\n'),
    );
  });

  try {
    await program.parseAsync(argv, { from: 'user' });
    return 0;
  } catch (err) {
    if (err instanceof CommanderError) return err.exitCode === 0 || err.code === 'commander.helpDisplayed' ? 0 : 1;
    io.err(`error: ${err instanceof ApiError || err instanceof Error ? err.message : String(err)}\n`);
    return 1;
  }
}
