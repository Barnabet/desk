import type { WebProvider } from '@desk/protocol';

/** A failed call to a paid web API. `status` is null when there was no HTTP answer (timeout, network). */
export class WebApiError extends Error {
  constructor(
    readonly provider: WebProvider,
    readonly status: number | null,
    message: string,
  ) {
    super(message);
    this.name = 'WebApiError';
  }

  /** A refused key or an empty account (401, 402, 403): the user has to act, so a notice is raised. */
  get keyProblem(): boolean {
    return this.status === 401 || this.status === 402 || this.status === 403;
  }
}

/** The provider's error text from a JSON body (`message`, `error`), else the status line; never longer than 300 characters. */
export async function errorDetail(res: Response): Promise<string> {
  const text = await res.text().catch(() => '');
  let detail = text.trim();
  try {
    const body = JSON.parse(text) as { message?: unknown; error?: unknown };
    const pick = typeof body.error === 'string' ? body.error : typeof body.message === 'string' ? body.message : undefined;
    if (pick) detail = pick;
  } catch {
    // Not JSON: keep the text.
  }
  const head = `HTTP ${res.status}${res.statusText ? ` ${res.statusText}` : ''}`;
  return detail ? `${head}: ${detail.slice(0, 300)}` : head;
}
