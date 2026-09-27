import { ChangeDetectionStrategy, Component, computed, input, linkedSignal } from '@angular/core';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/angular';
import { describe, expect, it, vi } from 'vitest';
import type { AutomationDefinition } from '@desk/protocol';
import { addStep, START_ID, type AutomationDoc, type GraphSelection } from '@desk/ui-core';
import { digestDef } from '@desk/ui-core/testing';
import { FakeDeskBridge } from '../../testing/fake-bridge';
import { EdgeInspector } from './edge-inspector';
import { StepInspector } from './step-inspector';

const doc = (def: AutomationDefinition = digestDef()): AutomationDoc => ({ def, layout: { [START_ID]: { x: 0, y: 0 } } });

/** The React Harness: holds the doc and the selection, as DesignView would. */
@Component({
  selector: 'desk-inspector-harness',
  imports: [StepInspector, EdgeInspector],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (stepId(); as id) {
      <aside deskStepInspector projectId="p" [doc]="d()" [stepId]="id" selfName="digest" [sources]="sources" [errors]="id === 'ok' ? okErrors : noErrors" (docChange)="change($event)" (renamed)="sel.set({ kind: 'step', id: $event })" (remove)="deleted()()"></aside>
    } @else if (edge() !== null) {
      <aside deskEdgeInspector [doc]="d()" [index]="edge() ?? 0" [errors]="noErrors" (docChange)="change($event)" (remove)="deleted()()"></aside>
    }
  `,
})
class Harness {
  readonly initial = input.required<AutomationDoc>();
  readonly select = input.required<GraphSelection>();
  readonly onDoc = input.required<(d: AutomationDoc) => void>();
  readonly deleted = input<() => void>(() => {});
  protected readonly d = linkedSignal(() => this.initial());
  protected readonly sel = linkedSignal(() => this.select());
  protected readonly stepId = computed(() => {
    const s = this.sel();
    return s.kind === 'step' ? s.id : null;
  });
  protected readonly edge = computed(() => {
    const s = this.sel();
    return s.kind === 'edge' ? s.index : null;
  });
  protected readonly sources = [{ id: 's1', label: 'site' }];
  protected readonly okErrors = ['question: required'];
  protected readonly noErrors: string[] = [];

  protected change(next: AutomationDoc): void {
    this.d.set(next);
    this.onDoc()(next);
  }
}

function bridge() {
  return new FakeDeskBridge({
    'skills.list': (i: { projectId?: string }) => (i.projectId ? [{ name: 'digest', scope: 'project', description: '', dir: '/d', version: 1 }] : []),
    'builtins.list': () => [{ name: 'pdf-toolkit', enabled: true, broken: null }],
    'skills.get': () => ({ name: 'digest', files: [{ path: 'SKILL.md', size: 1 }, { path: 'scripts/fetch.py', size: 1 }, { path: 'scripts/diff.py', size: 1 }] }),
    'automations.list': () => [
      { id: 'a1', name: 'digest' },
      { id: 'a2', name: 'notify' },
    ],
    'automations.get': () => ({ definition: { inputs: [{ key: 'message', label: 'Message', type: 'text', required: true }] } }),
  });
}

const show = (initial: AutomationDoc, select: GraphSelection, onDoc: (d: AutomationDoc) => void, deleted: () => void = () => {}) =>
  render(Harness, { inputs: { initial, select, onDoc, deleted }, providers: bridge().providers });

describe('StepInspector', () => {
  it('edits a script step: title, id (everywhere), skill and script pickers, arguments, routes and errors', async () => {
    const onDoc = vi.fn();
    await show(doc(), { kind: 'step', id: 'fetch' }, onDoc);
    expect(screen.getByText('Script step')).toBeTruthy();
    fireEvent.input(screen.getByLabelText('Title'), { target: { value: 'Fetch the pages' } });
    expect(onDoc.mock.lastCall![0].def.steps[0].title).toBe('Fetch the pages');

    const id = screen.getByLabelText('Id');
    fireEvent.input(id, { target: { value: 'Bad Id' } });
    fireEvent.blur(id);
    expect(screen.getByRole('alert').textContent).toMatch(/lowercase/i);
    fireEvent.input(id, { target: { value: 'grab' } });
    fireEvent.blur(id);
    expect(onDoc.mock.lastCall![0].def.edges[0]).toEqual({ from: 'grab', to: 'sum', route: 'changed' });

    const skill = (await screen.findByRole('option', { name: 'pdf-toolkit (built-in)' })).closest('select')!;
    expect(within(skill).getAllByRole('option').map((o) => o.textContent)).toEqual(['Choose a skill…', 'digest', 'pdf-toolkit (built-in)']);
    const script = screen.getByLabelText('Script');
    await waitFor(() => expect(within(script).getAllByRole('option').map((o) => o.textContent)).toEqual(['Choose a script…', 'scripts/diff.py', 'scripts/fetch.py']));
    fireEvent.change(script, { target: { value: 'scripts/diff.py' } });
    expect(onDoc.mock.lastCall![0].def.steps[0]).toMatchObject({ skill: 'digest', script: 'scripts/diff.py' });

    fireEvent.click(screen.getByRole('button', { name: 'Add argument' }));
    fireEvent.input(screen.getByLabelText('Arguments 2'), { target: { value: '--fast' } });
    expect(onDoc.mock.lastCall![0].def.steps[0].args).toEqual(['{{inputs.topic}}', '--fast']);

    fireEvent.click(screen.getByRole('button', { name: 'Add route' }));
    fireEvent.input(screen.getByLabelText('Routes 3'), { target: { value: 'error' } });
    expect(screen.getByText(/set by Desk/)).toBeTruthy();

    fireEvent.change(screen.getByLabelText('If it fails'), { target: { value: 'retry' } });
    expect(onDoc.mock.lastCall![0].def.steps[0].on_error).toEqual({ retry: 1 });
    fireEvent.input(screen.getByLabelText('Attempts after the first'), { target: { value: '3' } });
    expect(onDoc.mock.lastCall![0].def.steps[0].on_error).toEqual({ retry: 3 });
  });

  it('edits an agent step: brief, skills, output keys, worktree', async () => {
    const onDoc = vi.fn();
    await show(doc(), { kind: 'step', id: 'sum' }, onDoc);
    fireEvent.input(screen.getByLabelText('Brief'), { target: { value: 'Summarise.' } });
    expect(onDoc.mock.lastCall![0].def.steps[1].brief).toBe('Summarise.');
    fireEvent.click(await screen.findByLabelText('digest'));
    expect(onDoc.mock.lastCall![0].def.steps[1].skills).toEqual(['web-research', 'digest']);
    fireEvent.click(screen.getByRole('button', { name: 'Add output' }));
    fireEvent.input(screen.getByLabelText('Output 2 key'), { target: { value: 'count' } });
    fireEvent.input(screen.getByLabelText('Output 2 meaning'), { target: { value: 'how many changed' } });
    expect(onDoc.mock.lastCall![0].def.steps[1].output_keys).toEqual([
      { key: 'headline', description: 'the biggest change' },
      { key: 'count', description: 'how many changed' },
    ]);
    fireEvent.change(screen.getByLabelText('Work in a git worktree of'), { target: { value: 's1' } });
    expect(onDoc.mock.lastCall![0].def.steps[1].git_source_id).toBe('s1');
  });

  it('shows a step’s problems, switches a wait between minutes and a time, and picks a sub-automation’s inputs', async () => {
    const onDoc = vi.fn();
    const onDelete = vi.fn();
    const withMore = addStep(addStep(doc(), 'wait', 'ok').doc, 'automation', 'ok').doc;
    const view = await show(withMore, { kind: 'step', id: 'ok' }, onDoc, onDelete);
    expect(screen.getByRole('alert').textContent).toContain('question: required');
    fireEvent.click(screen.getByRole('button', { name: 'Delete step' }));
    expect(onDelete).toHaveBeenCalled();

    await view.rerender({ inputs: { select: { kind: 'step', id: 'wait' } }, partialUpdate: true });
    fireEvent.click(screen.getByLabelText('Until a time of day'));
    expect(onDoc.mock.lastCall![0].def.steps.find((s: { id: string }) => s.id === 'wait')).toMatchObject({ until: '08:00' });
    expect('minutes' in onDoc.mock.lastCall![0].def.steps.find((s: { id: string }) => s.id === 'wait')).toBe(false);

    await view.rerender({ inputs: { select: { kind: 'step', id: 'automation' } }, partialUpdate: true });
    const pick = await screen.findByLabelText('Automation to run');
    await waitFor(() => expect(within(pick).getAllByRole('option').map((o) => o.textContent)).toEqual(['Choose an automation…', 'notify']));
    fireEvent.change(pick, { target: { value: 'notify' } });
    fireEvent.input(await screen.findByLabelText('Message'), { target: { value: '{{steps.sum.summary}}' } });
    expect(onDoc.mock.lastCall![0].def.steps.find((s: { id: string }) => s.id === 'automation')).toMatchObject({ automation: 'notify', inputs: { message: '{{steps.sum.summary}}' } });
  });
});

describe('EdgeInspector', () => {
  it('sets an edge’s route and condition, offering the paths its source can see', async () => {
    const onDoc = vi.fn();
    await show(doc(), { kind: 'edge', index: 0 }, onDoc);
    expect(screen.getByText('Fetch pages → Summarise')).toBeTruthy();
    const route = screen.getByLabelText('Fires');
    expect(within(route).getAllByRole('option').map((o) => o.textContent)).toEqual(['When it succeeds', 'On route changed', 'On route unchanged']);
    fireEvent.change(route, { target: { value: 'unchanged' } });
    expect(onDoc.mock.lastCall![0].def.edges[0]).toEqual({ from: 'fetch', to: 'sum', route: 'unchanged' });
    fireEvent.click(screen.getByRole('button', { name: 'steps.fetch.status' }));
    expect(onDoc.mock.lastCall![0].def.edges[0].when).toBe('steps.fetch.status');
    fireEvent.input(screen.getByLabelText('Only if'), { target: { value: '' } });
    expect(onDoc.mock.lastCall![0].def.edges[0]).toEqual({ from: 'fetch', to: 'sum', route: 'unchanged' });
  });
});
