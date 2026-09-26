import type { AgentRow } from '../state/queries';
import { bashReadonlyTool, bashTool } from '../tools/bash';
import { deskCoordinationTools } from '../tools/desk';
import { editFileTool, fileTools, globTool, grepTool, listDirTool, readFileTool, writeFileTool } from '../tools/fs';
import { gitTools } from '../tools/git';
import { jobTools } from '../tools/jobs';
import { libraryListTool, libraryReadTool, libraryTools } from '../tools/library';
import { memorySearchTool, memoryTools } from '../tools/memory';
import { stepTools } from '../tools/step';
import { serviceTools } from '../tools/services';
import { skillAuthoringTools, skillUseTools } from '../tools/skills';
import { threadCoordinationTools } from '../tools/thread';
import { viewImageTool } from '../tools/vision';
import type { Tool } from '../tools/types';
import { webTools } from '../tools/web';

/** Desk: read (and look at) anything in the project, draft in its own scratch dir, read-only shell, skills (use + authoring), coordination. */
export function deskToolsFor(_agent: AgentRow): Tool[] {
  return [
    readFileTool,
    listDirTool,
    globTool,
    grepTool,
    viewImageTool,
    writeFileTool,
    editFileTool,
    bashReadonlyTool,
    ...webTools,
    ...memoryTools,
    ...libraryTools,
    ...skillUseTools,
    ...skillAuthoringTools,
    ...serviceTools,
    ...deskCoordinationTools,
  ];
}

/** Threads: full workspace tools, skills (use only — Desk installs drafts); git tools only in worktrees. */
export function threadToolsFor(agent: AgentRow): Tool[] {
  return [
    ...fileTools,
    viewImageTool,
    bashTool,
    ...jobTools,
    ...serviceTools,
    ...webTools,
    ...memoryTools,
    ...libraryTools,
    ...skillUseTools,
    ...(agent.git_branch ? gitTools : []),
    ...threadCoordinationTools,
  ];
}

/**
 * Automation step agents: workspace tools, the web, skills (use only), reading memory and the library, and their own
 * complete / fail_step. No messaging, services, memory writes or publishing: nobody is watching and outputs go
 * through the step's `publish` (spec §4.2).
 */
export function stepToolsFor(agent: AgentRow): Tool[] {
  return [
    ...fileTools,
    viewImageTool,
    bashTool,
    ...jobTools,
    ...webTools,
    memorySearchTool,
    libraryListTool,
    libraryReadTool,
    ...skillUseTools,
    ...(agent.git_branch ? gitTools : []),
    ...stepTools,
  ];
}

export function toolsForRole(agent: AgentRow): Tool[] {
  return agent.role === 'desk' ? deskToolsFor(agent) : agent.role === 'step' ? stepToolsFor(agent) : threadToolsFor(agent);
}

let byName: Map<string, Tool> | undefined;

/** Any tool of any role by name (grants derive their subject from an approval's call). */
export function toolByName(name: string): Tool | undefined {
  if (!byName) {
    const row = { git_branch: 'desk/x' } as AgentRow;
    byName = new Map([...deskToolsFor(row), ...threadToolsFor(row)].map((t) => [t.name, t]));
  }
  return byName.get(name);
}
