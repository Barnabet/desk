import { fireEvent, render, screen, waitFor } from '@testing-library/angular';
import { describe, expect, it } from 'vitest';
import { FakeDeskBridge } from '../testing/fake-bridge';
import { SafeMarkdown } from './safe-markdown';

const renderMarkdown = (text: string, bridge = new FakeDeskBridge()) => render(SafeMarkdown, { inputs: { text }, providers: bridge.providers });

describe('SafeMarkdown', () => {
  it('renders markdown and GFM', async () => {
    const { container } = await renderMarkdown('**Five drafts** ready\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n- [x] done');
    expect(container.querySelector('strong')?.textContent).toBe('Five drafts');
    expect(container.querySelector('table')).not.toBeNull();
  });

  it('puts a task checkbox inside the item\'s paragraph in a loose list, and straight in the item in a tight one', async () => {
    const { container } = await renderMarkdown('- [x] a\n\n- [ ] b\n\nthen\n\n- [x] tight');
    const [loose, tight] = [...container.querySelectorAll('ul.contains-task-list')];
    const looseItems = [...loose!.querySelectorAll(':scope > li.task-list-item')];
    expect(looseItems.map((li) => li.firstElementChild?.tagName)).toEqual(['P', 'P']);
    expect(looseItems.map((li) => li.querySelector(':scope > p > input[type=checkbox][disabled]') !== null)).toEqual([true, true]);
    expect(looseItems.map((li) => li.querySelector(':scope > input'))).toEqual([null, null]);
    expect(looseItems.map((li) => (li.querySelector('input') as HTMLInputElement).checked)).toEqual([true, false]);
    expect(looseItems.map((li) => li.querySelector('p')?.textContent)).toEqual([' a', ' b']);
    const tightItem = tight!.querySelector(':scope > li.task-list-item')!;
    expect(tightItem.querySelector('p')).toBeNull();
    expect(tightItem.firstElementChild?.tagName).toBe('INPUT');
    expect(tightItem.textContent).toBe(' tight');
  });

  it('marks an ordered list with task items as a task list too', async () => {
    const { container } = await renderMarkdown('1. [x] ordered\n2. [ ] two');
    const list = container.querySelector('ol')!;
    expect(list.classList.contains('contains-task-list')).toBe(true);
    expect([...list.querySelectorAll(':scope > li.task-list-item')].map((li) => li.textContent)).toEqual([' ordered', ' two']);
  });

  it('never renders raw HTML, scripts or remote images', async () => {
    const { container } = await renderMarkdown('<script>alert(1)</script><img src=x onerror=alert(1)>\n\n![chart](https://evil.test/p.png)\n\n<b>bold?</b>');
    expect(container.querySelector('script')).toBeNull();
    expect(container.querySelector('img')).toBeNull();
    expect(container.querySelector('b')).toBeNull();
    expect(screen.getByRole('button', { name: 'Image: chart' })).toBeTruthy();
  });

  it('asks before opening a link, and only opens web links', async () => {
    const bridge = new FakeDeskBridge({ 'app.openExternal': () => ({ ok: true }) });
    await renderMarkdown('See [the doc](https://example.com/doc) or [this](javascript:alert(1))', bridge);
    expect(screen.queryByRole('button', { name: 'this' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'the doc' }));
    expect(screen.getByRole('dialog').textContent).toContain('https://example.com/doc');
    expect(bridge.calls).toEqual([]);
    fireEvent.click(screen.getByRole('button', { name: 'Open in browser' }));
    await waitFor(() => expect(bridge.calls).toEqual([{ channel: 'app.openExternal', input: { url: 'https://example.com/doc' } }]));
  });

  it('renders fenced code as a code block', async () => {
    const { container } = await renderMarkdown('```bash\ncurl -fsSL https://bun.sh/install | bash\n```');
    expect(container.querySelector('.codeblock pre code')?.textContent).toBe('curl -fsSL https://bun.sh/install | bash');
    expect(container.querySelector('.codeblock-lang')?.textContent).toBe('bash');
  });

  it('leaves links and images of other schemes inert, an image as its alt text alone', async () => {
    const { container } = await renderMarkdown('<javascript:alert(1)> [x](data:text/html,hi) ![logo](javascript:alert(1)) [rel](/etc/passwd) ![map](/map.png)');
    expect(screen.queryAllByRole('button')).toEqual([]);
    expect(container.querySelector('a')).toBeNull();
    expect([...container.querySelectorAll('.md-link-disabled')].map((e) => e.textContent)).toEqual(['javascript:alert(1)', 'x', 'rel', 'Image: map']);
    // react-markdown's defaultUrlTransform empties the javascript: source, and the desktop then shows the alt in a plain span.
    expect([...container.querySelectorAll('p > span:not(.md-link-disabled)')].map((e) => [e.attributes.length, e.textContent])).toEqual([[0, 'logo']]);
  });

  it('shows markup in agent text as text', async () => {
    const { container } = await renderMarkdown('&lt;img src=x onerror=alert(1)&gt; and `<b>code</b>` and <svg onload=alert(1)>');
    expect(container.querySelector('img, b, svg')).toBeNull();
    expect(container.textContent).toContain('<img src=x onerror=alert(1)> and <b>code</b> and');
    expect(container.querySelector('code.md-code')?.textContent).toBe('<b>code</b>');
    expect(container.classList.contains('md')).toBe(true);
  });
});
