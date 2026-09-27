// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { digestDef } from '@desk/ui-core/testing';
import { installReactFlowShims } from '../../test/reactflow';
import { GraphCanvas } from './GraphCanvas';

beforeAll(installReactFlowShims);
afterEach(cleanup);

describe('GraphCanvas', () => {
  it('draws the Start pill, each step (kind, title, detail, problems) and route stubs, and reports clicks as selections', async () => {
    const onSelect = vi.fn();
    render(
      <GraphCanvas
        def={digestDef()}
        layout={{}}
        startLabel="Mondays 08:00 · or Run now"
        selection={{ kind: 'none' }}
        onSelect={onSelect}
        issues={{ steps: { ok: ['question: required'] }, edges: {}, start: [] }}
        editable
      />,
    );
    const fetch = await screen.findByTestId('node-fetch');
    expect(fetch.textContent).toContain('script');
    expect(fetch.textContent).toContain('Fetch pages');
    expect(fetch.textContent).toContain('digest: fetch.py');
    expect(screen.getByTestId('node-ok').className).toContain('invalid');
    expect(screen.getByTestId('node-start').textContent).toContain('Mondays 08:00 · or Run now');
    expect(screen.getByText('unchanged · ends')).toBeTruthy();
    fireEvent.click(screen.getByTestId('node-sum'));
    expect(onSelect).toHaveBeenLastCalledWith({ kind: 'step', id: 'sum' });
    fireEvent.click(screen.getByTestId('node-start'));
    expect(onSelect).toHaveBeenLastCalledWith({ kind: 'start' });
  });

  it('lights steps with their run state', async () => {
    render(
      <GraphCanvas
        def={digestDef()}
        layout={{}}
        startLabel="schedule"
        selection={{ kind: 'none' }}
        onSelect={() => {}}
        run={{ steps: { fetch: { tone: 'ok', badge: '✓ 12s', detail: 'route changed' }, sum: { tone: 'run', badge: '● 5m', detail: 'reading acme.md' } }, fired: new Set([0]) }}
        editable={false}
      />,
    );
    const sum = await screen.findByTestId('node-sum');
    expect(sum.className).toContain('run-run');
    expect(sum.textContent).toContain('● 5m');
    expect(sum.textContent).toContain('reading acme.md');
    expect(screen.getByTestId('node-fetch').className).toContain('run-ok');
  });
});
