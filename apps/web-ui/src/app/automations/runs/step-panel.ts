import { booleanAttribute, ChangeDetectionStrategy, Component, computed, effect, inject, input, signal, ViewEncapsulation } from '@angular/core';
import type { ApprovalRow } from '@desk/client';
import type { RunDetail, Step, StepRunInfo, WorkspaceEntry } from '@desk/protocol';
import { agentActivity, agentTokens, askDeskText, canStopStep, clock, outputText, policyReason, relRunPath, STEP_KIND_LABEL, stepLook, tokens } from '@desk/ui-core';
import { describeArgs } from '../../attention/describe-args';
import { Button } from '../../components/button';
import { CodeBlock } from '../../components/code-block';
import { ConfirmDialog } from '../../components/confirm-dialog';
import { Field } from '../../components/field';
import { SafeMarkdown } from '../../components/safe-markdown';
import { ToastService } from '../../components/toast';
import { DeskBridge } from '../../core/desk-bridge';
import { NowService } from '../../core/now.service';
import { RouteService } from '../../core/route.service';
import { transcriptOf, type SessionState } from '../../core/session.service';
import { RunFileList } from './run-file-list';
import { StepTranscript } from './step-transcript';

const val = (e: Event): string => (e.target as HTMLInputElement).value;

/** An Ask me step waiting on the user: its question, the files it shows, a note, and its two buttons. */
@Component({
  selector: 'section[deskAskAnswer]',
  imports: [Button, Field, RunFileList, SafeMarkdown],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-card', 'aria-label': 'Your answer' },
  template: `
    <div deskSafeMarkdown [text]="question().text"></div>
    @if (files().length) {
      <div deskRunFileList [runId]="run().id" [files]="files()"></div>
    }
    <div deskField id="answer-note" label="Note (optional)">
      <textarea id="answer-note" class="textarea" rows="2" maxlength="2000" [value]="note()" (input)="note.set(val($event))"></textarea>
    </div>
    <div class="auto-inline">
      <button deskButton variant="primary" [pending]="busy() === 'approve'" [disabled]="busy() !== null" (click)="answer('approve')">{{ question().approve_label ?? 'Approve' }}</button>
      <button deskButton [pending]="busy() === 'reject'" [disabled]="busy() !== null" (click)="answer('reject')">{{ question().reject_label ?? 'Reject' }}</button>
    </div>
    <p class="muted small">Rejecting takes the route rejected. Later steps can read your note.</p>
  `,
})
export class AskAnswer {
  readonly run = input.required<RunDetail>();
  readonly row = input.required<StepRunInfo>();
  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  protected readonly val = val;
  protected readonly note = signal('');
  protected readonly busy = signal<string | null>(null);
  protected readonly question = computed(() => this.row().question ?? { text: '', files: [] });
  protected readonly files = computed(() =>
    this.question().files.flatMap((f) => {
      const path = relRunPath(f, this.run().id);
      return path ? [{ path, label: path.split('/').at(-1)! }] : [];
    }),
  );

  protected async answer(decision: 'approve' | 'reject'): Promise<void> {
    this.busy.set(decision);
    const note = this.note().trim();
    try {
      await this.bridge.call('automations.answer', { runId: this.run().id, stepId: this.row().step_id, req: { decision, ...(note ? { note } : {}) } });
    } catch (err) {
      this.toasts.error(err);
      this.busy.set(null);
    }
  }
}

/** A script step's approval: the command, why it asks, and Approve (and remember, unless grants are suspended) or Reject. */
@Component({
  selector: 'section[deskGateAnswer]',
  imports: [Button, Field],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-card', 'aria-label': 'Approval' },
  template: `
    <p>{{ 'Its script wants to run ' + gate().tool + ':' }}</p>
    <pre class="command"><span class="command-prompt">$ </span>{{ gate().subject }}</pre>
    <p class="muted small">{{ why().text }}</p>
    <div deskField id="gate-note" label="Note (optional)">
      <input id="gate-note" class="input" maxlength="2000" [value]="note()" (input)="note.set(val($event))" />
    </div>
    <div class="auto-inline">
      <button deskButton variant="primary" [pending]="busy() === 'approve'" [disabled]="busy() !== null" (click)="answer('approve')">Approve</button>
      @if (!grantsSuspended()) {
        <button deskButton [pending]="busy() === 'remember'" [disabled]="busy() !== null" (click)="answer('approve', true)">Approve and remember</button>
      }
      <button deskButton [pending]="busy() === 'reject'" [disabled]="busy() !== null" (click)="answer('reject')">Reject</button>
    </div>
    <p class="muted small">{{ grantsSuspended() ? 'Its grants are suspended until you keep them, so an approval is for this run only.' : 'Remember adds a grant, so later runs run this script without asking.' }}</p>
  `,
})
export class GateAnswer {
  readonly run = input.required<RunDetail>();
  readonly row = input.required<StepRunInfo>();
  readonly grantsSuspended = input(false, { transform: booleanAttribute });
  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  protected readonly val = val;
  protected readonly note = signal('');
  protected readonly busy = signal<string | null>(null);
  protected readonly gate = computed(() => this.row().gate ?? { tool: '', subject: '', reason: '' });
  protected readonly why = computed(() => policyReason(this.gate().reason));

