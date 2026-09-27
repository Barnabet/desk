import { ChangeDetectionStrategy, Component, input, signal } from '@angular/core';
import { fireEvent, render, screen, within } from '@testing-library/angular';
import { describe, expect, it, vi } from 'vitest';
import { START_ID, type AutomationDoc } from '@desk/ui-core';
import { digestDef } from '@desk/ui-core/testing';
import { SettingsInspector } from './settings-inspector';
import { StartInspector } from './start-inspector';

const initial = (): AutomationDoc => ({ def: digestDef(), layout: { [START_ID]: { x: 0, y: 0 } } });

@Component({
  selector: 'desk-start-harness',
  imports: [StartInspector],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<aside deskStartInspector [doc]="d()" [errors]="errors" [nextTimes]="next()" (docChange)="change($event)"></aside>`,
})
class Start {
  readonly onDoc = input.required<(d: AutomationDoc) => void>();
  readonly next = input<Record<string, string[]>>({});
  protected readonly d = signal(initial());
  protected readonly errors = ['Schedule 1: cron: fires every minute'];
  protected change(n: AutomationDoc): void {
    this.d.set(n);
    this.onDoc()(n);
  }
}

@Component({
  selector: 'desk-settings-harness',
  imports: [SettingsInspector],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<aside deskSettingsInspector [doc]="d()" [issues]="issues" [warnings]="warnings" (docChange)="change($event)"></aside>`,
})
class Settings {
  readonly onDoc = input.required<(d: AutomationDoc) => void>();
  protected readonly d = signal(initial());
  protected readonly issues = ['Add at least one step'];
  protected readonly warnings = [{ path: 'steps[2]', message: 'Publish? has no edges' }];
  protected change(n: AutomationDoc): void {
    this.d.set(n);
    this.onDoc()(n);
  }
}

describe('StartInspector', () => {
  it('edits a schedule through its preset, time, day and timezone, and previews the next times', async () => {
    const onDoc = vi.fn();
    await render(Start, { inputs: { onDoc, next: { '0': ['2026-10-05T06:00:00.000Z', '2026-10-12T06:00:00.000Z', '2026-10-19T06:00:00.000Z', '2026-10-26T06:00:00.000Z'] } } });
    expect(screen.getByRole('alert').textContent).toContain('fires every minute');
    const s1 = screen.getByRole('group', { name: 'Schedule 1' });
    expect((within(s1).getByLabelText('Repeats') as HTMLSelectElement).value).toBe('weekly');
    expect((within(s1).getByLabelText('On') as HTMLSelectElement).value).toBe('1');
    expect(within(s1).getByText(/^Next: /).textContent!.split(' · ')).toHaveLength(3);
    fireEvent.input(within(s1).getByLabelText('At'), { target: { value: '09:15' } });
    expect(onDoc.mock.lastCall![0].def.triggers[0].cron).toBe('15 9 * * 1');
    fireEvent.change(within(s1).getByLabelText('Repeats'), { target: { value: 'monthly' } });
    fireEvent.input(within(s1).getByLabelText('Day of the month'), { target: { value: '15' } });
    expect(onDoc.mock.lastCall![0].def.triggers[0].cron).toBe('15 9 15 * *');
    fireEvent.change(within(s1).getByLabelText('Repeats'), { target: { value: 'custom' } });
    fireEvent.input(within(s1).getByLabelText('Cron'), { target: { value: '*/30 9-17 * * 1-5' } });
    expect(onDoc.mock.lastCall![0].def.triggers[0].cron).toBe('*/30 9-17 * * 1-5');
    fireEvent.input(within(s1).getByLabelText('Timezone'), { target: { value: 'UTC' } });
    fireEvent.change(within(s1).getByLabelText('If the computer was asleep'), { target: { value: 'skip' } });
    expect(onDoc.mock.lastCall![0].def.triggers[0]).toMatchObject({ timezone: 'UTC', catch_up: 'skip' });
    fireEvent.input(within(s1).getByLabelText('Topic'), { target: { value: 'robots' } });
    expect(onDoc.mock.lastCall![0].def.triggers[0].inputs).toEqual({ topic: 'robots' });
    fireEvent.click(screen.getByRole('button', { name: 'Add schedule' }));
    expect(onDoc.mock.lastCall![0].def.triggers).toHaveLength(2);
    fireEvent.click(within(screen.getByRole('group', { name: 'Schedule 2' })).getByRole('button', { name: 'Remove schedule' }));
    expect(onDoc.mock.lastCall![0].def.triggers).toHaveLength(1);
  });

  it('edits inputs: key renames in templates, type, options, default', async () => {
    const onDoc = vi.fn();
    await render(Start, { inputs: { onDoc } });
    const i1 = screen.getByRole('group', { name: 'Input 1' });
    const key = within(i1).getByLabelText('Key');
    fireEvent.input(key, { target: { value: 'subject' } });
    fireEvent.blur(key);
    expect(onDoc.mock.lastCall![0].def.steps[0].args).toEqual(['{{inputs.subject}}']);
    fireEvent.change(within(i1).getByLabelText('Type'), { target: { value: 'choice' } });
    fireEvent.input(within(i1).getByLabelText('Options 1'), { target: { value: 'robots' } });
    fireEvent.change(within(i1).getByLabelText('Default'), { target: { value: 'robots' } });
    expect(onDoc.mock.lastCall![0].def.inputs[0]).toMatchObject({ key: 'subject', type: 'choice', options: ['robots'], default: 'robots' });
    fireEvent.click(screen.getByRole('button', { name: 'Add input' }));
    expect(onDoc.mock.lastCall![0].def.inputs.map((i: { key: string }) => i.key)).toEqual(['subject', 'input']);
  });
});

describe('SettingsInspector', () => {
  it('edits the title, after-run, result step and limits, and lists problems and warnings', async () => {
    const onDoc = vi.fn();
    await render(Settings, { inputs: { onDoc } });
    expect(screen.getByRole('alert').textContent).toContain('Add at least one step');
    expect(screen.getByText('Publish? has no edges')).toBeTruthy();
    fireEvent.input(screen.getByLabelText('Title'), { target: { value: 'Digest' } });
    fireEvent.change(screen.getByLabelText('After each run'), { target: { value: 'desk_review' } });
    fireEvent.change(screen.getByLabelText('Its result, when another automation runs it'), { target: { value: 'sum' } });
    fireEvent.input(screen.getByLabelText('Agents at once'), { target: { value: '3' } });
    expect(onDoc.mock.lastCall![0].def).toMatchObject({ title: 'Digest', after_run: 'desk_review', output_step: 'sum', limits: { max_parallel_agents: 3 } });
    fireEvent.change(screen.getByLabelText('Its result, when another automation runs it'), { target: { value: '' } });
    expect('output_step' in onDoc.mock.lastCall![0].def).toBe(false);
  });
});
