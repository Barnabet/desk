import { fireEvent, render, screen } from '@testing-library/angular';
import { describe, expect, it, vi } from 'vitest';
import type { AutomationLayout } from '@desk/protocol';
import { autoLayout, edgeId, targetAnchor, toGraph, type GraphSelection } from '@desk/ui-core';
import { digestDef } from '@desk/ui-core/testing';
import { GraphCanvas } from './graph-canvas';

type Inputs = { selection?: GraphSelection; editable?: boolean; layout?: AutomationLayout };

async function canvas(o: Inputs = {}) {
  const outputs = { pick: vi.fn(), moved: vi.fn(), connect: vi.fn(), remove: vi.fn() };
  const layout = o.layout ?? autoLayout(digestDef());
  const view = await render(GraphCanvas, {
    inputs: { def: digestDef(), layout, startLabel: 'Mondays 08:00 · or Run now', selection: o.selection ?? { kind: 'none' }, editable: o.editable ?? true },
    on: outputs,
  });
  await view.fixture.whenStable();
  return { ...outputs, view, layout, flow: view.container.querySelector<HTMLElement>('.desk-flow')! };
}

/** The viewport's translate and scale, as the canvas renders them. */
function viewportOf(root: ParentNode): { x: number; y: number; zoom: number } {
  const t = root.querySelector<HTMLElement>('.react-flow__viewport')!.style.transform;
  const m = /translate\((-?[\d.]+)px, (-?[\d.]+)px\) scale\(([\d.]+)\)/.exec(t);
  if (!m) throw new Error(`No viewport transform in "${t}"`);
  return { x: Number(m[1]), y: Number(m[2]), zoom: Number(m[3]) };
}

