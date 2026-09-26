import { Injectable, inject, signal, type Signal } from '@angular/core';
import type { NotifyPermission, WebNotice } from '@desk/web-server/contract';
import { DeskBridge } from './desk-bridge';
import { RouteService } from './route.service';

function browserPermission(): NotifyPermission {
  return typeof Notification === 'undefined' ? 'denied' : Notification.permission;
}

/**
 * Browser notifications for new attention (spec §5). desk web pushes `desk:notify` to every signed-in tab; a tab shows the
 * items only while it is in the background (Electron skips a focused window), tagged with the item id so several tabs show
 * one. desk web claims notifications from deskd only while a tab reports `granted`, so every change of permission is reported.
 */
@Injectable({ providedIn: 'root' })
export class WebNotifications {
  private readonly bridge = inject(DeskBridge);
  private readonly routes = inject(RouteService);
  private readonly value = signal<NotifyPermission>(browserPermission());
  /** What this browser allows the page: `default` until the user answers the prompt. */
  readonly permission: Signal<NotifyPermission> = this.value.asReadonly();

  /**
   * Shows `desk:notify` items, and re-reads the permission whenever the window regains focus (the user may have changed it
   * in the browser's site settings). Returns the function that stops both.
   */
  start(): () => void {
    const off = this.bridge.onPush<WebNotice[]>('desk:notify', (notices) => this.show(notices));
    const recheck = () => this.report(browserPermission());
    window.addEventListener('focus', recheck);
    return () => {
      off();
      window.removeEventListener('focus', recheck);
    };
  }

  /** Asks the browser for permission (call it from a click: browsers prompt only on a user gesture) and reports the answer. */
  async request(): Promise<NotifyPermission> {
    if (typeof Notification === 'undefined') return 'denied';
    const answer = await Notification.requestPermission();
    this.report(answer);
    return answer;
  }

  private report(permission: NotifyPermission): void {
    if (permission === this.value()) return;
    this.value.set(permission);
    this.bridge.setNotifyPermission(permission);
  }

  private show(notices: WebNotice[]): void {
    if (browserPermission() !== 'granted' || document.hasFocus()) return;
    for (const notice of notices) {
      const shown = new Notification(notice.title, { body: notice.body, tag: notice.tag });
      shown.onclick = () => {
        window.focus();
        this.routes.navigate(notice.route);
        shown.close();
      };
    }
  }
}
