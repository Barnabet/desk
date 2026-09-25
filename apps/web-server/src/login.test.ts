import { describe, expect, it } from 'vitest';
import { escapeHtml, LOGIN_JS, loginFailedPage, loginPage, loginRedirectHtml } from './login';

describe('login pages', () => {
  it('carry the secret in a meta tag and load only the external /login.js', () => {
    const html = loginPage('S3cret_-x');
    expect(html).toContain('<meta name="desk-session" content="S3cret_-x">');
    expect(html).toContain('<script src="/login.js"></script>');
    expect(html.match(/<script/g)).toHaveLength(1);
    expect(loginPage('"><b>')).toContain('content="&quot;&gt;&lt;b&gt;"');
  });

  it('store the secret for this origin, then open the app with the code gone from the history', () => {
    expect(LOGIN_JS).toContain('localStorage.setItem("desk.session"');
    expect(LOGIN_JS).toContain("location.replace('/')");
  });

  it('say how to get a new link when one is refused', () => {
    expect(loginFailedPage()).toContain('This login link has expired or was already used.');
    expect(loginFailedPage()).toContain('Press Enter in the terminal where <code>desk web</code> is running to get a new link.');
    expect(loginFailedPage()).not.toContain('<script');
  });

  it('escape the link in the redirect page', () => {
    expect(loginRedirectHtml('http://127.0.0.1:7434/login?code=a&b')).toContain('content="0;url=http://127.0.0.1:7434/login?code=a&amp;b"');
    expect(escapeHtml('<a href="x">&</a>')).toBe('&lt;a href=&quot;x&quot;&gt;&amp;&lt;/a&gt;');
  });
});
