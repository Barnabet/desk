import { DOCUMENT, NgComponentOutlet, NgTemplateOutlet } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, DestroyRef, effect, inject, untracked, ViewEncapsulation } from '@angular/core';
import { CommandPalette, PaletteToggle } from './components/command-palette';
import { ConnectionOverlay } from './components/connection-overlay';
import { ErrorBoundary } from './components/error-boundary';
import { ProjectFrame } from './conversation/project-frame';
import { FolderBrowser } from './components/folder-browser';
import { ProjectNav } from './components/project-nav';
import { SignedOut } from './components/signed-out';
import { TitleBar } from './components/title-bar';
import { Toaster } from './components/toast';
import { DeskBridge } from './core/desk-bridge';
import { GlobalStore } from './core/global.store';
import { WebNotifications } from './core/notifications';
import { isOnboarded } from './core/onboarded';
import { RouteService } from './core/route.service';
import { SessionService } from './core/session.service';
import { screenFor, screenKey } from './screen-for';

/**
 * A browser opens a file dropped where no drop zone takes it, replacing the page and whatever was unsaved (Electron
 * refuses the navigation instead). Cancels file drags on the whole document, after drop zones (the Library, the
 * conversation's chat) have had them: a drag nothing took shows "no drop", and one a zone took keeps its drop effect.
 * Returns the function that removes the listeners.
 */
function guardFileDrops(doc: Document): () => void {
  const guard = (e: DragEvent) => {
    if (!Array.from(e.dataTransfer?.types ?? []).includes('Files')) return;
    if (e.type === 'dragover' && !e.defaultPrevented && e.dataTransfer) e.dataTransfer.dropEffect = 'none';
    e.preventDefault();
  };
  doc.addEventListener('dragover', guard);
  doc.addEventListener('drop', guard);
  return () => {
    doc.removeEventListener('dragover', guard);
    doc.removeEventListener('drop', guard);
  };
}

/** The web UI: the signed-out page, onboarding, or the shell (title bar, project tabs, screen, connection overlay, toasts). */
@Component({
  selector: 'desk-root',
  imports: [NgComponentOutlet, NgTemplateOutlet, CommandPalette, ConnectionOverlay, ErrorBoundary, FolderBrowser, ProjectFrame, ProjectNav, SignedOut, TitleBar, Toaster],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: block; height: 100%' },
  template: `
    @if (bridge.signedOut()) {
      <div class="app">
        <main class="screen"><div deskSignedOut></div></main>
      </div>
    } @else {
      <div deskErrorBoundary scope="whole">
        <ng-template>
          @if (route().name === 'onboarding') {
            @for (view of screen(); track view.key) {
              <ng-container *ngComponentOutlet="view.component; inputs: view.inputs" />
            }
            <div deskToaster></div>
          } @else {
            <div class="app">
              <header deskTitleBar [route]="route()"></header>
              @if (project(); as p) {
                <nav deskProjectNav [projectId]="p.id" [tab]="p.tab"></nav>
              }
              <main class="screen">
                <!-- One frame for the conversation and threads tabs, outside the screen's boundary and kept while the project
                     stays the same, so switching tabs folds or unfolds its timeline. -->
                @for (f of frame(); track f.id) {
                  <div deskProjectFrame [projectId]="f.id" [mode]="f.mode" [focus]="f.focus">
                    <ng-container *ngTemplateOutlet="screenBoundary" />
                  </div>
                } @empty {
                  <ng-container *ngTemplateOutlet="screenBoundary" />
                }
                <div deskConnectionOverlay></div>
              </main>
              @if (palette.open()) {
                <div deskCommandPalette></div>
              }
              <div deskToaster></div>
            </div>
          }
        </ng-template>
      </div>
      <ng-template #screenBoundary>
        <div deskErrorBoundary [resetKey]="key()">
          <ng-template>
            @for (view of screen(); track view.key) {
              <ng-container *ngComponentOutlet="view.component; inputs: view.inputs" />
            }
          </ng-template>
        </div>
      </ng-template>
      @for (request of folderRequests(); track request) {
        <div deskFolderBrowser [purpose]="request.purpose" (picked)="bridge.answerFolder($event)"></div>
      }
    }
  `,
})
export class App {
  protected readonly bridge = inject(DeskBridge);
  /** ⌘K / Ctrl-K: the command palette, rendered while it is open. */
  protected readonly palette = inject(PaletteToggle);
  private readonly routes = inject(RouteService);
  protected readonly route = this.routes.route;
  protected readonly project = computed(() => {
    const r = this.route();
    return r.name === 'project' ? r : null;
  });
  protected readonly key = computed(() => screenKey(this.route()));
  /** The conversation's and threads' frame, as a one-item list tracked by project id (ProjectFrame, App.tsx). */
  protected readonly frame = computed(() => {
    const r = this.route();
    if (r.name !== 'project' || (r.tab !== 'conversation' && r.tab !== 'threads')) return [];
    return [{ id: r.id, mode: r.tab === 'threads' ? ('full' as const) : ('desk' as const), focus: r.threadId ?? null }];
  });
  /** The route's screen as a one-item list keyed by screenKey, so a new key mounts a fresh screen. */
  protected readonly screen = computed(() => {
    const view = screenFor(this.route());
    return view ? [{ ...view, key: this.key() }] : [];
  });

  /** The open folder request as a one-item list tracked by the request, so a new request gets a fresh folder browser. */
  protected readonly folderRequests = computed(() => {
    const request = this.bridge.folderRequest();
    return request ? [request] : [];
  });

  constructor() {
    // Routes desk:event, desk:events and desk:ephemeral to project sessions from the start (startSessionRouting).
    inject(SessionService);
    const stopGlobal = inject(GlobalStore).start();
    const stopNotices = inject(WebNotifications).start();
    const stopNavigate = this.bridge.onPush<string>('desk:navigate', (to) => this.routes.navigate(to));
    const stopDrops = guardFileDrops(inject(DOCUMENT));
    inject(DestroyRef).onDestroy(() => {
      stopGlobal();
      stopNotices();
      stopNavigate();
      stopDrops();
    });
    effect(() => {
      const name = this.route().name;
      if (this.bridge.signedOut()) return;
      untracked(() => {
        if (name === 'tray') this.routes.replace({ name: 'map' });
        else if (name !== 'onboarding' && !isOnboarded()) this.routes.navigate({ name: 'onboarding' });
      });
    });
  }
}
