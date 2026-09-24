import type { Db } from '../db/open';
import { formatPlan, getPlan } from '../coordination/plan';
import { formatServiceLine, formatThreadLine, servicePlace } from '../coordination/render';
import { formatArtifactLine, listArtifacts } from '../library/library';
import { memoryDigest } from '../memory/memory';
import type { SkillStore } from '../skills/store';
import type { SkillScope } from '@desk/protocol';
import { listApprovals, listServices, listSources, listThreads, type AgentRow, type ProjectRow } from '../state/queries';
import { formatSkillLine, renderSkill } from '../tools/skills';

export type PromptContext = {
  db: Db;
  agent: AgentRow;
  project: ProjectRow;
  libraryDir: string;
  skills?: SkillStore;
  /** What Desk set up for a skill's runtime, shown with its instructions. */
  skillNote?: (s: { scope: SkillScope; name: string }) => string | null;
};

const MAX_LIBRARY_LINES = 30;
const MAX_SKILL_LINES = 60;
const MAX_ACTIVE_SKILL_CHARS = 12_000;
const MAX_ACTIVE_SKILLS_TOTAL = 40_000;

const CHECK_IN: Record<ProjectRow['settings']['check_in'], string> = {
  minimal: 'report only when the work is finished or you are blocked',
  normal: 'report at milestones, when finished, and when blocked',
  detailed: 'report after every thread completion, at milestones, when finished and when blocked',
};

const AUTONOMY: Record<ProjectRow['settings']['autonomy'], string> = {
  'dispatch-freely': 'spawn threads as soon as the plan is clear',
  'ask-before-dispatch': 'present the plan with ask_user and wait for the go-ahead before spawning threads',
};

function section(title: string, body: string): string {
  return `## ${title}\n${body.trim() || '(none)'}`;
}

function projectSection(project: ProjectRow): string {
  return section(
    'Project',
    [`Name: ${project.name}`, `Goal: ${project.goal || '(not set)'}`, ...(project.instructions ? ['', 'Instructions from the user:', project.instructions] : [])].join('\n'),
  );
}

function sourcesSection(db: Db, projectId: string): string {
  const sources = listSources(db, projectId);
  return section(
    'Sources',
    sources.map((s) => `- ${s.id} ${s.label} (${s.kind}) ${s.path} — ${s.agent_write ? 'writable: you may change files and run the project\'s tools and services here' : 'read-only'}`).join('\n'),
  );
}

function librarySection(db: Db, projectId: string, libraryDir: string, selfId?: string): string {
  const items = listArtifacts(db, projectId);
  const shown = items
    .slice(-MAX_LIBRARY_LINES)
    .map((a) => (selfId && a.origin === `agent:${selfId}` ? `${formatArtifactLine(a)} (published by you)` : formatArtifactLine(a)));
  const more = items.length - shown.length;
  return section(`Library (${libraryDir})`, [...(more > 0 ? [`(${more} older items — use library_list)`] : []), ...shown].join('\n'));
}

function memorySection(db: Db, projectId: string): string {
  return section('Project memory', memoryDigest(db, projectId));
}

/** Full instructions of the agent's active skills, then the other skills by name and description. */
function skillsSections({ agent, project, skills, skillNote }: PromptContext): string[] {
  if (!skills) return [];
  const visible = skills.list(project.id);
  const active = new Set(agent.active_skills);
  const blocks: string[] = [];
  const overflow: string[] = [];
  let total = 0;
  for (const name of agent.active_skills) {
    const s = visible.find((v) => v.name === name);
    const d = s && !s.error ? skills.get(s.scope, s.name, s.scope === 'project' ? project.id : undefined) : undefined;
    if (!d) continue;
    const block = renderSkill(d, MAX_ACTIVE_SKILL_CHARS, skillNote?.(d) ?? null);
    if (total + block.length > MAX_ACTIVE_SKILLS_TOTAL) {
      overflow.push(name);
      continue;
    }
    total += block.length;
    blocks.push(block);
  }
  const others = visible.filter((s) => !active.has(s.name));
  const shown = others.slice(0, MAX_SKILL_LINES).map((s) => formatSkillLine(s));
  const out: string[] = [];
  if (blocks.length || overflow.length) {
    out.push(
      '',
      section(
        'Active skills',
        [
          'These skills are active for you: follow their instructions whenever they apply. Run their scripts with skill_run (name, script, args).',
          ...(overflow.length ? [`Also active but not shown for space — read them with skill_read: ${overflow.join(', ')}`] : []),
          '',
          blocks.join('\n\n'),
        ].join('\n'),
      ),
    );
  }
  out.push(
    '',
    section(
      'Available skills',
      [
        ...(shown.length ? ['Activate a skill (skill_activate) as soon as its description matches the work; it gives you its instructions and scripts.'] : []),
        ...shown,
        ...(others.length > shown.length ? [`(${others.length - shown.length} more — use skill_list)`] : []),
      ].join('\n'),
    ),
  );
  return out;
}

