import OpenAI from 'openai';

export type ModelErrorKind = 'rate_limited' | 'transient' | 'proxy_down' | 'context_overflow' | 'aborted' | 'fatal';

export class ModelError extends Error {
  constructor(
    readonly kind: ModelErrorKind,
    message: string,
    readonly status?: number,
    options?: { cause?: unknown },
  ) {
    super(message, options);
    this.name = 'ModelError';
  }
}

function findErrorCode(err: unknown): string | undefined {
  let current: unknown = err;
  for (let depth = 0; depth < 6 && current; depth++) {
    const code = (current as { code?: unknown }).code;
    if (typeof code === 'string') return code;
    current = (current as { cause?: unknown }).cause;
  }
  return undefined;
}

const CONTEXT_OVERFLOW = /prompt is too long|context (length|window)|maximum context|too many tokens/i;

export function classifyModelError(err: unknown): ModelError {
  if (err instanceof ModelError) return err;
  if (err instanceof OpenAI.APIUserAbortError || (err instanceof Error && err.name === 'AbortError')) {
    return new ModelError('aborted', 'Request aborted', undefined, { cause: err });
  }
  if (err instanceof OpenAI.APIConnectionTimeoutError) {
    return new ModelError('transient', err.message, undefined, { cause: err });
  }
  if (err instanceof OpenAI.APIConnectionError) {
    const kind = findErrorCode(err) === 'ECONNREFUSED' ? 'proxy_down' : 'transient';
    return new ModelError(kind, err.message, undefined, { cause: err });
  }
  if (err instanceof OpenAI.APIError) {
    const status = err.status;
    const message = err.message ?? 'Model API error';
    const type = (err as { type?: unknown }).type ?? (err.error as { type?: unknown } | undefined)?.type;
    if (status === 429 || type === 'rate_limit_error') return new ModelError('rate_limited', message, status, { cause: err });
    if (status === 400 && CONTEXT_OVERFLOW.test(message)) return new ModelError('context_overflow', message, status, { cause: err });
    if (status === 408 || status === 409 || (status !== undefined && status >= 500)) {
      return new ModelError('transient', message, status, { cause: err });
    }
    return new ModelError('fatal', message, status, { cause: err });
  }
  return new ModelError('fatal', err instanceof Error ? err.message : String(err), undefined, { cause: err });
}
