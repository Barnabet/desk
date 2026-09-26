import { ChangeDetectionStrategy, Component, ViewEncapsulation, computed, inject, signal } from '@angular/core';
import { Button } from '../components/button';
import { EndpointPanel, type EndpointState } from '../components/endpoint-panel';
import { describeError } from '../components/toast';
import { DeskBridge } from '../core/desk-bridge';
import { GlobalStore } from '../core/global.store';
import { markOnboarded } from '../core/onboarded';
import { RouteService } from '../core/route.service';
import { ProjectForm } from './project-form';

type Step = 'daemon' | 'endpoint' | 'project';
const STEPS: Array<{ id: Step; label: string }> = [
  { id: 'daemon', label: 'Daemon' },
  { id: 'endpoint', label: 'Model endpoint' },
  { id: 'project', label: 'First project' },
];

/** First run: start deskd, connect a model endpoint, create the first project (Onboarding.tsx). */
@Component({
  selector: 'div[deskOnboarding]',
  encapsulation: ViewEncapsulation.None,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [Button, EndpointPanel, ProjectForm],
  host: { class: 'onboarding' },
  template: `
    <div class="drag-strip"></div>
    <div class="card onboarding-card">
      <p class="eyebrow">Welcome to Desk</p>
      <ol class="steps" aria-label="Setup steps">
        @for (s of steps; track s.id; let i = $index) {
          <li [class.done]="i < index()" [attr.aria-current]="i === index() ? 'step' : null">{{ s.label }}</li>
        }
      </ol>
      @switch (step()) {
        @case ('daemon') {
          <section class="sheet-body" aria-labelledby="step-title">
            <h1 id="step-title" class="title">Start the Desk daemon</h1>
            <p class="subtitle">Desk runs in the background as deskd, so your projects keep working when this window is closed.</p>
            <p class="status-line"><span class="dot" [class.ok]="live()" [class.bad]="status() === 'mismatch'" aria-hidden="true"></span>{{ daemonLine() }}</p>
            @if (error()) {
              <p class="field-error" role="alert">{{ error() }} <button type="button" class="link" (click)="revealLogs()">Reveal logs</button></p>
            }
            <div class="actions">
              @if (live()) {
                <button deskButton variant="primary" (click)="step.set('endpoint')">Continue</button>
              } @else {
                <button deskButton variant="primary" [pending]="pending()" [disabled]="status() === 'starting'" (click)="start()">Start Desk</button>
              }
            </div>
          </section>
        }
        @case ('endpoint') {
          <section class="sheet-body" aria-labelledby="step-title">
            <h1 id="step-title" class="title">Connect a model endpoint</h1>
            <p class="subtitle">Desk talks to an OpenAI-compatible endpoint, such as a local proxy. The key goes to your macOS Keychain and is never shown again.</p>
            <div deskEndpointPanel (statusChanged)="endpoint.set($event)"></div>
            <div class="actions">
              <button deskButton [variant]="endpointReady() ? 'primary' : 'ghost'" (click)="step.set('project')">{{ endpointReady() ? 'Continue' : 'Skip for now' }}</button>
            </div>
          </section>
        }
        @case ('project') {
          <section class="sheet-body" aria-labelledby="step-title">
            <h1 id="step-title" class="title">Create your first project</h1>
            <p class="subtitle">A project is a goal Desk works toward, with its own threads, library and memory.</p>
            @if (projectCount() > 0) {
              <p class="status-line">You already have {{ projectCount() }} project{{ projectCount() === 1 ? '' : 's' }}. <button deskButton variant="ghost" size="sm" (click)="finish()">Skip to the map</button></p>
            }
            <form deskProjectForm (created)="finish()"></form>
          </section>
        }
      }
    </div>
  `,
})
export class Onboarding {
  private readonly bridge = inject(DeskBridge);
  private readonly global = inject(GlobalStore);
  private readonly routes = inject(RouteService);

  protected readonly steps = STEPS;
  protected readonly step = signal<Step>('daemon');
  protected readonly index = computed(() => STEPS.findIndex((s) => s.id === this.step()));

  protected readonly status = computed(() => this.global.state().connection.status);
  protected readonly live = computed(() => this.status() === 'live' || this.status() === 'connecting');
  protected readonly daemonLine = computed(() => {
    const { connection, health } = this.global.state();
    if (this.live()) return `deskd ${health?.version ?? ''} is running.`.replace('  ', ' ');
    if (connection.status === 'mismatch') return connection.detail ?? 'deskd needs an update.';
    return connection.status === 'offline' ? 'deskd is not running yet.' : 'Looking for deskd…';
  });
  protected readonly pending = signal(false);
  protected readonly error = signal<string | null>(null);

  protected readonly endpoint = signal<EndpointState>(null);
  protected readonly endpointReady = computed(() => {
    const s = this.endpoint();
    return s === 'unsupported' || (s !== null && s.configured);
  });

  protected readonly projectCount = computed(() => this.global.state().overview.length);

  protected async start(): Promise<void> {
    this.pending.set(true);
    this.error.set(null);
    try {
      await this.bridge.call('daemon.start', {});
    } catch (err) {
      this.error.set(describeError(err).message);
    } finally {
      this.pending.set(false);
    }
  }

  protected revealLogs(): void {
    void this.bridge.call('app.revealLogs', {}).catch(() => {});
  }

  protected finish(): void {
    markOnboarded();
    this.routes.navigate({ name: 'map' });
  }
}