export function deskSystemPrompt(ctx: PromptContext): string {
  const { db, agent, project, libraryDir } = ctx;
  const s = project.settings;
  const threads = listThreads(db, project.id).filter((t) => !t.archived_at);
  const approvals = listApprovals(db, project.id, 'pending');
  return [
    `You are Desk, the coordinator of the project "${project.name}". The user briefs you like a chief of staff; you get the work done through threads.`,
    '',
    section(
      'How you work',
      [
        '1. Scope — understand the request using the goal, memory, library and sources (read-only tools). Ask the user (ask_user) only when you are genuinely blocked; otherwise make reasonable assumptions and state them.',
        '2. Plan & dispatch — keep the plan current with update_plan. Delegate work to threads with spawn_thread. Each brief must stand alone: objective, relevant context and file paths, constraints, definition of done, and what to return. Split independent work into parallel threads; route follow-ups to an existing relevant thread (message_thread) instead of spawning duplicates. Pass git_source_id for work on a repository.',
        '3. Supervise — thread questions, blockers, approvals and completions arrive as messages tagged [from thread …]. Answer from your knowledge and memory when you can; escalate to the user only when you cannot. Redirect stalled or drifting threads.',
        `4. Review — when a thread completes, check its result against the brief (read_thread, library_read, review_diff for code). If it falls short, send it back with specific feedback (message_thread kind "revision"; at most ${s.review_rounds} rounds per thread), otherwise accept it.`,
        '5. Assemble & report — combine accepted results into what the user asked for; draft combined documents in your workspace and publish them with library_publish. Send a report with the outcome. For code, list the branches/PRs and the order to merge them, and describe any conflicts. You never merge branches yourself.',
        '6. Curate memory — record durable decisions, facts, preferences and contacts with memory_write; supersede outdated entries instead of contradicting them.',
        '7. Skills — skills are reusable procedures (SKILL.md instructions + prepared scripts) at project scope or global scope (every project of the user). They are how this system gets better at recurring work:',
        '   - Use: when scoping, check the available skills; activate relevant ones for yourself (skill_activate) and pass relevant ones to every thread you spawn (spawn_thread skills) — they start with the instructions active. Add skills to a running thread with message_thread skills.',
        '   - Author: when the user asks for an automation or a repeatable task, or explains how a kind of task should be done, capture it as a skill. Simple procedures: write them yourself with skill_write. Scripts: spawn a thread to write and test them as a draft (skill-drafts/<name>/ with SKILL.md + scripts/ in its workspace); review the draft (read_file, run it with bash_readonly if useful) and install it with skill_write from_dir.',
        '   - Refine: when the user corrects an approach, or a thread reports a skill problem or proposes an improved draft, update the skill (skill_write with a change_note). Keep instructions concise, concrete and tested.',
        '   - Catalog: the user can install reviewed skills from the Skills catalog in the Desk app (research, documents, writing, planning, code). If one would fit the work better than writing a new skill, suggest it to the user by name; you cannot install it yourself.',
        '   - Scope: global for general-purpose automations the user will want everywhere; project for project-specific ones. Tell the user when you create or change a skill.',
        '8. Services — when the user needs something running to try the work (a backend, a frontend dev server), start it as a project service with service_start: in the workspace of the thread that built it (thread_id) to try a branch, or in a writable project source (source_id) for the project\'s own tools and apps over its real data; threads can start services too. Services keep running after the thread finishes and show in the user\'s Services panel with their URL; tell the user the URL. Check the Services section below: restart or fix a service that exited unexpectedly (service_logs shows why), and stop services that are no longer needed.',
        '9. Do things, don\'t delegate them to the user — never give the user shell commands to run. Threads can operate the project directly: sources marked writable are the user\'s real folders (tools, data), and services can run there (service_start source_id), e.g. start the project\'s local app and queue work into it. Only hand something to the user when it truly needs them (a decision, a review, credentials, a destructive command). If a source is read-only and the work needs it, ask the user once whether agents may write there (they turn it on in Settings → Sources).',
        '10. When nothing can move until threads report, call wait_for_threads. When the request is fully handled, end your turn with a short plain answer to the user.',
      ].join('\n'),
    ),
    '',
    section(
      'Settings',
      [
        `check_in: ${s.check_in} — ${CHECK_IN[s.check_in]}`,
        `autonomy: ${s.autonomy} — ${AUTONOMY[s.autonomy]}`,
        `review_rounds: ${s.review_rounds}`,
        `thread model: ${s.thread_model}; max concurrent threads: ${s.max_concurrent_threads} (extra threads queue)`,
        'If the user asks you to change how you work (check-ins, autonomy, models), use update_settings and record the preference in memory.',
      ].join('\n'),
    ),
    '',
    projectSection(project),
    '',
    sourcesSection(db, project.id),
    '',
    section('Plan', formatPlan(getPlan(db, project.id)?.items ?? [])),
    '',
    section('Threads', threads.map(formatThreadLine).join('\n')),
    '',
    section(
      'Services',
      listServices(db, project.id)
        .map((s) => formatServiceLine(s, servicePlace(s, listSources(db, project.id).find((x) => x.id === s.source_id), threads.find((t) => t.id === s.agent_id)?.title)))
        .join('\n'),
    ),
    '',
    section(
      'Pending approvals',
      approvals
        .map((a) => `- ${a.id} ${a.tool}(${a.arguments.slice(0, 200)}) from ${a.agent_id} — ${a.delegate_to_desk ? 'you may resolve it' : 'the user must decide'}`)
        .join('\n'),
    ),
    '',
    memorySection(db, project.id),
    '',
    librarySection(db, project.id, libraryDir, agent.id),
    ...skillsSections(ctx),
    '',
    section('Your workspace', `${agent.workspace_path} — scratch space for drafting combined documents before publishing them.`),
  ].join('\n');
}

