import { render, screen } from '@testing-library/angular';
import { describe, expect, it } from 'vitest';
import type { AttentionItem } from '@desk/protocol';
import type { WaitHop } from '@desk/ui-core';
import { HopLink } from './hop-link';

const approval: AttentionItem = {
  id: 'approval:x1',
  kind: 'approval',
  project_id: 'p',
  project_name: 'Onboarding revamp',
  agent_id: 'f',
  title: 'Frontend wants to run bash',
  detail: '',
  created_at: '2026-09-25T10:00:00.000Z',
  ref: { approval_id: 'x1', thread_id: 'f' },
};

describe('HopLink', () => {
  it('links one hop past a wait to the attention item that needs you', async () => {
    const hop: WaitHop = { text: '→ needs your approval', label: 'Frontend needs your approval', item: approval };
    await render(`<a deskHopLink [hop]="hop"></a>`, { imports: [HopLink], componentProperties: { hop } });
    const link = screen.getByRole('link', { name: 'Frontend needs your approval' });
    expect(link.getAttribute('href')).toBe('#/attention?item=approval%3Ax1');
    expect(link.className).toBe('wait-hop');
    expect(link.textContent).toBe('→ needs your approval');
    expect(link.querySelector('.hop-dot')!.getAttribute('aria-hidden')).toBe('true');
  });
});
