import type { AutomationDefinition, ValidationIssue } from '@desk/protocol';
import type { GraphIssues } from './automation-graph';

/** Validation issues by where the editor shows them: on a step, an edge (by index), the Start pill (inputs, schedules), or the automation. */
export type IssueMap = GraphIssues & { general: string[]; count: number };

const withField = (field: string, message: string) => (field && !message.startsWith(field) ? `${field}: ${message}` : message);

export function mapIssues(def: AutomationDefinition, issues: ValidationIssue[]): IssueMap {
  const out: IssueMap = { steps: {}, edges: {}, start: [], general: [], count: issues.length };
  for (const { path, message } of issues) {
    const step = /^steps\[(\d+)\]\.?(.*)$/.exec(path);
    if (step) {
      const id = def.steps[Number(step[1])]?.id;
      if (id) (out.steps[id] ??= []).push(withField(step[2] ?? '', message));
      else out.general.push(message);
      continue;
    }
    const edge = /^edges\[(\d+)\]\.?(.*)$/.exec(path);
    if (edge) {
      (out.edges[Number(edge[1])] ??= []).push(withField(edge[2] ?? '', message));
      continue;
    }
    const start = /^(inputs|triggers)\[(\d+)\]\.?(.*)$/.exec(path);
    if (start) {
      out.start.push(`${start[1] === 'inputs' ? 'Input' : 'Schedule'} ${Number(start[2]) + 1}: ${withField(start[3] ?? '', message)}`);
      continue;
    }
    out.general.push(path && path !== 'steps' ? withField(path, message) : message);
  }
  return out;
}
