import { ModelError } from '../model/errors';

/**
 * Global pause for model calls while the proxy is unreachable. Agents that hit `proxy_down` wait here;
 * one probe loop checks health and releases everyone when the proxy answers again.
 */
export class ProxyGate {
  private down = false;
  private waiters: Array<() => void> = [];
  private timer: NodeJS.Timeout | undefined;
  private readonly affected = new Set<string>();

  constructor(
    private readonly probe: () => Promise<boolean>,
    private readonly intervalMs: number,
    private readonly notify: (projectId: string, up: boolean) => void,
  ) {}

  get isDown(): boolean {
    return this.down;
  }

  /** Records that `projectId` hit an outage; the first report starts the probe loop. */
  markDown(projectId: string): void {
    if (!this.affected.has(projectId)) {
      this.affected.add(projectId);
      this.notify(projectId, false);
    }
    if (this.down) return;
    this.down = true;
    this.scheduleProbe();
  }

  waitUntilUp(signal: AbortSignal): Promise<void> {
    if (!this.down) return Promise.resolve();
    return new Promise((resolve, reject) => {
      const onAbort = () => {
        this.waiters = this.waiters.filter((w) => w !== release);
        reject(new ModelError('aborted', 'Aborted while waiting for the model proxy'));
      };
      const release = () => {
        signal.removeEventListener('abort', onAbort);
        resolve();
      };
      if (signal.aborted) return onAbort();
      signal.addEventListener('abort', onAbort, { once: true });
      this.waiters.push(release);
    });
  }

  close(): void {
    if (this.timer) clearTimeout(this.timer);
  }

  private scheduleProbe(): void {
    this.timer = setTimeout(async () => {
      const ok = await this.probe().catch(() => false);
      if (!ok) return this.scheduleProbe();
      this.down = false;
      for (const projectId of this.affected) this.notify(projectId, true);
      this.affected.clear();
      const waiters = this.waiters;
      this.waiters = [];
      for (const w of waiters) w();
    }, this.intervalMs);
    this.timer.unref();
  }
}
