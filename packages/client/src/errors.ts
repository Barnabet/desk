/** An error response from deskd: `{ error: { code, message, details? } }`. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details?: unknown,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

/** deskd did not answer (not started, crashed, or wrong port). */
export class DaemonUnavailable extends Error {
  constructor(readonly baseUrl: string) {
    super(`Cannot reach deskd at ${baseUrl}`);
    this.name = 'DaemonUnavailable';
  }
}

/** No daemon.json: deskd is not running for this data directory. */
export class DaemonNotRunning extends Error {
  constructor(readonly dataDir: string) {
    super(`deskd is not running (no daemon.json in ${dataDir}). Start it with: desk up`);
    this.name = 'DaemonNotRunning';
  }
}

/** The daemon speaks a different protocol version than this client. */
export class ProtocolMismatch extends Error {
  constructor(
    readonly daemon: number,
    readonly client: number,
  ) {
    super(`deskd speaks protocol ${daemon}, this client speaks protocol ${client}`);
    this.name = 'ProtocolMismatch';
  }
}
