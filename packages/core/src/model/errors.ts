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

const TOO_LARGE = /too large|exceeds the maximum (request )?size|maximum (request|payload|body) size/i;
/**
 * "image" as a word ("Could not process image", "image MIME type"), or the `image_url` content part; not inside
 * another name such as the view_image tool or an `image_count` field.
 */
const IMAGE = /(?<![\w-])images?(?![\w-])|(?<![\w-])image_url(?![\w-])/i;

/**
 * Whether the endpoint refused a request because of the images in it: an image it cannot decode or take
 * (`image`, e.g. "400 Could not process image"), or a body over its size limit (`too_large`, 413). Null otherwise.
 * A wrong guess costs retries only: the agent loop records nothing until a retry goes through.
 */
export function imageRefusal(err: ModelError): 'image' | 'too_large' | null {
  if (err.kind !== 'fatal') return null;
  if (err.status === 413) return 'too_large';
  if (err.status !== 400 && err.status !== 422) return null;
  if (IMAGE.test(err.message)) return 'image';
  return TOO_LARGE.test(err.message) ? 'too_large' : null;
}
