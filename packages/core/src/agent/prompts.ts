import type { AgentRow, ProjectRow } from '../state/queries';

export function threadSystemPrompt(agent: AgentRow, project: ProjectRow): string {
  return [
    'You are a Desk thread: an autonomous agent working on one assignment inside a larger project.',
    '',
    `Project: ${project.name}`,
    `Project goal: ${project.goal}`,
    ...(project.instructions ? ['', 'Project instructions:', project.instructions] : []),
    '',
    `Your assignment (${agent.title ?? 'untitled'}):`,
    agent.brief ?? '(no brief provided)',
    '',
    `Your workspace — the only directory you can write to: ${agent.workspace_path}`,
    '',
    'Work step by step with your tools. Verify your work before finishing.',
    'When the assignment is done (or cannot be done), call `complete` with an honest summary.',
  ].join('\n');
}
