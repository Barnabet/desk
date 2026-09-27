import { booleanAttribute, ChangeDetectionStrategy, Component, computed, input, ViewEncapsulation } from '@angular/core';
import type { Step } from '@desk/protocol';
import type { StartNodeData, StepNodeData, StubNodeData } from '@desk/ui-core';

/** Each kind's colour stripe (automations.css maps these to tokens). */
const KIND_CLASS: Record<Step['kind'], string> = { script: 'k-script', agent: 'k-agent', ask: 'k-ask', wait: 'k-wait', automation: 'k-automation', tell_desk: 'k-tell' };

/**
 * A step (mockup 2): kind, title and a one-line detail; red when validation found a problem; lit with its state in a run.
 * Its handles are React Flow's: the canvas starts connections from the bottom one (`connectable`) and marks the top one
 * `valid` while a connection that may land hovers it.
 */
@Component({
  selector: 'div[deskStepNode]',
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: {
    class: 'auto-node',
    '[class]': 'look()',
    '[class.selected]': 'selected()',
    '[class.invalid]': 'data().errors.length > 0',
    '[attr.data-testid]': "'node-' + data().step.id",
    '[attr.aria-label]': "data().kind + ' step: ' + data().step.title",
    '[attr.title]': "data().errors.length ? data().errors.join('\\n') : null",
  },
  template: `
    <div class="react-flow__handle react-flow__handle-top target" [class.valid]="dropValid()" data-handlepos="top" [attr.data-nodeid]="data().step.id"></div>
    <div class="auto-node-kind"><span>{{ data().kind }}</span>@if (data().run; as run) {<b>{{ run.badge }}</b>} @else if (data().output) {<b>result</b>}</div>
    <div class="auto-node-title">{{ data().step.title }}</div>
    <div class="auto-node-detail">{{ data().run?.detail ?? data().detail }}</div>
    <div class="react-flow__handle react-flow__handle-bottom source" [class.connectable]="connectable()" data-handlepos="bottom" [attr.data-nodeid]="data().step.id"></div>
    <div class="react-flow__handle react-flow__handle-right source auto-handle-side" data-handlepos="right"></div>
  `,
})
export class StepNode {
  readonly data = input.required<StepNodeData>();
  readonly selected = input(false, { transform: booleanAttribute });
  readonly connectable = input(false, { transform: booleanAttribute });
  readonly dropValid = input(false, { transform: booleanAttribute });
  protected readonly look = computed(() => {
    const d = this.data();
    return d.run ? `${KIND_CLASS[d.step.kind]} run-${d.run.tone}` : KIND_CLASS[d.step.kind];
  });
}

/** The Start pill: schedules and Run now. Clicking it edits schedules and inputs. */
@Component({
  selector: 'div[deskStartNode]',
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: {
    class: 'auto-start',
    '[class.selected]': 'selected()',
    '[class.invalid]': 'data().errors.length > 0',
    'data-testid': 'node-start',
    '[attr.aria-label]': "'Start: ' + data().label",
    '[attr.title]': "data().errors.length ? data().errors.join('\\n') : null",
  },
  template: `<span aria-hidden="true">▶</span><span class="auto-start-label">{{ data().label }}</span><div class="react-flow__handle react-flow__handle-bottom source" data-handlepos="bottom"></div>`,
})
export class StartNode {
  readonly data = input.required<StartNodeData>();
  readonly selected = input(false, { transform: booleanAttribute });
}

/** A declared route no edge takes: "<route> · ends". */
@Component({
  selector: 'div[deskStubNode]',
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-stub' },
  template: `<div class="react-flow__handle react-flow__handle-left target" data-handlepos="left"></div>{{ data().label }}`,
})
export class StubNode {
  readonly data = input.required<StubNodeData>();
}