describe('GraphCanvas', () => {
  it('draws the Start pill, each step (kind, title, detail, problems) and route stubs, and reports clicks as selections', async () => {
    const outputs = { pick: vi.fn() };
    const view = await render(GraphCanvas, {
      inputs: { def: digestDef(), layout: {}, startLabel: 'Mondays 08:00 · or Run now', selection: { kind: 'none' }, issues: { steps: { ok: ['question: required'] }, edges: {}, start: [] }, editable: true },
      on: outputs,
    });
    await view.fixture.whenStable();
    const fetch = await screen.findByTestId('node-fetch');
    expect(fetch.textContent).toContain('script');
    expect(fetch.textContent).toContain('Fetch pages');
    expect(fetch.textContent).toContain('digest: fetch.py');
    expect(screen.getByTestId('node-ok').className).toContain('invalid');
    expect(screen.getByTestId('node-start').textContent).toContain('Mondays 08:00 · or Run now');
    expect(screen.getByText('unchanged · ends')).toBeTruthy();
    fireEvent.click(screen.getByTestId('node-sum'));
    expect(outputs.pick).toHaveBeenLastCalledWith({ kind: 'step', id: 'sum' });
    fireEvent.click(screen.getByTestId('node-start'));
    expect(outputs.pick).toHaveBeenLastCalledWith({ kind: 'start' });
  });

  it('lights steps with their run state', async () => {
    const view = await render(GraphCanvas, {
      inputs: {
        def: digestDef(),
        layout: {},
        startLabel: 'schedule',
        selection: { kind: 'none' },
        run: { steps: { fetch: { tone: 'ok', badge: '✓ 12s', detail: 'route changed' }, sum: { tone: 'run', badge: '● 5m', detail: 'reading acme.md' } }, fired: new Set([0]) },
        editable: false,
      },
    });
    await view.fixture.whenStable();
    const sum = await screen.findByTestId('node-sum');
    expect(sum.className).toContain('run-run');
    expect(sum.textContent).toContain('● 5m');
    expect(sum.textContent).toContain('reading acme.md');
    expect(screen.getByTestId('node-fetch').className).toContain('run-ok');
    expect(view.container.querySelector(`[data-id="${edgeId(0)}"] .auto-edge`)!.getAttribute('class')).toContain('look-fired');
  });

  it('selects an edge and the pane by clicking, and marks the selection', async () => {
    const c = await canvas({ selection: { kind: 'edge', index: 1 } });
    expect(c.view.container.querySelector(`[data-id="${edgeId(1)}"] .auto-edge`)!.getAttribute('class')).toContain('selected');
    fireEvent.click(c.view.container.querySelector(`[data-id="${edgeId(0)}"]`)!);
    expect(c.pick).toHaveBeenLastCalledWith({ kind: 'edge', index: 0 });
    expect(c.view.container.querySelector('.auto-edge-label')!.textContent).toBe('changed');
    fireEvent.click(c.view.container.querySelector('.react-flow__pane')!);
    expect(c.pick).toHaveBeenLastCalledWith({ kind: 'none' });
  });

  it('drags a step in edit mode, reports its rounded position, and swallows the click that ends the drag', async () => {
    const c = await canvas();
    const v = viewportOf(c.view.container);
    const node = screen.getByTestId('node-fetch');
    fireEvent.pointerDown(node, { button: 0, clientX: 100, clientY: 100 });
    fireEvent.pointerMove(window, { clientX: 150, clientY: 130 });
    fireEvent.pointerUp(window, { clientX: 150, clientY: 130 });
    const from = c.layout['fetch']!;
    expect(c.moved).toHaveBeenCalledWith({ fetch: { x: Math.round(from.x + 50 / v.zoom), y: Math.round(from.y + 30 / v.zoom) } });
    fireEvent.click(node);
    expect(c.pick).not.toHaveBeenCalled();
  });

  it('pans instead of dragging when it is read-only', async () => {
    const c = await canvas({ editable: false });
    const before = viewportOf(c.view.container);
    fireEvent.pointerDown(screen.getByTestId('node-fetch'), { button: 0, clientX: 100, clientY: 100 });
    fireEvent.pointerMove(window, { clientX: 140, clientY: 90 });
    fireEvent.pointerUp(window, { clientX: 140, clientY: 90 });
    expect(c.moved).not.toHaveBeenCalled();
    expect(viewportOf(c.view.container)).toEqual({ ...before, x: before.x + 40, y: before.y - 10 });
  });

  it('connects a step’s bottom handle to another step’s top handle, and only where the connection may go', async () => {
    const c = await canvas();
    const byId = new Map(toGraph(digestDef(), c.layout, { startLabel: 's', selection: { kind: 'none' }, editable: true }).nodes.map((n) => [n.id, n]));
    const v = viewportOf(c.view.container);
    const screenOf = (id: string) => {
      const a = targetAnchor(byId.get(id)!);
      return { clientX: a.x * v.zoom + v.x + 3, clientY: a.y * v.zoom + v.y };
    };
    const fetchOut = screen.getByTestId('node-fetch').querySelector('.react-flow__handle-bottom')!;
    fireEvent.pointerDown(fetchOut, { button: 0, clientX: 0, clientY: 0 });
    fireEvent.pointerMove(window, screenOf('ok'));
    expect(screen.getByTestId('node-ok').querySelector('.react-flow__handle-top')!.className).toContain('valid');
    expect(c.view.container.querySelector('.react-flow__connection-path')).toBeTruthy();
    fireEvent.pointerUp(window, screenOf('ok'));
    expect(c.connect).toHaveBeenCalledWith({ from: 'fetch', to: 'ok' });
    expect(c.view.container.querySelector('.react-flow__connection-path')).toBeNull();

    const okOut = screen.getByTestId('node-ok').querySelector('.react-flow__handle-bottom')!;
    fireEvent.pointerDown(okOut, { button: 0, clientX: 0, clientY: 0 });
    fireEvent.pointerMove(window, screenOf('fetch'));
    expect(screen.getByTestId('node-fetch').querySelector('.react-flow__handle-top')!.className).not.toContain('valid');
    fireEvent.pointerUp(window, screenOf('fetch'));
    expect(c.connect).toHaveBeenCalledTimes(1);
  });

  it('deletes the selected step or edge with Delete or Backspace, only in edit mode', async () => {
    const c = await canvas({ selection: { kind: 'step', id: 'sum' } });
    // rerender unsubscribes every output its `on` leaves out, so it passes them again.
    const on = { pick: c.pick, moved: c.moved, connect: c.connect, remove: c.remove };
    fireEvent.keyDown(c.flow, { key: 'Delete' });
    expect(c.remove).toHaveBeenLastCalledWith({ steps: ['sum'], edges: [] });
    await c.view.rerender({ inputs: { selection: { kind: 'edge', index: 0 } }, on, partialUpdate: true });
    fireEvent.keyDown(c.flow, { key: 'Backspace' });
    expect(c.remove).toHaveBeenLastCalledWith({ steps: [], edges: [0] });
    await c.view.rerender({ inputs: { editable: false }, on, partialUpdate: true });
    fireEvent.keyDown(c.flow, { key: 'Delete' });
    expect(c.remove).toHaveBeenCalledTimes(2);
  });

  it('zooms with the controls and the wheel, and fits the view again', async () => {
    const c = await canvas();
    const fitted = viewportOf(c.view.container);
    fireEvent.click(screen.getByRole('button', { name: 'Zoom Out' }));
    expect(viewportOf(c.view.container).zoom).toBeCloseTo(fitted.zoom / 1.2);
    fireEvent.wheel(c.flow, { deltaY: -100, clientX: 10, clientY: 10 });
    expect(viewportOf(c.view.container).zoom).toBeCloseTo((fitted.zoom / 1.2) * 2 ** 0.2);
    fireEvent.click(screen.getByRole('button', { name: 'Fit View' }));
    expect(viewportOf(c.view.container)).toEqual(fitted);
    fireEvent.click(screen.getByRole('button', { name: 'Zoom In' }));
    expect(viewportOf(c.view.container).zoom).toBeCloseTo(Math.min(1.5, fitted.zoom * 1.2));
  });
});
