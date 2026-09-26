import { TestBed } from '@angular/core/testing';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/angular';
import { describe, expect, it } from 'vitest';
import type { ModelInfo } from '@desk/protocol';
import { ToastService } from '../components/toast';
import { FakeDeskBridge, type FakeHandlers } from '../testing/fake-bridge';
import { ModelsEditor, modelProblems, toggleEffort } from './models-editor';

const model = (id: string): ModelInfo => ({ id, family: 'claude', context_window: 200000, max_output_tokens: 32000, reasoning_efforts: ['low', 'medium', 'high'], default_reasoning_effort: null, concurrency: 4, vision: true });

/** The editor inside System's "Model registry" section, as SystemScreen renders it. */
async function setup(handlers: FakeHandlers = {}) {
  const bridge = new FakeDeskBridge({
    'models.list': () => [model('claude-opus-5-5'), model('claude-fable-5-1')],
    'models.replace': ({ models }: { models: unknown[] }) => models,
    ...handlers,
  });
  await render(`<section aria-labelledby="sys-models"><h2 id="sys-models">Model registry</h2><div deskModelsEditor></div></section>`, {
    imports: [ModelsEditor],
    providers: bridge.providers,
  });
  return { bridge, reg: screen.getByRole('region', { name: 'Model registry' }) };
}

describe('modelProblems', () => {
  it('explains what the daemon would reject', () => {
    expect(modelProblems([])).toEqual(['Keep at least one model.']);
    expect(modelProblems([model('a'), model('a')])).toEqual(['a is listed twice.']);
    expect(modelProblems([{ ...model(''), concurrency: 0 }])).toEqual(['Every model needs an id.', 'Token limits must be positive and concurrency at least 1.']);
  });
});

describe('toggleEffort', () => {
  it('keeps the levels in order and forgets a default that is no longer offered', () => {
    const m: ModelInfo = { ...model('a'), reasoning_efforts: ['high'], default_reasoning_effort: 'high' };
    expect(toggleEffort(m, 'low')).toEqual({ reasoning_efforts: ['low', 'high'], default_reasoning_effort: 'high' });
    expect(toggleEffort(m, 'high')).toEqual({ reasoning_efforts: [], default_reasoning_effort: null });
  });
});

