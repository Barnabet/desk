import {
  AutomationExport,
  AutomationName,
  Grant,
  type AutomationDefinition,
  type AutomationLayout,
  type AutomationValidateResponse,
  type SaveVia,
} from '@desk/protocol';
import { z } from 'zod';
import { ConflictError, NotFoundError, ValidationError } from '../errors';
import type { EventStore } from '../events/store';
import { newId } from '../ids';
import { getProject } from '../state/queries';
import type { Tool } from '../tools/types';
import { proposedGrants } from './grants';
import { findAutomation, getAutomation, getVersion, listAutomations, type AutomationRow, type AutomationVersionRow } from './queries';
import { checkSchedule, nextTimes } from './schedule';
import { validateDefinition, type ValidateContext } from './validate';
import { versionInfos } from './views';

export type AutomationsHost = {
  store: EventStore;
  validateContext(projectId: string, name: string): ValidateContext;
  now(): Date;
  tools(name: string): Tool | undefined;
  /** Called before an automation is deleted (the engine cancels its runs). */
  beforeDelete?(automationId: string): Promise<void>;
};

type SaveMeta = { origin: string; changeNote?: string; via: SaveVia };

/** The agent id in an `agent:<id>` origin, for the event's agent_id. */
const agentOf = (origin: string): string | null => (origin.startsWith('agent:') ? origin.slice('agent:'.length) : null);

/** Definitions and the user's switches (spec §2, §5): every change is an event; runs live in the engine. */
export class Automations {
  constructor(private readonly host: AutomationsHost) {}

  private get db() {
    return this.host.store.db;
  }

  /** An automation that exists and is not deleted. */
  require(automationId: string): AutomationRow {
    const a = getAutomation(this.db, automationId);
    if (!a || a.deleted_at) throw new NotFoundError(`Unknown automation: ${automationId}`);
    return a;
  }

  requireByName(projectId: string, name: string): AutomationRow {
    const a = findAutomation(this.db, projectId, name);
    if (!a) throw new NotFoundError(`No automation named "${name}" in this project`);
    return a;
  }

  /** The project's automations (not deleted), by name. */
  list(projectId: string): AutomationRow[] {
    return listAutomations(this.db, projectId);
  }

  private requireOpen(projectId: string): void {
    const p = getProject(this.db, projectId);
    if (!p) throw new NotFoundError(`Unknown project: ${projectId}`);
    if (p.archived_at) throw new ConflictError(`Project ${projectId} is archived`);
  }

  /** Validates or throws ValidationError with `details: {errors, warnings}`. */
  private checked(projectId: string, name: string, raw: unknown): { definition: AutomationDefinition; warnings: AutomationValidateResponse['warnings'] } {
    const r = validateDefinition(raw, this.host.validateContext(projectId, name));
    if (!r.definition || r.errors.length) {
      const first = r.errors[0]!;
      const more = r.errors.length > 1 ? ` (and ${r.errors.length - 1} more)` : '';
      throw new ValidationError(`The automation is not valid: ${first.path ? `${first.path}: ` : ''}${first.message}${more}`, { errors: r.errors, warnings: r.warnings });
    }
    return { definition: r.definition, warnings: r.warnings };
  }

  create(projectId: string, name: string, raw: unknown, meta: SaveMeta): { automation: AutomationRow; warnings: AutomationValidateResponse['warnings'] } {
    this.requireOpen(projectId);
    const parsed = AutomationName.safeParse(name);
    if (!parsed.success) throw new ValidationError(`Invalid automation name "${name}": ${parsed.error.issues[0]?.message ?? 'invalid'}`);
    if (findAutomation(this.db, projectId, name)) throw new ConflictError(`An automation named "${name}" already exists in this project`);
    const { definition, warnings } = this.checked(projectId, name, raw);
    const id = newId();
    this.host.store.append({
      project_id: projectId,
      agent_id: agentOf(meta.origin),
      type: 'automation.saved',
      payload: { automation_id: id, name, version: 1, definition, origin: meta.origin, change_note: meta.changeNote ?? 'Created', via: meta.via },
    });
    return { automation: getAutomation(this.db, id)!, warnings };
  }