  protected async answer(decision: 'approve' | 'reject', remember = false): Promise<void> {
    this.busy.set(remember ? 'remember' : decision);
    const note = this.note().trim();
    try {
      await this.bridge.call('automations.answer', { runId: this.run().id, stepId: this.row().step_id, req: { decision, ...(note ? { note } : {}), ...(remember ? { remember: true } : {}) } });
    } catch (err) {
      this.toasts.error(err);
      this.busy.set(null);
    }
  }
}

/** A step agent's pending approval, answered in place (remember adds a grant, unless grants are suspended). */
@Component({
  selector: 'section[deskAgentApproval]',
  imports: [Button, CodeBlock],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-card', '[attr.aria-label]': "'Approval: ' + approval().tool" },
  template: `
    @if (args().command; as command) {
      <pre class="command"><span class="command-prompt">$ </span>{{ command }}</pre>
    } @else {
      <div deskCodeBlock [code]="args().pretty" [language]="approval().tool"></div>
    }
    <div class="auto-inline">
      <button deskButton variant="primary" size="sm" [pending]="busy() === 'approved'" [disabled]="busy() !== null" (click)="resolve('approved')">Approve</button>
      @if (!grantsSuspended()) {
        <button deskButton size="sm" [pending]="busy() === 'remember'" [disabled]="busy() !== null" (click)="resolve('approved', true)">Approve and remember</button>
      }
      <button deskButton size="sm" [pending]="busy() === 'denied'" [disabled]="busy() !== null" (click)="resolve('denied')">Deny</button>
    </div>
  `,
})
export class AgentApproval {
  readonly approval = input.required<ApprovalRow>();
  readonly grantsSuspended = input(false, { transform: booleanAttribute });
  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  protected readonly busy = signal<string | null>(null);
  protected readonly args = computed(() => describeArgs(this.approval().tool, this.approval().arguments));

  protected async resolve(decision: 'approved' | 'denied', remember = false): Promise<void> {
    this.busy.set(remember ? 'remember' : decision);
    try {
      await this.bridge.call('approvals.resolve', { id: this.approval().id, decision, ...(remember ? { remember: true } : {}) });
    } catch (err) {
      this.toasts.error(err);
      this.busy.set(null);
    }
  }
}

