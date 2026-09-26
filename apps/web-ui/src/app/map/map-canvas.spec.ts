import { createEvent, fireEvent, render, screen, waitFor } from '@testing-library/angular';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MapCanvas, type CanvasSize } from './map-canvas';

afterEach(() => {
  vi.restoreAllMocks();
});

async function setup() {
  const sizes: CanvasSize[] = [];
  await render(`<div deskMapCanvas label="Projects map" (resized)="sizes.push($event)"><button type="button">Inside</button></div>`, {
    imports: [MapCanvas],
    componentProperties: { sizes },
  });
  const canvas = screen.getByRole('group', { name: 'Projects map' });
  const layer = canvas.querySelector<HTMLElement>('.map-layer')!;
  return { canvas, layer, sizes, user: userEvent.setup() };
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('MapCanvas', () => {
  it('projects its content into the moving layer, beside the zoom controls', async () => {
    const { canvas, layer } = await setup();
    expect(canvas.className).toBe('map-canvas');
    expect(canvas.hasAttribute('label')).toBe(false);
    expect(layer.contains(screen.getByRole('button', { name: 'Inside' }))).toBe(true);
    expect(layer.style.transform).toBe('translate(0px, 0px) scale(1)');
    const zoom = screen.getByRole('group', { name: 'Zoom' });
    expect(zoom.className).toBe('map-zoom');
    expect(screen.getByRole('button', { name: 'Zoom in' }).textContent).toBe('+');
    expect(screen.getByRole('button', { name: 'Zoom out' }).textContent).toBe('−');
    expect(screen.queryByRole('button', { name: 'Reset' })).toBeNull();
  });

  it('zooms around the centre from its buttons, between 0.5 and 2.5, and resets', async () => {
    const { layer, sizes, user } = await setup();
    await user.click(screen.getByRole('button', { name: 'Zoom in' }));
    // jsdom lays nothing out: the canvas keeps 1000 × 700, so the centre is (500, 350), and reports no size.
    await waitFor(() => expect(layer.style.transform).toBe('translate(-100px, -70px) scale(1.2)'));
    expect(sizes).toEqual([]);
    for (let i = 0; i < 8; i++) await user.click(screen.getByRole('button', { name: 'Zoom in' }));
    await waitFor(() => expect(layer.style.transform).toMatch(/ scale\(2\.5\)$/));
    await user.click(screen.getByRole('button', { name: 'Reset' }));
    await waitFor(() => expect(layer.style.transform).toBe('translate(0px, 0px) scale(1)'));
    expect(screen.queryByRole('button', { name: 'Reset' })).toBeNull();
    for (let i = 0; i < 6; i++) await user.click(screen.getByRole('button', { name: 'Zoom out' }));
    await waitFor(() => expect(layer.style.transform).toMatch(/ scale\(0\.5\)$/));
    expect(screen.getByRole('button', { name: 'Reset' })).toBeTruthy();
  });

  it('pans with the wheel, and zooms at the pointer with ⌘ or a pinch without zooming the page', async () => {
    const { canvas, layer } = await setup();
    const pan = createEvent.wheel(canvas, { deltaX: 10, deltaY: 20 });
    canvas.dispatchEvent(pan);
    expect(pan.defaultPrevented).toBe(false);
    await waitFor(() => expect(layer.style.transform).toBe('translate(-10px, -20px) scale(1)'));
    expect(await screen.findByRole('button', { name: 'Reset' })).toBeTruthy();
    // A pinch arrives as a wheel event with ctrlKey; jsdom's rect is at (0, 0), and so is the pointer.
    const pinch = createEvent.wheel(canvas, { deltaY: -100, ctrlKey: true });
    canvas.dispatchEvent(pinch);
    expect(pinch.defaultPrevented).toBe(true);
    await waitFor(() => expect(layer.style.transform).toBe('translate(-25px, -50px) scale(2.5)'));
    const command = createEvent.wheel(canvas, { deltaY: 100, metaKey: true });
    canvas.dispatchEvent(command);
    expect(command.defaultPrevented).toBe(true);
  });

  it('drags from the background, never from a link or a button', async () => {
    const { canvas, layer } = await setup();
    fireEvent.pointerDown(screen.getByRole('button', { name: 'Inside' }), { clientX: 5, clientY: 5, pointerId: 1 });
    fireEvent.pointerMove(canvas, { clientX: 200, clientY: 200, pointerId: 1 });
    await tick();
    expect(layer.style.transform).toBe('translate(0px, 0px) scale(1)');
    fireEvent.pointerUp(canvas, { pointerId: 1 });
    fireEvent.pointerDown(canvas, { clientX: 10, clientY: 10, pointerId: 1 });
    fireEvent.pointerMove(canvas, { clientX: 40, clientY: 30, pointerId: 1 });
    await waitFor(() => expect(layer.style.transform).toBe('translate(30px, 20px) scale(1)'));
    fireEvent.pointerUp(canvas, { pointerId: 1 });
    fireEvent.pointerMove(canvas, { clientX: 400, clientY: 300, pointerId: 1 });
    await tick();
    expect(layer.style.transform).toBe('translate(30px, 20px) scale(1)');
    fireEvent.pointerDown(canvas, { clientX: 0, clientY: 0, pointerId: 2 });
    fireEvent.pointerCancel(canvas, { pointerId: 2 });
    fireEvent.pointerMove(canvas, { clientX: 50, clientY: 50, pointerId: 2 });
    await tick();
    expect(layer.style.transform).toBe('translate(30px, 20px) scale(1)');
  });

  it('measures itself, reports the size, and zooms around its real centre', async () => {
    vi.spyOn(Element.prototype, 'clientWidth', 'get').mockReturnValue(1300);
    vi.spyOn(Element.prototype, 'clientHeight', 'get').mockReturnValue(800);
    const { layer, sizes, user } = await setup();
    await waitFor(() => expect(sizes).toEqual([{ width: 1300, height: 800 }]));
    await user.click(screen.getByRole('button', { name: 'Zoom in' }));
    await waitFor(() => expect(layer.style.transform).toBe('translate(-130px, -80px) scale(1.2)'));
    expect(sizes).toHaveLength(1);
  });
});