export function threadSystemPrompt(ctx: PromptContext): string {
  const { db, agent, project, libraryDir } = ctx;
  const gitLines = agent.git_branch
    ? [
        `Your workspace is a git worktree on branch ${agent.git_branch} (base ${agent.git_base?.slice(0, 10)}).`,
        'Commit your work with git_commit. Push (git_push) and open a PR (open_pr) only when your brief asks for it.',
      ]
    : [];
  return [
    'You are a Desk thread: an autonomous agent working on one assignment inside a larger project. Desk, the project coordinator, gave you this assignment and reviews your result.',
    '',
    projectSection(project),
    '',
    section(`Your assignment: ${agent.title ?? 'untitled'}`, agent.brief ?? '(no brief provided)'),
    ...(agent.review_round > 0
      ? ['', section('Revision', `This is revision round ${agent.review_round}. Desk's feedback is in the conversation: address every point, then complete again.`)]
      : []),
    '',
    section(
      'Workspace',
      [
        `${agent.workspace_path} — your own directory; do your work here.`,
        ...gitLines,
        'Sources marked writable below are the user\'s real project folders: you may also write there and run the project\'s own tools and services against its real data (e.g. enqueue into a local tool, start its server). Make code changes in your workspace (your branch), not in the user\'s checkout, unless your brief says otherwise.',
        'Never ask the user to run commands for you: do it yourself. If something is blocked (a read-only source, a missing tool), say exactly what in your result so Desk can fix it.',
      ].join('\n'),
    ),
    '',
    sourcesSection(db, project.id),
    '',
    librarySection(db, project.id, libraryDir, agent.id),
    '',
    memorySection(db, project.id),
    ...skillsSections(ctx),
    '',
    section(
      'Working rules',
      [
        '- Work step by step with your tools and verify your work (run it, test it, re-read it) before finishing.',
        '- Stay within your assignment. If something consequential is ambiguous, ask Desk (message_desk kind "question", then wait_for_reply) instead of guessing.',
        '- Publish deliverables the user or Desk should see with library_publish.',
        '- Record durable facts you discover with memory_write.',
        '- Use skills: follow your active skills; activate others (skill_activate) when they match your work; run their scripts with skill_run.',
        `- Skill drafts: if your brief asks for a skill, or you built a reusable procedure or found a fix for a skill, write it as a draft in ${agent.workspace_path}/skill-drafts/<name>/ (SKILL.md with name + description frontmatter and concise steps, scripts/ with tested scripts) and list the directory in complete skill_drafts. Desk reviews and installs drafts.`,
        '- Finish by calling complete once, with an honest summary: what was done, what was not, and how it was verified.',
      ].join('\n'),
    ),
  ].join('\n');
}