/** A step's agent: its approvals, what it last said, its tokens, the transcript and Stop step. */
@Component({
  selector: 'div[deskAgentPart]',
  imports: [AgentApproval, Button, ConfirmDialog, SafeMarkdown, StepTranscript],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    @for (a of approvals(); track a.id) {
      <section deskAgentApproval [approval]="a" [grantsSuspended]="grantsSuspended()"></section>
    }
    @if (going()) {
      <section aria-label="Live output" class="auto-live">
        @if (last(); as text) {
          <div deskSafeMarkdown [text]="text"></div>
        } @else {
          <p class="muted small">Starting…</p>
        }
      </section>
    }
    <p class="muted small">{{ usedText() }}</p>
    <div class="auto-inline">
      <button deskButton size="sm" (click)="open.set(true)">Open transcript</button>
      @if (canStop()) {
        <button deskButton size="sm" variant="danger" (click)="stopping.set(true)">Stop step…</button>
      }
    </div>
    @if (open()) {
      <div deskStepTranscript [projectId]="projectId()" [s]="s()" [agentId]="agentId()" [title]="step().title" (close)="open.set(false)"></div>
    }
    @if (stopping()) {
      <div deskConfirmDialog title="Stop this step?" confirmLabel="Stop step" [danger]="true" (confirm)="stop()" (cancel)="stopping.set(false)">Its agent stops and the step fails. The run then does what the step's "If it fails" says.</div>
    }
  `,
})
export class AgentPart {
  readonly projectId = input.required<string>();
  readonly s = input.required<SessionState>();
  readonly run = input.required<RunDetail>();
  readonly step = input.required<Step>();
  readonly row = input.required<StepRunInfo>();
  readonly agentId = input.required<string>();
  readonly grantsSuspended = input(false, { transform: booleanAttribute });
  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  protected readonly open = signal(false);
  protected readonly stopping = signal(false);
  private readonly transcript = computed(() => transcriptOf(this.s().events, this.s().streams[this.agentId()], this.projectId(), this.agentId()));
  protected readonly last = computed(() => {
    const entries = this.transcript().entries;
    for (let i = entries.length - 1; i >= 0; i--) {
      const e = entries[i]!;
      if (e.kind === 'assistant' && e.text.trim()) return e.text.trim();
    }
    return null;
  });
  protected readonly usedText = computed(() => `${tokens(agentTokens(this.s().events, this.agentId()))} tokens`);
  protected readonly approvals = computed(() => (this.s().project?.approvals ?? []).filter((a) => a.agent_id === this.agentId()));
  protected readonly going = computed(() => this.row().status === 'running' || this.row().status === 'waiting');
  protected readonly canStop = computed(() => canStopStep(this.run(), this.row()));

  protected async stop(): Promise<void> {
    this.stopping.set(false);
    try {
      await this.bridge.call('automations.stopStep', { runId: this.run().id, stepId: this.row().step_id });
    } catch (err) {
      this.toasts.error(err);
    }
  }
}

/** A failed step: its error, and Ask Desk to fix. */
@Component({
  selector: 'section[deskFailed]',
  imports: [Button],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-card', 'aria-label': 'Error' },
  template: `
    <pre class="auto-error">{{ error() }}</pre>
    <div><button deskButton variant="primary" size="sm" [pending]="sending()" (click)="ask()">Ask Desk to fix</button></div>
  `,
})
export class Failed {
  readonly projectId = input.required<string>();
  readonly run = input.required<RunDetail>();
  readonly step = input.required<Step>();
  readonly row = input.required<StepRunInfo>();
  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  private readonly routes = inject(RouteService);
  protected readonly sending = signal(false);
  protected readonly error = computed(() => this.row().error ?? this.run().reason ?? 'It failed.');

  protected async ask(): Promise<void> {
    this.sending.set(true);
    const projectId = this.projectId();
    try {
      await this.bridge.call('projects.send', { id: projectId, text: askDeskText(this.run(), this.step().title, this.error()) });
      this.toasts.toast({ tone: 'info', message: 'Sent to Desk.', action: { label: 'Open conversation', run: () => this.routes.navigate({ name: 'project', id: projectId, tab: 'conversation' }) } });
    } catch (err) {
      this.toasts.error(err);
    } finally {
      this.sending.set(false);
    }
  }
}

/** What a finished step produced: its summary, route, your note and its outputs. */
@Component({
  selector: 'section[deskResults]',
  imports: [SafeMarkdown],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-results', 'aria-label': 'Results' },
  template: `
    @if (row().summary; as summary) {
      <div deskSafeMarkdown [text]="summary"></div>
    }
    <dl class="auto-facts">
      @if (row().route; as route) {
        <div><dt>route</dt><dd class="mono">{{ route }}</dd></div>
      }
      @if (row().note; as note) {
        <div><dt>your note</dt><dd>{{ note }}</dd></div>
      }
      @for (o of outputs(); track o.key) {
        <div><dt class="mono">{{ o.key }}</dt><dd class="auto-output">{{ o.text }}</dd></div>
      }
    </dl>
  `,
})
export class Results {
  readonly row = input.required<StepRunInfo>();
  protected readonly outputs = computed(() => Object.entries(this.row().outputs).map(([key, v]) => ({ key, text: outputText(v) })));
}

/** A script step's log: polled every 2 s while it runs, open then. Hidden until the first answer. */
@Component({
  selector: 'details[deskScriptLog]',
  imports: [CodeBlock],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-log', '[attr.open]': "live() ? '' : null", '[hidden]': 'log() === null' },
  template: `
    <summary>Log</summary>
    @if (log()) {
      <div deskCodeBlock [code]="tail()" language="log"></div>
    } @else {
      <p class="muted small">Nothing logged.</p>
    }
  `,
})
export class ScriptLog {
  readonly runId = input.required<string>();
  readonly stepId = input.required<string>();
  readonly live = input(false, { transform: booleanAttribute });
  private readonly bridge = inject(DeskBridge);
  protected readonly log = signal<string | null>(null);
  protected readonly tail = computed(() => (this.log() ?? '').split('\n').slice(-400).join('\n'));

  constructor() {
    effect((onCleanup) => {
      const runId = this.runId();
      const stepId = this.stepId();
      const live = this.live();
      let alive = true;
      const load = () =>
        this.bridge.call('automations.log', { runId, stepId }).then(
          (t) => {
            if (alive) this.log.set(t);
          },
          () => {
            if (alive) this.log.set('');
          },
        );
      void load();
      const timer = live ? setInterval(() => void load(), 2000) : undefined;
      onCleanup(() => {
        alive = false;
        clearInterval(timer);
      });
    });
  }
}

/** A step's folder: its files (opening in the viewer) and Open folder. */
@Component({
  selector: 'section[deskStepFiles]',
  imports: [Button, RunFileList],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-results', 'aria-label': 'Files' },
  template: `
    <div class="auto-inline">
      <h3 class="auto-sub">Files</h3>
      <span class="grow"></span>
      <button deskButton size="sm" variant="ghost" (click)="reveal()">Open folder</button>
    </div>
    @if (files().length) {
      <div deskRunFileList [runId]="runId()" [files]="files()"></div>
    } @else if (entries()) {
      <p class="muted small">No files.</p>
    }
  `,
})
export class StepFiles {
  readonly runId = input.required<string>();
  readonly stepId = input.required<string>();
  readonly status = input.required<string>();
  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  protected readonly entries = signal<WorkspaceEntry[] | null>(null);
  protected readonly files = computed(() => {
    const prefix = `steps/${this.stepId()}/`;
    return (this.entries() ?? []).filter((e) => e.type === 'file').map((f) => ({ path: f.path, label: f.path.replace(prefix, '') }));
  });

  constructor() {
    effect((onCleanup) => {
      const runId = this.runId();
      const stepId = this.stepId();
      this.status();
      let alive = true;
      this.bridge.call('automations.files', { runId, path: `steps/${stepId}` }).then(
        (list) => {
          if (alive) this.entries.set(list);
        },
        () => {
          if (alive) this.entries.set([]);
        },
      );
      onCleanup(() => {
        alive = false;
      });
    });
  }

  protected reveal(): void {
    void this.bridge.call('app.revealPath', { runId: this.runId(), stepId: this.stepId() }).catch((err: unknown) => this.toasts.error(err));
  }
}

/** The selected step of a run (spec §8.3): what it is doing, what it needs from you, or what it produced. */
@Component({
  selector: 'aside[deskStepPanel]',
  imports: [AgentPart, AskAnswer, Failed, GateAnswer, Results, ScriptLog, StepFiles],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-panel', '[attr.aria-label]': 'step()?.title ?? null' },
  template: `
    @if (step(); as step) {
      <p class="eyebrow">{{ eyebrow() }}</p>
      <h2>{{ step.title }}</h2>
      @if (row(); as row) {
        @if (row.attempt > 1) {
          <p class="muted small">{{ 'Attempt ' + row.attempt }}</p>
        }
        @if (row.status === 'waiting' && row.question) {
          <section deskAskAnswer [run]="run()" [row]="row"></section>
        }
        @if (row.status === 'waiting' && row.gate) {
          <section deskGateAnswer [run]="run()" [row]="row" [grantsSuspended]="grantsSuspended()"></section>
        }
        @if (row.agent_id; as agentId) {
          <div deskAgentPart [projectId]="projectId()" [s]="s()" [run]="run()" [step]="step" [row]="row" [agentId]="agentId" [grantsSuspended]="grantsSuspended()"></div>
        }
        @if (row.status === 'waiting' && row.resume_at) {
          <p>{{ 'Waits until ' + clock(row.resume_at) + '.' }}</p>
        }
        @if (row.child_run_id) {
          <p class="muted small">{{ 'Runs another automation (run ' + row.child_run_id + ').' }}</p>
        }
        @if (row.status === 'failed') {
          <section deskFailed [projectId]="projectId()" [run]="run()" [step]="step" [row]="row"></section>
        }
        @if (row.status === 'succeeded' || row.status === 'rejected') {
          <section deskResults [row]="row"></section>
        }
        @if (step.kind === 'script' && row.attempt > 0) {
          <details deskScriptLog [runId]="run().id" [stepId]="step.id" [live]="row.status === 'running'"></details>
        }
        @if (row.attempt > 0 && row.status !== 'pending') {
          <section deskStepFiles [runId]="run().id" [stepId]="step.id" [status]="row.status"></section>
        }
      }
      @if (!row() || row()?.attempt === 0) {
        <p class="muted">Not reached yet.</p>
      }
      @if (row()?.status === 'skipped') {
        <p class="muted">Skipped: none of the edges into it fired.</p>
      }
    }
  `,
})
export class StepPanel {
  readonly projectId = input.required<string>();
  readonly s = input.required<SessionState>();
  readonly run = input.required<RunDetail>();
  readonly stepId = input.required<string>();
  readonly grantsSuspended = input(false, { transform: booleanAttribute });
  private readonly now = inject(NowService).now;
  protected readonly clock = clock;
  protected readonly step = computed(() => this.run().definition.steps.find((x) => x.id === this.stepId()));
  protected readonly row = computed(() => this.run().steps.find((x) => x.step_id === this.stepId()));
  protected readonly eyebrow = computed(() => {
    const step = this.step();
    const row = this.row();
    const activity = row?.agent_id && row.status === 'running' ? agentActivity(this.s().events, row.agent_id) : null;
    return step ? `${STEP_KIND_LABEL[step.kind]} · ${stepLook(row, this.now(), activity).badge}` : '';
  });
}
