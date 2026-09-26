import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { WebNotice } from '@desk/web-server/contract';
import { FakeDeskBridge } from '../testing/fake-bridge';
import { WebNotifications } from './notifications';

class FakeNotification {
  static permission: NotificationPermission = 'granted';
  static answer: NotificationPermission = 'granted';
  static shown: FakeNotification[] = [];
  static readonly requestPermission = vi.fn(async (): Promise<NotificationPermission> => {
    FakeNotification.permission = FakeNotification.answer;
    return FakeNotification.answer;
  });
  onclick: (() => void) | null = null;
  closed = false;
  constructor(
    readonly title: string,
    readonly options: NotificationOptions,
  ) {
    FakeNotification.shown.push(this);
  }
  close(): void {
    this.closed = true;
  }
}

const NOTICE: WebNotice = { tag: 'question:1', title: 'Launch: Desk has a question', body: 'Which first?', route: '#/attention?item=question%3A1' };

let focused = false;
let stop: () => void = () => {};

function setup() {
  const bridge = new FakeDeskBridge();
  TestBed.configureTestingModule({ providers: bridge.providers });
  const notifications = TestBed.inject(WebNotifications);
  stop = notifications.start();
  return { bridge, notifications };
}

beforeEach(() => {
  focused = false;
  FakeNotification.permission = 'granted';
  FakeNotification.answer = 'granted';
  FakeNotification.shown = [];
  FakeNotification.requestPermission.mockClear();
  vi.stubGlobal('Notification', FakeNotification);
  vi.spyOn(document, 'hasFocus').mockImplementation(() => focused);
  // jsdom has no window.focus(); a click on a notification brings the tab forward with it.
  vi.spyOn(window, 'focus').mockImplementation(() => {});
});
afterEach(() => {
  stop();
  stop = () => {};
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  history.replaceState(null, '', window.location.pathname);
});

describe('WebNotifications', () => {
  it('shows each new item while the page is in the background, tagged with its id, and a click opens it', () => {
    const { bridge } = setup();
    bridge.emit('desk:notify', [NOTICE, { tag: 'approval:2', title: 'Launch: approve a command', body: 'npm publish', route: '#/attention?item=approval%3A2' }]);
    expect(FakeNotification.shown.map((n) => [n.title, n.options.body, n.options.tag])).toEqual([
      ['Launch: Desk has a question', 'Which first?', 'question:1'],
      ['Launch: approve a command', 'npm publish', 'approval:2'],
    ]);
    FakeNotification.shown[0]!.onclick?.();
    expect(window.focus).toHaveBeenCalledTimes(1);
    expect(window.location.hash).toBe('#/attention?item=question%3A1');
    expect(FakeNotification.shown[0]!.closed).toBe(true);
  });

  it('stays quiet while the page has focus, without permission, and once stopped', () => {
    const { bridge } = setup();
    focused = true;
    bridge.emit('desk:notify', [NOTICE]);
    focused = false;
    FakeNotification.permission = 'denied';
    bridge.emit('desk:notify', [NOTICE]);
    FakeNotification.permission = 'granted';
    stop();
    bridge.emit('desk:notify', [NOTICE]);
    expect(FakeNotification.shown).toEqual([]);
  });

  it('asks the browser and tells desk web the answer, and notices a change made in the site settings', async () => {
    FakeNotification.permission = 'default';
    const { bridge, notifications } = setup();
    expect(notifications.permission()).toBe('default');
    expect(await notifications.request()).toBe('granted');
    expect(FakeNotification.requestPermission).toHaveBeenCalledTimes(1);
    expect(notifications.permission()).toBe('granted');
    expect(bridge.notifyPermissions).toEqual(['granted']);
    FakeNotification.permission = 'denied';
    window.dispatchEvent(new Event('focus'));
    expect(notifications.permission()).toBe('denied');
    window.dispatchEvent(new Event('focus'));
    expect(bridge.notifyPermissions).toEqual(['granted', 'denied']);
  });
});
