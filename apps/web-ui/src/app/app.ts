import { DOCUMENT, NgComponentOutlet } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, DestroyRef, effect, inject, untracked, ViewEncapsulation } from '@angular/core';
import { ConnectionOverlay } from './components/connection-overlay';
import { ErrorBoundary } from './components/error-boundary';
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
 * refuses the navigation instead). Cancels file drags on the whole document, after drop zones (the Library) have had
 * them: a drag nothing took shows "no drop". Returns the function that removes the listeners.
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
  imports: [NgComponentOutlet, ConnectionOverlay, ErrorBoundary, FolderBrowser, ProjectNav, SignedOut, TitleBar, Toaster],
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
                <div deskErrorBoundary [resetKey]="key()">
                  <ng-template>
                    @for (view of screen(); track view.key) {
                      <ng-container *ngComponentOutlet="view.component; inputs: view.inputs" />
                    }
                  </ng-template>
                </div>
                <div deskConnectionOverlay></div>
              </main>
              <div deskToaster></div>
            </div>
          }
        </ng-template>
      </div>
      @for (request of folderRequests(); track request) {
        <div deskFolderBrowser [purpose]="request.purpose" (picked)="bridge.answerFolder($event)"></div>
      }
    }
  `,
})
export class App {
  protected readonly bridge = inject(DeskBridge);
  private readonly routes = inject(RouteService);
  protected readonly route = this.routes.route;
  protected readonly project = computed(() => {
    const r = this.route();
    return r.name === 'project' ? r : null;
  });
  protected readonly key = computed(() => screenKey(this.route()));
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