  save(automationId: string, raw: unknown, meta: SaveMeta & { baseVersion?: number }): { automation: AutomationRow; warnings: AutomationValidateResponse['warnings'] } {
    const a = this.require(automationId);
    this.requireOpen(a.project_id);
    if (meta.baseVersion !== undefined && meta.baseVersion !== a.version) {
      throw new ConflictError(`"${a.name}" is at version ${a.version}; your changes were made on version ${meta.baseVersion}`, { current_version: a.version });
    }
    const { definition, warnings } = this.checked(a.project_id, a.name, raw);
    this.host.store.append({
      project_id: a.project_id,
      agent_id: agentOf(meta.origin),
      type: 'automation.saved',
      payload: { automation_id: a.id, name: a.name, version: a.version + 1, definition, origin: meta.origin, change_note: meta.changeNote ?? 'Updated', via: meta.via },
    });
    return { automation: getAutomation(this.db, a.id)!, warnings };
  }

  /** Validation without saving, plus the next three times of each valid schedule (the editor calls this live). */
  validate(projectId: string, raw: unknown, name = ''): AutomationValidateResponse {
    const r = validateDefinition(raw, this.host.validateContext(projectId, name));
    const next_times: Record<string, string[]> = {};
    r.definition?.triggers.forEach((t, i) => {
      if (!checkSchedule(t.cron, t.timezone, this.host.now())) next_times[String(i)] = nextTimes(t.cron, t.timezone, 3, this.host.now()).map((d) => d.toISOString());
    });
    return { errors: r.errors, warnings: r.warnings, next_times };
  }

  async delete(automationId: string, origin: string): Promise<void> {
    const a = this.require(automationId);
    await this.host.beforeDelete?.(a.id);
    this.host.store.append({ project_id: a.project_id, agent_id: agentOf(origin), type: 'automation.deleted', payload: { automation_id: a.id, origin } });
  }

  setLayout(automationId: string, layout: AutomationLayout): void {
    const a = this.require(automationId);
    this.host.store.append({ project_id: a.project_id, agent_id: null, type: 'automation.layout_saved', payload: { automation_id: a.id, layout } });
  }

  /** The user's switch (or the system's, when the project is archived). A no-op when already in that state. */
  setEnabled(automationId: string, enabled: boolean, by: 'user' | 'system'): AutomationRow {
    const a = this.require(automationId);
    if (a.enabled === enabled) return a;
    if (enabled) this.requireOpen(a.project_id);
    this.host.store.append({ project_id: a.project_id, agent_id: null, type: 'automation.switched', payload: { automation_id: a.id, enabled, by } });
    return getAutomation(this.db, a.id)!;
  }

  setGrants(automationId: string, grants: Grant[], reason: 'edited' | 'remembered' | 'kept' | 'enabled'): AutomationRow {
    const a = this.require(automationId);
    const list = z.array(Grant).max(100).parse(grants);
    this.host.store.append({ project_id: a.project_id, agent_id: null, type: 'automation.grants_set', payload: { automation_id: a.id, grants: list, reason } });
    return getAutomation(this.db, a.id)!;
  }

  /** Ends a suspension with the same grants. */
  keepGrants(automationId: string): AutomationRow {
    const a = this.require(automationId);
    if (!a.grants_suspended) return a;
    return this.setGrants(a.id, a.grants, 'kept');
  }

  requestEnable(automationId: string, deskAgentId: string, note: string): void {
    const a = this.require(automationId);
    this.host.store.append({
      project_id: a.project_id,
      agent_id: deskAgentId,
      type: 'automation.enable_requested',
      payload: { automation_id: a.id, note, proposed_grants: proposedGrants(this.db, a.id, this.host.tools) },
    });
  }

  versions(automationId: string) {
    return versionInfos(this.db, this.require(automationId).id);
  }

  version(automationId: string, version: number): AutomationVersionRow {
    const a = this.require(automationId);
    const v = getVersion(this.db, a.id, version);
    if (!v) throw new NotFoundError(`"${a.name}" has no version ${version}`);
    return v;
  }

  restore(automationId: string, version: number, origin: string): { automation: AutomationRow; warnings: AutomationValidateResponse['warnings'] } {
    const v = this.version(automationId, version);
    return this.save(automationId, v.definition, { origin, via: 'restore', changeNote: `Restored version ${version}` });
  }

  exportOf(automationId: string): AutomationExport {
    const a = this.require(automationId);
    return { format: 'desk-automation/1', name: a.name, definition: a.definition };
  }

  importInto(projectId: string, raw: unknown, origin: string): { automation: AutomationRow; warnings: AutomationValidateResponse['warnings'] } {
    const exp = AutomationExport.parse(raw);
    return this.create(projectId, exp.name, exp.definition, { origin, via: 'import', changeNote: 'Imported' });
  }
}
