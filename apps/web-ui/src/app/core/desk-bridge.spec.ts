import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { DeskBridge, DeskCallError } from './desk-bridge';

/** A WebSocket the spec drives: `open()`, `receive(frame)`, `drop(code)`. */
class FakeSocket {
  static instances: FakeSocket[] = [];
  readyState = 0;
  readonly sent: unknown[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: ((e: { code: number }) => void) | null = null;
  closedWith: number | null = null;

  constructor(readonly url: string) {
    FakeSocket.instances.push(this);
  }

  send(data: string): void {
    this.sent.push(JSON.parse(data));
  }

  close(code = 1000): void {
    this.closedWith = code;
    this.readyState = 3;
  }

  open(): void {
    this.readyState = 1;
    this.onopen?.();
  }

  receive(frame: unknown): void {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }

  drop(code: number): void {
    this.readyState = 3;
    this.onclose?.({ code });
  }
}

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
const refused = () => json({ ok: false, error: { code: 'unauthorized', message: 'Sign in again.' } }, 401);
const lastSocket = (): FakeSocket => FakeSocket.instances.at(-1)!;
/** The session secret each /rpc call sent, in order. */
const sentSecrets = () => fetchMock.mock.calls.map(([, init]) => (init.headers as Record<string, string>)['x-desk-session']);
let fetchMock: Mock<(url: string, init: RequestInit) => Promise<Response>>;

beforeEach(() => {
  FakeSocket.instances = [];
  localStorage.clear();
  localStorage.setItem('desk.session', 's3cret');
  vi.stubGlobal('WebSocket', FakeSocket);
  fetchMock = vi.fn<(url: string, init: RequestInit) => Promise<Response>>();
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('DeskCallError', () => {
  it('is built from an IpcError or from its parts', () => {
    expect(new DeskCallError({ code: 'conflict', message: 'Already decided', status: 409 })).toMatchObject({ name: 'DeskCallError', code: 'conflict', message: 'Already decided', status: 409 });
    const e = new DeskCallError('invalid_url', 'Only web and mail links can be opened.');
    expect(e).toBeInstanceOf(Error);
    expect(e).toMatchObject({ code: 'invalid_url', message: 'Only web and mail links can be opened.', status: undefined });
  });
});

describe('DeskBridge.call over /rpc', () => {
  it('posts the operation with the session secret and decodes bytes in the answer', async () => {
    fetchMock.mockResolvedValue(json({ ok: true, value: { $bytes: 'AAEC' } }));
    await expect(TestBed.inject(DeskBridge).call('threads.file', { id: 't', path: 'a.bin' })).resolves.toEqual(new Uint8Array([0, 1, 2]));
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe('/rpc/threads.file');
    expect(init.method).toBe('POST');
    expect(init.headers).toEqual({ 'content-type': 'application/json', 'x-desk-session': 's3cret' });
    expect(JSON.parse(String(init.body))).toEqual({ id: 't', path: 'a.bin' });
  });

  it('turns an answered failure into DeskCallError, keeping its code and status', async () => {
    const bridge = TestBed.inject(DeskBridge);
    fetchMock.mockResolvedValueOnce(json({ ok: false, error: { code: 'not_found', message: 'Unknown project: x', status: 404 } }));
    await expect(bridge.call('projects.get', { id: 'x' })).rejects.toMatchObject({ name: 'DeskCallError', code: 'not_found', message: 'Unknown project: x', status: 404 });
    fetchMock.mockResolvedValueOnce(json({ ok: false, error: { code: 'too_large', message: 'That upload is too large.' } }, 413));
    await expect(bridge.call('projects.get', { id: 'x' })).rejects.toMatchObject({ code: 'too_large', status: 413 });
    fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'));
    await expect(bridge.call('projects.get', { id: 'x' })).rejects.toMatchObject({ code: 'web_unreachable' });
  });

  it('turns an answer that is not JSON into bad_response, such as the plain-text 421 for a wrong Host', async () => {
    const bridge = TestBed.inject(DeskBridge);
    fetchMock.mockResolvedValueOnce(new Response('Misdirected Request', { status: 421, headers: { 'content-type': 'text/plain' } }));
    await expect(bridge.call('overview', {})).rejects.toMatchObject({ name: 'DeskCallError', code: 'bad_response', message: 'desk web answered overview with HTTP 421.', status: 421 });
    expect(bridge.signedOut()).toBe(false);
  });

  it('signs out on 401: the secret is dropped and later calls never leave the page', async () => {
    fetchMock.mockResolvedValue(json({ ok: false, error: { code: 'unauthorized', message: 'Sign in again.' } }, 401));
    const bridge = TestBed.inject(DeskBridge);
    expect(bridge.signedOut()).toBe(false);
    await expect(bridge.call('overview', {})).rejects.toMatchObject({ code: 'unauthorized', status: 401 });
    expect(bridge.signedOut()).toBe(true);
    expect(localStorage.getItem('desk.session')).toBeNull();
    await expect(bridge.call('overview', {})).rejects.toMatchObject({ code: 'unauthorized' });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('keeps a secret another tab stored while a call was out: the 401 for the old one retries with it', async () => {
    const bridge = TestBed.inject(DeskBridge);
    let answer: (res: Response) => void = () => {};
    fetchMock.mockImplementationOnce(() => new Promise<Response>((resolve) => (answer = resolve)));
    fetchMock.mockResolvedValueOnce(json({ ok: true, value: { n: 1 } }));
    const done = bridge.call('overview', {});
    localStorage.setItem('desk.session', 'fresh');
    window.dispatchEvent(new StorageEvent('storage', { key: 'desk.session', newValue: 'fresh' }));
    answer(refused());
    await expect(done).resolves.toEqual({ n: 1 });
    expect(sentSecrets()).toEqual(['s3cret', 'fresh']);
    expect(bridge.signedOut()).toBe(false);
    expect(localStorage.getItem('desk.session')).toBe('fresh');
  });

  it('on a 401, adopts a newer secret another tab stored and tries once more with it', async () => {
    const bridge = TestBed.inject(DeskBridge);
    fetchMock.mockImplementationOnce(async () => {
      // Another tab signed in again; this page has not seen its storage event yet.
      localStorage.setItem('desk.session', 'fresh');
      return refused();
    });
    fetchMock.mockResolvedValueOnce(json({ ok: true, value: { n: 1 } }));
    await expect(bridge.call('overview', {})).resolves.toEqual({ n: 1 });
    expect(sentSecrets()).toEqual(['s3cret', 'fresh']);
    expect(bridge.signedOut()).toBe(false);

    fetchMock.mockImplementationOnce(async () => {
      localStorage.setItem('desk.session', 'newer');
      return refused();
    });
    fetchMock.mockResolvedValueOnce(refused());
    await expect(bridge.call('overview', {})).rejects.toMatchObject({ code: 'unauthorized', status: 401 });
    expect(sentSecrets()).toEqual(['s3cret', 'fresh', 'fresh', 'newer']);
    expect(bridge.signedOut()).toBe(true);
    expect(localStorage.getItem('desk.session')).toBeNull();
  });

  it('starts signed out without a secret, and signs in when another tab stores one', () => {
    localStorage.clear();
    const bridge = TestBed.inject(DeskBridge);
    expect(bridge.signedOut()).toBe(true);
    bridge.onPush('desk:global', () => {});
    expect(FakeSocket.instances).toHaveLength(0);
    localStorage.setItem('desk.session', 'fresh');
    window.dispatchEvent(new StorageEvent('storage', { key: 'desk.session', newValue: 'fresh' }));
    expect(bridge.signedOut()).toBe(false);
    expect(FakeSocket.instances).toHaveLength(1);
    lastSocket().open();
    expect(lastSocket().sent).toEqual([{ session: 'fresh' }]);
  });
});

describe('DeskBridge host operations', () => {
  it('opens web and mail links itself, synchronously, and refuses the rest', async () => {
    const open = vi.spyOn(window, 'open').mockReturnValue(null);
    const bridge = TestBed.inject(DeskBridge);
    const done = bridge.call('app.openExternal', { url: 'HTTPS://Example.com/a' });
    expect(open).toHaveBeenCalledWith('https://example.com/a', '_blank', 'noopener,noreferrer');
    await expect(done).resolves.toEqual({ ok: true });
    await expect(bridge.call('app.openExternal', { url: 'javascript:alert(1)' })).rejects.toMatchObject({ name: 'DeskCallError', code: 'invalid_url', message: 'Only web and mail links can be opened.' });
    expect(open).toHaveBeenCalledTimes(1);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('saves a file through an octet-stream blob, revoked right after the click', async () => {
    const blobs: Blob[] = [];
    const revoke = vi.fn();
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, writable: true, value: (b: Blob) => (blobs.push(b), 'blob:desk/1') });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, writable: true, value: revoke });
    const clicks: Array<{ href: string | null; download: string; connected: boolean }> = [];
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      clicks.push({ href: this.getAttribute('href'), download: this.download, connected: this.isConnected });
    });
    try {
      await expect(TestBed.inject(DeskBridge).call('app.saveFile', { name: 'report.pdf', data: new Uint8Array([1, 2, 3]) })).resolves.toBe(true);
      expect(blobs.map((b) => [b.type, b.size])).toEqual([['application/octet-stream', 3]]);
      expect(clicks).toEqual([{ href: 'blob:desk/1', download: 'report.pdf', connected: true }]);
      expect(revoke).toHaveBeenCalledWith('blob:desk/1');
      expect(document.querySelector('a[download]')).toBeNull();
      expect(fetchMock).not.toHaveBeenCalled();
    } finally {
      Reflect.deleteProperty(URL, 'createObjectURL');
      Reflect.deleteProperty(URL, 'revokeObjectURL');
    }
  });

  it('asks the folder browser for a folder, one request at a time', async () => {
    const bridge = TestBed.inject(DeskBridge);
    const first = bridge.call('app.pickFolder', { purpose: 'source' });
    expect(bridge.folderRequest()).toEqual({ purpose: 'source' });
    const second = bridge.call('app.pickFolder', { purpose: 'skill-import' });
    await expect(first).resolves.toBeNull();
    expect(bridge.folderRequest()).toEqual({ purpose: 'skill-import' });
    bridge.answerFolder('/Users/me/code/app');
    await expect(second).resolves.toBe('/Users/me/code/app');
    expect(bridge.folderRequest()).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe('DeskBridge over /push', () => {
  it('listens on one socket that signs in with its first frame', () => {
    vi.stubGlobal('Notification', { permission: 'granted' });
    const bridge = TestBed.inject(DeskBridge);
    const globals: unknown[] = [];
    const events: unknown[] = [];
    const off = bridge.onPush('desk:global', (s) => globals.push(s));
    bridge.onPush('desk:event', (e) => events.push(e));
    expect(FakeSocket.instances).toHaveLength(1);
    const ws = lastSocket();
    expect(ws.url).toBe(`ws://${location.host}/push`);
    expect(bridge.pushStatus()).toBe('connecting');
    ws.open();
    expect(ws.sent).toEqual([{ session: 's3cret' }]);
    ws.receive({ channel: 'desk:global', payload: { n: 1 } });
    expect(bridge.pushStatus()).toBe('live');
    expect(ws.sent).toEqual([{ session: 's3cret' }, { notifyPermission: 'granted' }]);
    ws.receive({ channel: 'desk:event', payload: { id: 1 } });
    off();
    ws.receive({ channel: 'desk:global', payload: { n: 2 } });
    expect(globals).toEqual([{ n: 1 }]);
    expect(events).toEqual([{ id: 1 }]);
    bridge.setNotifyPermission('denied');
    expect(ws.sent.at(-1)).toEqual({ notifyPermission: 'denied' });
  });

  it('runs broker.watch and broker.unwatch on the socket and settles them on their ack', async () => {
    const bridge = TestBed.inject(DeskBridge);
    const watched = bridge.call('broker.watch', { projectId: 'p', afterSeq: 0 });
    const ws = lastSocket();
    ws.open();
    expect(ws.sent).toEqual([{ session: 's3cret' }]);
    ws.receive({ channel: 'desk:global', payload: {} });
    expect(ws.sent.slice(1)).toEqual([{ notifyPermission: 'denied' }, { op: 'broker.watch', id: 0, input: { projectId: 'p', afterSeq: 0 } }]);
    ws.receive({ ack: 0, result: { ok: true, value: { ok: true } } });
    await expect(watched).resolves.toEqual({ ok: true });
    const unwatched = bridge.call('broker.unwatch', { projectId: 'p' });
    expect(ws.sent.at(-1)).toEqual({ op: 'broker.unwatch', id: 1, input: { projectId: 'p' } });
    ws.receive({ ack: 1, result: { ok: false, error: { code: 'invalid_request', message: 'Bad input.' } } });
    await expect(unwatched).rejects.toMatchObject({ name: 'DeskCallError', code: 'invalid_request' });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('reconnects with backoff as a new sender, resending what was not acknowledged', async () => {
    vi.useFakeTimers();
    const bridge = TestBed.inject(DeskBridge);
    const reconnects = vi.fn();
    bridge.onReconnect(reconnects);
    bridge.onPush('desk:global', () => {});
    const first = lastSocket();
    first.open();
    first.receive({ channel: 'desk:global', payload: {} });
    const pending = bridge.call('broker.watch', { projectId: 'p', afterSeq: 4 });
    first.drop(1006);
    expect(bridge.pushStatus()).toBe('reconnecting');
    vi.advanceTimersByTime(499);
    expect(FakeSocket.instances).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(FakeSocket.instances).toHaveLength(2);
    const second = lastSocket();
    second.open();
    second.receive({ channel: 'desk:global', payload: {} });
    expect(second.sent).toEqual([{ session: 's3cret' }, { notifyPermission: 'denied' }, { op: 'broker.watch', id: 0, input: { projectId: 'p', afterSeq: 4 } }]);
    expect(reconnects).toHaveBeenCalledTimes(1);
    expect(bridge.pushStatus()).toBe('live');
    second.receive({ ack: 0, result: { ok: true, value: { ok: true } } });
    await expect(pending).resolves.toEqual({ ok: true });
  });

  it('waits 0.5, 1, 2 and 5 seconds before each new try, then 10 seconds each time, and starts over once signed in', () => {
    vi.useFakeTimers();
    const bridge = TestBed.inject(DeskBridge);
    bridge.onPush('desk:global', () => {});
    const waits = (delays: number[]) => {
      for (const delay of delays) {
        lastSocket().drop(1006);
        const sockets = FakeSocket.instances.length;
        vi.advanceTimersByTime(delay - 1);
        expect(FakeSocket.instances, `before ${delay} ms`).toHaveLength(sockets);
        vi.advanceTimersByTime(1);
        expect(FakeSocket.instances, `at ${delay} ms`).toHaveLength(sockets + 1);
      }
    };
    waits([500, 1000, 2000, 5000, 10_000, 10_000, 10_000]);
    expect(bridge.pushStatus()).toBe('reconnecting');
    lastSocket().open();
    lastSocket().receive({ channel: 'desk:global', payload: {} });
    expect(bridge.pushStatus()).toBe('live');
    waits([500, 1000]);
  });

  it('adopts a newer secret another tab stored when /push refuses the session', () => {
    const bridge = TestBed.inject(DeskBridge);
    bridge.onPush('desk:global', () => {});
    const first = lastSocket();
    first.open();
    localStorage.setItem('desk.session', 'fresh');
    first.drop(4401);
    expect(bridge.signedOut()).toBe(false);
    expect(localStorage.getItem('desk.session')).toBe('fresh');
    expect(FakeSocket.instances).toHaveLength(2);
    lastSocket().open();
    expect(lastSocket().sent).toEqual([{ session: 'fresh' }]);
  });

  it('signs out when /push refuses the session', async () => {
    const bridge = TestBed.inject(DeskBridge);
    const watched = bridge.call('broker.watch', { projectId: 'p', afterSeq: 0 });
    const ws = lastSocket();
    ws.open();
    ws.drop(4401);
    expect(bridge.signedOut()).toBe(true);
    expect(bridge.pushStatus()).toBe('idle');
    expect(localStorage.getItem('desk.session')).toBeNull();
    await expect(watched).rejects.toMatchObject({ code: 'unauthorized' });
    expect(FakeSocket.instances).toHaveLength(1);
  });
});
