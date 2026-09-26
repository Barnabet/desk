export type DeskErrorCode = 'not_found' | 'conflict' | 'invalid';

/** Errors with a stable code that API layers map to HTTP statuses; `details` goes into the error body. */
export class DeskError extends Error {
  constructor(
    readonly code: DeskErrorCode,
    message: string,
    readonly details?: unknown,
  ) {
    super(message);
    this.name = new.target.name;
  }
}

export class NotFoundError extends DeskError {
  constructor(message: string, details?: unknown) {
    super('not_found', message, details);
  }
}

/** The request is valid but conflicts with current state (already resolved, still running, archived…). */
export class ConflictError extends DeskError {
  constructor(message: string, details?: unknown) {
    super('conflict', message, details);
  }
}

export class ValidationError extends DeskError {
  constructor(message: string, details?: unknown) {
    super('invalid', message, details);
  }
}
