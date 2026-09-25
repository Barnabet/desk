import { SESSION_STORAGE_KEY } from './frames';

export const escapeHtml = (s: string): string => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

const STYLE = '<style>body{font:16px/1.5 system-ui,sans-serif;margin:4rem auto;max-width:36rem;padding:0 1rem;color:#2b2a27;background:#efeae0}</style>';

/** The /login answer: the new session secret in a <meta> tag, stored by the external /login.js (the CSP forbids inline scripts). */
export function loginPage(secret: string): string {
  return `<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><meta name="desk-session" content="${escapeHtml(secret)}"><title>Desk</title><script src="/login.js"></script></head><body></body></html>\n`;
}

export function loginFailedPage(): string {
  return `<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><title>Desk</title>${STYLE}</head><body><main><h1>This login link has expired or was already used.</h1><p>Press Enter in the terminal where <code>desk web</code> is running to get a new link.</p></main></body></html>\n`;
}

/** Stores the session secret for this origin, then opens the app; `replace` keeps the code out of the history. */
export const LOGIN_JS = `(() => {
  const meta = document.querySelector('meta[name="desk-session"]');
  try {
    if (meta) localStorage.setItem(${JSON.stringify(SESSION_STORAGE_KEY)}, meta.getAttribute('content') || '');
  } catch {}
  location.replace('/');
})();
`;

/** The page desk web opens instead of the link, so the one-time code never appears on a command line. */
export function loginRedirectHtml(link: string): string {
  const href = escapeHtml(link);
  return `<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><meta http-equiv="refresh" content="0;url=${href}"><title>Desk</title></head><body><a href="${href}">Open Desk</a></body></html>\n`;
}
