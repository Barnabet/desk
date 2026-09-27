import type { AfterRun, InputSpec, InputType, InputValue, ScheduleTrigger } from '@desk/protocol';
import { setInputs, type AutomationDoc } from './automation-draft';
import { cronOf, systemTimezone, type SchedulePreset } from './automation-schedules';

export const INPUT_TYPE_LABEL: Record<InputType, string> = { text: 'Text', long_text: 'Long text', url: 'URL', file: 'File', folder: 'Folder', number: 'Number', boolean: 'Yes / no', choice: 'Choice' };

export const AFTER_RUN_LABEL: Record<AfterRun, string> = { silent: 'Nothing (failures still notify)', notify: 'Notify me with its summary', desk_review: 'Send Desk the run report' };

/** A new schedule: weekdays at 08:00 in this computer's timezone, catching up once. */
export const newSchedule = (timezone = systemTimezone()): ScheduleTrigger => ({ kind: 'schedule', cron: cronOf({ kind: 'weekdays', time: '08:00' }), timezone, catch_up: 'once' });

/** A new text input with a key no other input has: `input`, `input_2`… */
export function newInput(inputs: InputSpec[]): InputSpec {
  const keys = new Set(inputs.map((i) => i.key));
  let key = 'input';
  for (let n = 2; keys.has(key); n++) key = `input_${n}`;
  return { key, label: 'New input', type: 'text', required: false };
}

/** The preset a schedule switches to: it keeps its time, and its weekday or day of month where one applies. */
export function switchPreset(current: SchedulePreset, kind: SchedulePreset['kind']): SchedulePreset {
  const time = current.kind === 'custom' ? '08:00' : current.time;
  switch (kind) {
    case 'daily':
    case 'weekdays':
      return { kind, time };
    case 'weekly':
      return { kind, day: current.kind === 'weekly' ? current.day : 1, time };
    case 'monthly':
      return { kind, dom: current.kind === 'monthly' ? current.dom : 1, time };
    case 'custom':
      return { kind, cron: cronOf(current) };
  }
}

/** IANA timezones this runtime knows, for the timezone field's suggestions (empty where Intl cannot list them). */
export function timezones(): string[] {
  const intl = Intl as { supportedValuesOf?(key: string): string[] };
  return intl.supportedValuesOf ? intl.supportedValuesOf('timeZone') : [];
}

const dropUndefined = <T extends object>(o: T): T => Object.fromEntries(Object.entries(o).filter(([, v]) => v !== undefined)) as T;

/** Merges fields into one input (not its key: `renameInput` changes that everywhere). A field set to undefined is removed. */
export function patchInput(doc: AutomationDoc, index: number, patch: Partial<Omit<InputSpec, 'key'>>): AutomationDoc {
  return setInputs(
    doc,
    doc.def.inputs.map((x, i) => (i === index ? dropUndefined({ ...x, ...patch, key: x.key }) : x)),
  );
}

/** Changes an input's type: its default goes (it may not fit), and a choice gets a first option. */
export function retypeInput(doc: AutomationDoc, index: number, type: InputType): AutomationDoc {
  const x = doc.def.inputs[index];
  if (!x) return doc;
  return patchInput(doc, index, { type, default: undefined, options: type === 'choice' ? (x.options ?? ['First option']) : undefined });
}

/** The value a field holds, typed: a number parsed, a boolean as it is, empty as undefined. */
export function inputValueOf(type: InputType, raw: string | boolean): InputValue | undefined {
  if (typeof raw === 'boolean') return raw;
  if (!raw.trim()) return undefined;
  if (type === 'number') {
    const n = Number(raw);
    return Number.isFinite(n) ? n : undefined;
  }
  return raw;
}
