import { accessSync, constants, existsSync, readFileSync } from 'node:fs';
import { extname, isAbsolute, join } from 'node:path';
import { SkillName, SkillScope } from '@desk/protocol';
import { z } from 'zod';
import type { SkillDetail, SkillStore, SkillSummary } from '../skills/store';
import { getAgent } from '../state/queries';
import { scrubbedEnv, withSkillEnv } from './bash';
import { runProcess } from './process';
import { shellInvocation } from './sandbox';
import { defineTool, type ToolContext } from './types';

const MAX_READ_CHARS = 50_000;

const INTERPRETERS: Record<string, string> = {
  '.py': 'python3',
  '.sh': 'bash',
  '.bash': 'bash',
  '.zsh': 'zsh',
  '.js': 'node',
  '.mjs': 'node',
  '.cjs': 'node',
  '.ts': 'node',
  '.rb': 'ruby',
  '.pl': 'perl',
};

export const shellQuote = (s: string): string => `'${s.replace(/'/g, `'\\''`)}'`;

function resolveSkill(ctx: ToolContext, name: string): SkillSummary {
  const skill = ctx.services.skills.resolve(name, ctx.projectId);
  if (!skill) throw new Error(`Unknown skill: ${name}. Use skill_list to see the available skills.`);
  if (skill.error) throw new Error(`Skill ${name} is broken: ${skill.error}`);
  return skill;
}

function detail(store: SkillStore, s: SkillSummary, projectId: string): SkillDetail {
  return store.get(s.scope, s.name, s.scope === 'project' ? projectId : undefined)!;
}

export function formatSkillLine(s: SkillSummary, active?: Set<string>): string {
  const flags = [s.scope, `v${s.version}`, ...(active?.has(s.name) ? ['active'] : [])].join(', ');
  return `- ${s.name} (${flags}) — ${s.error ? `BROKEN: ${s.error}` : s.description}`;
}

/** Instructions of a skill as an agent sees them: location, files and the SKILL.md body. */
export function renderSkill(d: SkillDetail, maxChars = Infinity): string {
  const files = d.files.filter((f) => f.path !== 'SKILL.md').map((f) => `  ${f.path} (${f.size} bytes)`);
  const body = d.instructions.length > maxChars ? `${d.instructions.slice(0, maxChars)}\n[… truncated — read the rest with skill_read]` : d.instructions;
  return [
    `### Skill: ${d.name} (${d.scope}, v${d.version})`,
    `Directory: ${d.dir}`,
    ...(files.length ? ['Files:', ...files] : []),
    '',
    body,
  ].join('\n');
}

function locateScript(skill: SkillSummary, script: string, store: SkillStore): string {
  try {
    return store.filePath(skill, script);
  } catch (e) {
    if (!script.includes('/') && existsSync(join(skill.dir, 'scripts', script))) return store.filePath(skill, `scripts/${script}`);
    throw e;
  }
}

function commandFor(file: string): string {
  let executable = false;
  try {
    accessSync(file, constants.X_OK);
    executable = true;
  } catch {}
  const shebang = readFileSync(file, { encoding: 'utf8', flag: 'r' }).startsWith('#!');
  if (executable && shebang) return shellQuote(file);
  const interpreter = INTERPRETERS[extname(file).toLowerCase()];
  if (interpreter) return `${interpreter} ${shellQuote(file)}`;
  if (executable) return shellQuote(file);
  throw new Error(`Cannot tell how to run ${file}: add a shebang line or use a known extension (${Object.keys(INTERPRETERS).join(' ')})`);
}

export const skillListTool = defineTool({
  name: 'skill_list',
  description: 'List the skills available in this project (project skills shadow global ones), optionally filtered by words matched against names and descriptions.',
  input: z.object({ query: z.string().optional() }),
  async execute({ query }, ctx) {
    const active = new Set(getAgent(ctx.services.store.db, ctx.agentId)?.active_skills ?? []);
    const words = (query ?? '').toLowerCase().split(/\s+/).filter(Boolean);
    const skills = ctx.services.skills
      .list(ctx.projectId)
      .filter((s) => words.every((w) => `${s.name} ${s.description}`.toLowerCase().includes(w)));
    if (!skills.length) return query ? `No skills match "${query}".` : 'No skills yet.';
    return skills.map((s) => formatSkillLine(s, active)).join('\n');
  },
});

export const skillReadTool = defineTool({
  name: 'skill_read',
  description: "Read a skill: its instructions and file list, or one of its files (e.g. path 'scripts/run.py' or 'references/guide.md').",
  input: z.object({ name: SkillName, path: z.string().optional() }),
  async execute({ name, path }, ctx) {
    const skill = resolveSkill(ctx, name);
    const store = ctx.services.skills;
    if (!path || path === 'SKILL.md') return renderSkill(detail(store, skill, ctx.projectId), MAX_READ_CHARS);
    const buf = readFileSync(store.filePath(skill, path));
    if (buf.subarray(0, 8000).includes(0)) return `${path} is a binary file (${buf.length} bytes) at ${join(skill.dir, path)}.`;
    const text = buf.toString('utf8');
    return text.length > MAX_READ_CHARS ? `${text.slice(0, MAX_READ_CHARS)}\n[… truncated at ${MAX_READ_CHARS} characters]` : text;
  },
});

