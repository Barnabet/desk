import { fireEvent, render, screen } from '@testing-library/angular';
import userEvent from '@testing-library/user-event';
import type { ModelInfo } from '@desk/protocol';
import { describe, expect, it } from 'vitest';
import { DEFAULT_STYLE, SettingsFields, type WorkingStyle } from './settings-fields';

const model = (id: string, reasoning_efforts: ModelInfo['reasoning_efforts'] = [], default_reasoning_effort: ModelInfo['default_reasoning_effort'] = null): ModelInfo => ({
  id,
  family: 'claude',
  context_window: 200_000,
  max_output_tokens: 32_000,
  reasoning_efforts,
  default_reasoning_effort,
  concurrency: 4,
  vision: true,
});

async function setup(value: WorkingStyle = DEFAULT_STYLE, models: ModelInfo[] | null = null) {
  const patches: Array<Partial<WorkingStyle>> = [];
  await render(SettingsFields, { inputs: { value, models, idPrefix: 'p' }, on: { changed: (p: Partial<WorkingStyle>) => patches.push(p) } });
  return { patches, user: userEvent.setup() };
}

const options = (label: string) => Array.from((screen.getByLabelText(label) as HTMLSelectElement).options).map((o) => o.textContent);

describe('SettingsFields', () => {
  it('reports check-ins, autonomy and review rounds as patches, clamping the rounds', async () => {
    const { patches, user } = await setup();
    await user.click(screen.getByLabelText(/Minimal/));
    await user.click(screen.getByLabelText(/Ask before dispatching/));
    const rounds = screen.getByLabelText('Review rounds');
    fireEvent.input(rounds, { target: { value: '12' } });
    fireEvent.input(rounds, { target: { value: '-3' } });
    expect(patches).toEqual([{ check_in: 'minimal' }, { autonomy: 'ask-before-dispatch' }, { review_rounds: 10 }, { review_rounds: 0 }]);
  });

  it("offers the registry's models, marks one it does not know, and drops a level the new model does not take", async () => {
    const models = [model('claude-opus-5-5', ['low', 'high', 'max'], 'high'), model('claude-fable-5-1', ['low', 'high'], 'high')];
    const { patches, user } = await setup({ ...DEFAULT_STYLE, desk_model: 'gpt-old', desk_reasoning_effort: 'max' }, models);
    expect(options("Desk's model")).toEqual(['gpt-old (not in the registry)', 'claude-opus-5-5', 'claude-fable-5-1']);
    expect((screen.getByLabelText("Desk's model") as HTMLSelectElement).value).toBe('gpt-old');
    expect(options('Fallback when rate limited')).toEqual(['None', 'claude-opus-5-5', 'claude-fable-5-1']);
    expect((screen.getByLabelText("Desk's reasoning effort") as HTMLSelectElement).disabled).toBe(true);
    expect(options("Desk's reasoning effort")).toEqual(['Model default', 'max (not taken by this model)']);
    expect(options("Threads' reasoning effort")).toEqual(['Model default (high)', 'low', 'high', 'max']);
    await user.selectOptions(screen.getByLabelText("Desk's model"), 'claude-fable-5-1');
    await user.selectOptions(screen.getByLabelText("Desk's model"), 'claude-opus-5-5');
    await user.selectOptions(screen.getByLabelText('Fallback when rate limited'), 'claude-fable-5-1');
    await user.selectOptions(screen.getByLabelText('Fallback when rate limited'), 'None');
    expect(patches).toEqual([
      { desk_model: 'claude-fable-5-1', desk_reasoning_effort: null },
      { desk_model: 'claude-opus-5-5' },
      { fallback_model: 'claude-fable-5-1' },
      { fallback_model: null },
    ]);
  });

  it('sets threads at once from its slots and its number field', async () => {
    const { patches, user } = await setup();
    expect(screen.getByRole('button', { name: '4 threads at once' }).getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByRole('button', { name: '1 thread at once' }).getAttribute('aria-pressed')).toBe('false');
    expect(screen.getByText('Threads at once · 4')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: '3 threads at once' }));
    fireEvent.input(screen.getByLabelText('Threads at once'), { target: { value: '40' } });
    expect(patches).toEqual([{ max_concurrent_threads: 3 }, { max_concurrent_threads: 32 }]);
  });

  it('waits for the registry before offering models or levels', async () => {
    await setup(DEFAULT_STYLE, null);
    expect((screen.getByLabelText("Desk's model") as HTMLSelectElement).disabled).toBe(true);
    expect(options("Desk's model")).toEqual(['claude-opus-5-5']);
    expect((screen.getByLabelText("Threads' reasoning effort") as HTMLSelectElement).disabled).toBe(true);
    expect(screen.queryByText(/takes no reasoning level/)).toBeNull();
  });
});
