// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { installBridge } from '../test/bridge';
import { SafeMarkdown } from './SafeMarkdown';

afterEach(cleanup);

describe('SafeMarkdown', () => {
  it('renders markdown and GFM', () => {
    const { container } = render(<SafeMarkdown text={'**Five drafts** ready\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n- [x] done'} />);
    expect(container.querySelector('strong')?.textContent).toBe('Five drafts');
    expect(container.querySelector('table')).not.toBeNull();
  });

  it('never renders raw HTML, scripts or remote images', () => {
    const { container } = render(<SafeMarkdown text={'<script>alert(1)</script><img src=x onerror=alert(1)>\n\n![chart](https://evil.test/p.png)\n\n<b>bold?</b>'} />);
    expect(container.querySelector('script')).toBeNull();
    expect(container.querySelector('img')).toBeNull();
    expect(container.querySelector('b')).toBeNull();
    expect(screen.getByRole('button', { name: 'Image: chart' })).toBeTruthy();
  });

  it('asks before opening a link, and only opens web links', async () => {
    const bridge = installBridge({ 'app.openExternal': () => ({ ok: true }) });
    render(<SafeMarkdown text={'See [the doc](https://example.com/doc) or [this](javascript:alert(1))'} />);
    expect(screen.queryByRole('button', { name: 'this' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'the doc' }));
    expect(screen.getByRole('dialog').textContent).toContain('https://example.com/doc');
    expect(bridge.calls).toEqual([]);
    fireEvent.click(screen.getByRole('button', { name: 'Open in browser' }));
    await waitFor(() => expect(bridge.calls).toEqual([{ channel: 'app.openExternal', input: { url: 'https://example.com/doc' } }]));
  });

  it('renders fenced code as a code block', () => {
    const { container } = render(<SafeMarkdown text={'```bash\ncurl -fsSL https://bun.sh/install | bash\n```'} />);
    expect(container.querySelector('.codeblock pre code')?.textContent).toBe('curl -fsSL https://bun.sh/install | bash');
    expect(container.querySelector('.codeblock-lang')?.textContent).toBe('bash');
  });
});
