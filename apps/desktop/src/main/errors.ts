/** An error whose message is safe and useful to show in the UI. */
export class UserFacingError extends Error {
  constructor(
    readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = 'UserFacingError';
  }
}
