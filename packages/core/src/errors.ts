export type DeskErrorCode = 'not_found' | 'conflict' | 'invalid';

/** Errors with a stable code that API layers map to HTTP statuses. */
export class DeskError extends Error {
  constructor(
    readonly code: DeskErrorCode,
    message: string,
  ) {
    super(message);
    this.name = new.target.name;
  }
}

export class NotFoundError extends DeskError {
  constructor(message: string) {
    super('not_found', message);
  }
}

/** The request is valid but conflicts with current state (already resolved, still running, archived…). */
export class ConflictError extends DeskError {
  constructor(message: string) {
    super('conflict', message);
  }
}

export class ValidationError extends DeskError {
  constructor(message: string) {
    super('invalid', message);
  }
}