describe('ModelsEditor', () => {
  it('edits the model registry', async () => {
    const { bridge, reg } = await setup();
    fireEvent.click(await within(reg).findByRole('button', { name: 'Add model' }));
    fireEvent.input(within(reg).getByLabelText('Model 3 id'), { target: { value: 'claude-opus-5-5' } });
    expect(within(reg).getByRole('alert').textContent).toContain('claude-opus-5-5 is listed twice.');
    expect((within(reg).getByRole('button', { name: 'Save registry' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.input(within(reg).getByLabelText('Model 3 id'), { target: { value: 'gpt-6-sol' } });
    fireEvent.change(within(reg).getByLabelText('Model 3 family'), { target: { value: 'gpt' } });
    // Reasoning levels: chips per level, and a default drawn from the chosen levels.
    const pressed = (i: number) =>
      within(within(reg).getByRole('group', { name: `Model ${i} reasoning levels` }))
        .getAllByRole('button')
        .filter((b) => b.getAttribute('aria-pressed') === 'true')
        .map((b) => b.textContent);
    expect(pressed(1)).toEqual(['low', 'medium', 'high']);
    expect((within(reg).getByLabelText('Model 3 default reasoning effort') as HTMLSelectElement).disabled).toBe(true);
    const levels3 = within(reg).getByRole('group', { name: 'Model 3 reasoning levels' });
    fireEvent.click(within(levels3).getByRole('button', { name: 'max' }));
    fireEvent.click(within(levels3).getByRole('button', { name: 'high' }));
    expect(pressed(3)).toEqual(['high', 'max']);
    fireEvent.change(within(reg).getByLabelText('Model 3 default reasoning effort'), { target: { value: 'max' } });
    fireEvent.click(within(levels3).getByRole('button', { name: 'max' }));
    expect((within(reg).getByLabelText('Model 3 default reasoning effort') as HTMLSelectElement).value).toBe('');
    fireEvent.change(within(reg).getByLabelText('Model 3 default reasoning effort'), { target: { value: 'high' } });
    // Vision: on for new models; turning it off makes view_image refuse for that model.
    expect((within(reg).getByLabelText('Model 3 sees images') as HTMLInputElement).checked).toBe(true);
    fireEvent.click(within(reg).getByLabelText('Model 3 sees images'));
    fireEvent.click(within(reg).getByRole('button', { name: 'Remove model 2' }));
    fireEvent.click(within(reg).getByRole('button', { name: 'Save registry' }));
    await waitFor(() =>
      expect((bridge.calls.find((c) => c.channel === 'models.replace')?.input as { models: ModelInfo[] }).models.map((m) => [m.id, m.reasoning_efforts, m.default_reasoning_effort, m.vision])).toEqual([
        ['claude-opus-5-5', ['low', 'medium', 'high'], null, true],
        ['gpt-6-sol', ['high'], 'high', false],
      ]),
    );
  });

  it('says it is loading, then why the registry could not be read', async () => {
    let fail: (err: unknown) => void = () => {};
    const { reg } = await setup({ 'models.list': () => new Promise((_, reject) => (fail = reject)) });
    expect(within(reg).getByText('Loading…')).toBeTruthy();
    // React renders a bare paragraph until the registry is loaded: the host adds no box and no class.
    const host = reg.querySelector<HTMLElement>('[deskModelsEditor]')!;
    expect([host.className, host.style.display]).toEqual(['', 'contents']);
    fail({ code: 'daemon_down', message: 'deskd is not running.' });
    expect(await within(reg).findByText('deskd is not running.')).toBeTruthy();
    expect([host.className, host.style.display]).toEqual(['', 'contents']);
    expect(within(reg).queryByRole('button', { name: 'Add model' })).toBeNull();
  });

  it('discards a draft, and saves trimmed ids with a toast', async () => {
    const { bridge, reg } = await setup();
    const id = (await within(reg).findByLabelText('Model 2 id')) as HTMLInputElement;
    const host = reg.querySelector<HTMLElement>('[deskModelsEditor]')!;
    expect([host.className, host.style.display]).toEqual(['models-editor', '']);
    fireEvent.input(id, { target: { value: '  claude-fable-5-2 ' } });
    fireEvent.click(within(reg).getByRole('button', { name: 'Discard changes' }));
    expect(id.value).toBe('claude-fable-5-1');
    expect(within(reg).queryByRole('button', { name: 'Discard changes' })).toBeNull();
    expect((within(reg).getByRole('button', { name: 'Save registry' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.input(id, { target: { value: '  claude-fable-5-2 ' } });
    fireEvent.click(within(reg).getByRole('button', { name: 'Save registry' }));
    await waitFor(() => expect(TestBed.inject(ToastService).list().map((t) => t.message)).toEqual(['Model registry saved.']));
    expect((bridge.calls.find((c) => c.channel === 'models.replace')?.input as { models: ModelInfo[] }).models.map((m) => m.id)).toEqual(['claude-opus-5-5', 'claude-fable-5-2']);
    await waitFor(() => expect(id.value).toBe('claude-fable-5-2'));
    expect(within(reg).queryByRole('button', { name: 'Discard changes' })).toBeNull();
  });

  it('shows the whole number a box holds, as the desktop rewrites it', async () => {
    const { reg } = await setup();
    const atOnce = (await within(reg).findByLabelText('Model 1 concurrency')) as HTMLInputElement;
    // 4.5 counts as 4, which the box already held: nothing changes, and the box says 4 again.
    fireEvent.input(atOnce, { target: { value: '4.5' } });
    expect(atOnce.value).toBe('4');
    expect(within(reg).queryByRole('button', { name: 'Discard changes' })).toBeNull();
    const output = within(reg).getByLabelText('Model 1 max output tokens') as HTMLInputElement;
    fireEvent.input(output, { target: { value: '32000.9' } });
    expect(output.value).toBe('32000');
    // An emptied box holds 0, and says so.
    fireEvent.input(atOnce, { target: { value: '' } });
    expect(atOnce.value).toBe('0');
    expect(within(reg).getByRole('alert').textContent).toContain('Token limits must be positive and concurrency at least 1.');
  });

  it('shows the number it saved, not the text typed after it', async () => {
    const { bridge, reg } = await setup();
    const atOnce = (await within(reg).findByLabelText('Model 2 concurrency')) as HTMLInputElement;
    fireEvent.input(atOnce, { target: { value: '2.9' } });
    expect(atOnce.value).toBe('2');
    // 2.2 also counts as 2, so the draft does not change: once 2 is saved, the box says 2, not 2.2.
    fireEvent.input(atOnce, { target: { value: '2.2' } });
    fireEvent.click(within(reg).getByRole('button', { name: 'Save registry' }));
    await waitFor(() => expect(within(reg).queryByRole('button', { name: 'Discard changes' })).toBeNull());
    expect((bridge.calls.find((c) => c.channel === 'models.replace')?.input as { models: ModelInfo[] }).models.map((m) => m.concurrency)).toEqual([4, 2]);
    expect(atOnce.value).toBe('2');
  });
});
