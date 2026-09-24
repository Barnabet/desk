import type { AgentRow } from '../state/queries';
import { bashReadonlyTool, bashTool } from '../tools/bash';
import { deskCoordinationTools } from '../tools/desk';
import { editFileTool, fileTools, globTool, grepTool, listDirTool, readFileTool, writeFileTool } from '../tools/fs';
import { gitTools } from '../tools/git';
import { jobTools } from '../tools/jobs';
import { libraryTools } from '../tools/library';
import { memoryTools } from '../tools/memory';
import { serviceTools } from '../tools/services';
import { skillAuthoringTools, skillUseTools } from '../tools/skills';
import { threadCoordinationTools } from '../tools/thread';
import type { Tool } from '../tools/types';
import { webTools } from '../tools/web';

/** Desk: read anything in the project, draft in its own scratch dir, read-only shell, skills (use + authoring), coordination. */
export function deskToolsFor(_agent: AgentRow): Tool[] {
  return [
    readFileTool,
    listDirTool,
    globTool,
    grepTool,
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

export function toolsForRole(agent: AgentRow): Tool[] {
  return agent.role === 'desk' ? deskToolsFor(agent) : threadToolsFor(agent);
}
