import { describe, expect, it } from 'vitest';
import { digestDef } from './testing/automations';
import { START_ID } from './automation-graph';
import { inputValueOf, newInput, newSchedule, patchInput, retypeInput, switchPreset } from './automation-start';

const doc = () => ({ def: digestDef(), layout: { [START_ID]: { x: 0, y: 0 } } });

describe('start helpers', () => {
  it('makes a new schedule and a new input with a free key', () => {
    expect(newSchedule('Europe/Paris')).toEqual({ kind: 'schedule', cron: '0 8 * * 1-5', timezone: 'Europe/Paris', catch_up: 'once' });
    expect(newInput(digestDef().inputs)).toEqual({ key: 'input', label: 'New input', type: 'text', required: false });
    expect(newInput([...digestDef().inputs, { key: 'input', label: 'x', type: 'text', required: false }]).key).toBe('input_2');
  });

  it('switches presets, keeping the time and the day where it can', () => {
    expect(switchPreset({ kind: 'daily', time: '07:30' }, 'weekly')).toEqual({ kind: 'weekly', day: 1, time: '07:30' });
    expect(switchPreset({ kind: 'weekly', day: 5, time: '07:30' }, 'monthly')).toEqual({ kind: 'monthly', dom: 1, time: '07:30' });
    expect(switchPreset({ kind: 'weekly', day: 5, time: '07:30' }, 'custom')).toEqual({ kind: 'custom', cron: '30 7 * * 5' });
    expect(switchPreset({ kind: 'custom', cron: '*/5 * * * *' }, 'daily')).toEqual({ kind: 'daily', time: '08:00' });
  });

  it('patches and retypes inputs, and reads typed values', () => {
    const d = patchInput(doc(), 0, { label: 'Subject', description: undefined });
    expect(d.def.inputs[0]).toEqual({ key: 'topic', label: 'Subject', type: 'text', required: true });
    const c = retypeInput(patchInput(doc(), 0, { default: 'robots' }), 0, 'choice');
    expect(c.def.inputs[0]).toEqual({ key: 'topic', label: 'Topic', type: 'choice', required: true, options: ['First option'] });
    expect(retypeInput(c, 0, 'text').def.inputs[0]).toEqual({ key: 'topic', label: 'Topic', type: 'text', required: true });
    expect(inputValueOf('number', '3')).toBe(3);
    expect(inputValueOf('number', 'x')).toBeUndefined();
    expect(inputValueOf('text', '  ')).toBeUndefined();
    expect(inputValueOf('boolean', true)).toBe(true);
  });
});