export const skillActivateTool = defineTool({
  name: 'skill_activate',
  description:
    'Activate a skill for yourself: returns its instructions now, and keeps them in your context for the rest of your work. Activate a skill whenever its description matches what you are doing.',
  input: z.object({ name: SkillName }),
  async execute({ name }, ctx) {
    const [skill] = ctx.services.activateSkills(ctx.agentId, [name]);
    return `Skill "${name}" is active. Follow its instructions:\n\n${renderSkill(detail(ctx.services.skills, skill!, ctx.projectId))}`;
  },
});

export const skillRunTool = defineTool({
  name: 'skill_run',
  description:
    "Run a script from a skill (e.g. script 'scripts/report.py' or just 'report.py') with arguments, in your workspace directory, sandboxed like bash. SKILL_DIR points to the skill's directory. Output is stdout+stderr.",
  input: z.object({
    name: SkillName,
    script: z.string().min(1),
    args: z.array(z.string()).default([]),
    stdin: z.string().optional(),
    timeout_s: z.number().int().min(1).max(600).default(120),
  }),
  gate: { subject: (i) => ({ command: [`${i.name}/${i.script}`, ...i.args].join(' ') }), unmatched: 'auto' },
  async execute({ name, script, args, stdin, timeout_s }, ctx) {
    const skill = resolveSkill(ctx, name);
    const file = locateScript(skill, script, ctx.services.skills);
    const skillEnv = ctx.services.skillEnv(ctx.agentId, { scope: skill.scope, name: skill.name });
    if (skillEnv.blocked) throw new Error(skillEnv.blocked);
    const command = [commandFor(file), ...args.map(shellQuote)].join(' ');
    const r = await runProcess({
      ...shellInvocation(command, ctx.sandbox),
      cwd: ctx.workspace,
      env: { ...withSkillEnv(scrubbedEnv(ctx.workspace), skillEnv), SKILL_DIR: skill.dir, SKILL_NAME: skill.name },
      timeoutMs: timeout_s * 1000,
      signal: ctx.signal,
      ...(stdin !== undefined ? { stdin } : {}),
    });
    const status = r.aborted ? 'aborted' : r.timedOut ? `timed out after ${timeout_s}s` : `exit code ${r.exitCode}`;
    return `[${status}]\n${r.output}`;
  },
});

export const skillWriteTool = defineTool({
  name: 'skill_write',
  description: [
    'Create or refine a skill — a reusable procedure (SKILL.md instructions + optional scripts/references) that you and threads can activate.',
    "scope 'global' makes it available in every project of the user; 'project' only here.",
    "To install a draft built by a thread (a directory with SKILL.md, scripts/…), pass from_dir; other fields then override the draft's.",
    'For an update, omitted fields keep their current value; always explain the change in change_note.',
    'The description must say what the skill does and when to use it. Instructions are Markdown steps; reference scripts by relative path (scripts/x.py) and run them with skill_run.',
  ].join(' '),
  input: z.object({
    name: SkillName,
    scope: SkillScope.default('project'),
    description: z.string().min(1).max(1024).optional(),
    instructions: z.string().min(1).optional(),
    files: z.array(z.object({ path: z.string().min(1), content: z.string() })).default([]),
    remove_files: z.array(z.string()).default([]),
    from_dir: z.string().optional(),
    change_note: z.string().min(1),
  }),
  async execute(i, ctx) {
    const fromDir = i.from_dir ? ctx.services.skillDraftDir(ctx.projectId, isAbsolute(i.from_dir) ? i.from_dir : join(ctx.workspace, i.from_dir)) : undefined;
    const r = ctx.services.saveSkill(
      {
        scope: i.scope,
        name: i.name,
        ...(i.scope === 'project' ? { projectId: ctx.projectId } : {}),
        ...(i.description ? { description: i.description } : {}),
        ...(i.instructions ? { instructions: i.instructions } : {}),
        files: i.files,
        removeFiles: i.remove_files,
        ...(fromDir ? { fromDir } : {}),
      },
      { origin: `agent:${ctx.agentId}`, changeNote: i.change_note, projectId: ctx.projectId, agentId: ctx.agentId },
    );
    const skill = ctx.services.skills.get(i.scope, i.name, i.scope === 'project' ? ctx.projectId : undefined)!;
    return `${r.created ? 'Created' : 'Updated'} ${i.scope} skill "${i.name}" v${r.version} at ${r.dir}. Files: ${skill.files.map((f) => f.path).join(', ')}`;
  },
});

export const skillDeleteTool = defineTool({
  name: 'skill_delete',
  description: 'Delete a skill (its last version stays in history and the user can restore it). Needs approval.',
  input: z.object({ name: SkillName, scope: SkillScope }),
  gate: { subject: () => ({}), unmatched: 'ask' },
  async execute({ name, scope }, ctx) {
    ctx.services.deleteSkill(scope, name, scope === 'project' ? ctx.projectId : undefined, {
      origin: `agent:${ctx.agentId}`,
      projectId: ctx.projectId,
      agentId: ctx.agentId,
    });
    return `Deleted ${scope} skill "${name}".`;
  },
});

/** Available to every agent. */
export const skillUseTools = [skillListTool, skillReadTool, skillActivateTool, skillRunTool];
/** Desk only: authoring, so installed skills stay under Desk's supervision. */
export const skillAuthoringTools = [skillWriteTool, skillDeleteTool];
