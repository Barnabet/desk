import { fireEvent, render, screen } from '@testing-library/angular';
import { describe, expect, it, vi } from 'vitest';
import { CodeBlock } from './code-block';

describe('CodeBlock', () => {
  it('shows the language and the code, and copies it', async () => {
    const writeText = vi.fn(async () => {});
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
    try {
      const { container } = await render(CodeBlock, { inputs: { code: 'pnpm test', language: 'bash' } });
      expect(container.querySelector('.codeblock-bar .codeblock-lang')?.textContent).toBe('bash');
      expect(container.querySelector('pre code')?.textContent).toBe('pnpm test');
      fireEvent.click(screen.getByRole('button', { name: 'Copy' }));
      expect(writeText).toHaveBeenCalledWith('pnpm test');
      expect(await screen.findByRole('button', { name: 'Copied' })).toBeTruthy();
    } finally {
      Reflect.deleteProperty(navigator, 'clipboard');
    }
  });
});
