// @vitest-environment jsdom
import { useState } from 'react';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { START_ID, type AutomationDoc } from '@desk/ui-core';
import { digestDef } from '@desk/ui-core/testing';
import { SettingsInspector } from './SettingsInspector';
import { StartInspector } from './StartInspector';

afterEach(cleanup);

const initial = (): AutomationDoc => ({ def: digestDef(), layout: { [START_ID]: { x: 0, y: 0 } } });

function Start(p: { onDoc(d: AutomationDoc): void; next?: Record<string, string[]> }) {
  const [d, setD] = useState(initial());
  return (
    <StartInspector
      doc={d}
      errors={['Schedule 1: cron: fires every minute']}
      nextTimes={p.next ?? {}}
      onChange={(n) => {
        setD(n);
        p.onDoc(n);
      }}
    />
  );
}

describe('StartInspector', () => {
  it('edits a schedule through its preset, time, day and timezone, and previews the next times', () => {
    const onDoc = vi.fn();
    render(<Start onDoc={onDoc} next={{ '0': ['2026-10-05T06:00:00.000Z', '2026-10-12T06:00:00.000Z', '2026-10-19T06:00:00.000Z', '2026-10-26T06:00:00.000Z'] }} />);
    expect(screen.getByRole('alert').textContent).toContain('fires every minute');
    const s1 = screen.getByRole('group', { name: 'Schedule 1' });
    expect((within(s1).getByLabelText('Repeats') as HTMLSelectElement).value).toBe('weekly');
    expect((within(s1).getByLabelText('On') as HTMLSelectElement).value).toBe('1');
    expect(within(s1).getByText(/^Next: /).textContent!.split(' · ')).toHaveLength(3);
    fireEvent.change(within(s1).getByLabelText('At'), { target: { value: '09:15' } });
    expect(onDoc.mock.lastCall![0].def.triggers[0].cron).toBe('15 9 * * 1');
    fireEvent.change(within(s1).getByLabelText('Repeats'), { target: { value: 'monthly' } });
    fireEvent.change(within(s1).getByLabelText('Day of the month'), { target: { value: '15' } });
    expect(onDoc.mock.lastCall![0].def.triggers[0].cron).toBe('15 9 15 * *');
    fireEvent.change(within(s1).getByLabelText('Repeats'), { target: { value: 'custom' } });
    fireEvent.change(within(s1).getByLabelText('Cron'), { target: { value: '*/30 9-17 * * 1-5' } });
    expect(onDoc.mock.lastCall![0].def.triggers[0].cron).toBe('*/30 9-17 * * 1-5');
    fireEvent.change(within(s1).getByLabelText('Timezone'), { target: { value: 'UTC' } });
    fireEvent.change(within(s1).getByLabelText('If the computer was asleep'), { target: { value: 'skip' } });
    expect(onDoc.mock.lastCall![0].def.triggers[0]).toMatchObject({ timezone: 'UTC', catch_up: 'skip' });
    fireEvent.change(within(s1).getByLabelText('Topic'), { target: { value: 'robots' } });
    expect(onDoc.mock.lastCall![0].def.triggers[0].inputs).toEqual({ topic: 'robots' });
    fireEvent.click(screen.getByRole('button', { name: 'Add schedule' }));
    expect(onDoc.mock.lastCall![0].def.triggers).toHaveLength(2);
    fireEvent.click(within(screen.getByRole('group', { name: 'Schedule 2' })).getByRole('button', { name: 'Remove schedule' }));
    expect(onDoc.mock.lastCall![0].def.triggers).toHaveLength(1);
  });

  it('edits inputs: key renames in templates, type, options, default', () => {
    const onDoc = vi.fn();
    render(<Start onDoc={onDoc} />);
    const i1 = screen.getByRole('group', { name: 'Input 1' });
    const key = within(i1).getByLabelText('Key');
    fireEvent.change(key, { target: { value: 'subject' } });
    fireEvent.blur(key);
    expect(onDoc.mock.lastCall![0].def.steps[0].args).toEqual(['{{inputs.subject}}']);
    fireEvent.change(within(i1).getByLabelText('Type'), { target: { value: 'choice' } });
    fireEvent.change(within(i1).getByLabelText('Options 1'), { target: { value: 'robots' } });
    fireEvent.change(within(i1).getByLabelText('Default'), { target: { value: 'robots' } });
    expect(onDoc.mock.lastCall![0].def.inputs[0]).toMatchObject({ key: 'subject', type: 'choice', options: ['robots'], default: 'robots' });
    fireEvent.click(screen.getByRole('button', { name: 'Add input' }));
    expect(onDoc.mock.lastCall![0].def.inputs.map((i: { key: string }) => i.key)).toEqual(['subject', 'input']);
  });
});

describe('SettingsInspector', () => {
  it('edits the title, after-run, result step and limits, and lists problems and warnings', () => {
    const onDoc = vi.fn();
    function S() {
      const [d, setD] = useState(initial());
      return (
        <SettingsInspector
          doc={d}
          issues={['Add at least one step']}
          warnings={[{ path: 'steps[2]', message: 'Publish? has no edges' }]}
          onChange={(n) => {
            setD(n);
            onDoc(n);
          }}
        />
      );
    }
    render(<S />);
    expect(screen.getByRole('alert').textContent).toContain('Add at least one step');
    expect(screen.getByText('Publish? has no edges')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Digest' } });
    fireEvent.change(screen.getByLabelText('After each run'), { target: { value: 'desk_review' } });
    fireEvent.change(screen.getByLabelText('Its result, when another automation runs it'), { target: { value: 'sum' } });
    fireEvent.change(screen.getByLabelText('Agents at once'), { target: { value: '3' } });
    expect(onDoc.mock.lastCall![0].def).toMatchObject({ title: 'Digest', after_run: 'desk_review', output_step: 'sum', limits: { max_parallel_agents: 3 } });
    fireEvent.change(screen.getByLabelText('Its result, when another automation runs it'), { target: { value: '' } });
    expect('output_step' in onDoc.mock.lastCall![0].def).toBe(false);
  });
});
