import { computed, effect, inject, signal, untracked, type Signal } from '@angular/core';
import type { InputSpec } from '@desk/protocol';
import { scriptFiles, skillChoices, type SkillChoice } from '@desk/ui-core';
import { DeskBridge } from '../../core/desk-bridge';

/** The skills a step can name (project, global, built-in), loaded once per project. Empty until loaded or when loading fails. */
export function injectSkillChoices(projectId: () => string): Signal<SkillChoice[]> {
  const bridge = inject(DeskBridge);
  const choices = signal<SkillChoice[]>([]);
  let seq = 0;
  effect(() => {
    const id = projectId();
    const n = ++seq;
    Promise.all([bridge.call('skills.list', { projectId: id }), bridge.call('skills.list', {}), bridge.call('builtins.list', { projectId: id })])
      .then(([project, global, builtins]) => {
        if (n === seq) choices.set(skillChoices(project, global, builtins));
      })
      .catch(() => {});
  });
  return choices.asReadonly();
}

/** The scripts of the skill a script step names, from the copy agents would use. */
export function injectSkillScripts(projectId: () => string, choice: () => SkillChoice | null): Signal<{ scripts: string[]; loading: boolean }> {
  const bridge = inject(DeskBridge);
  const state = signal<{ key: string; scripts: string[] } | null>(null);
  // The key names the choice; the object itself is rebuilt whenever the skill list is.
  const key = computed(() => {
    const c = choice();
    return c ? `${c.source}:${c.name}` : '';
  });
  let seq = 0;
  effect(() => {
    const k = key();
    const id = projectId();
    const c = untracked(choice);
    if (!c) return;
    const n = ++seq;
    const load = c.source === 'builtin' ? bridge.call('builtins.get', { name: c.name }) : c.source === 'project' ? bridge.call('skills.get', { projectId: id, name: c.name }) : bridge.call('skills.get', { name: c.name });
    load.then(
      (d) => {
        if (n === seq) state.set({ key: k, scripts: scriptFiles(d.files) });
      },
      () => {
        if (n === seq) state.set({ key: k, scripts: [] });
      },
    );
  });
  return computed(() => {
    const k = key();
    const s = state();
    return { scripts: s?.key === k ? s.scripts : [], loading: k !== '' && s?.key !== k };
  });
}

/** The project's automation names (Run automation's picker). */
export function injectAutomationNames(projectId: () => string): Signal<string[]> {
  const bridge = inject(DeskBridge);
  const names = signal<string[]>([]);
  let seq = 0;
  effect(() => {
    const id = projectId();
    const n = ++seq;
    bridge
      .call('automations.list', { projectId: id })
      .then((list) => {
        if (n === seq) names.set(list.map((a) => a.name).sort());
      })
      .catch(() => {});
  });
  return names.asReadonly();
}

/** The inputs of the project's automation called `name`, or null while unknown. */
export function injectAutomationInputs(projectId: () => string, name: () => string): Signal<InputSpec[] | null> {
  const bridge = inject(DeskBridge);
  const state = signal<{ name: string; inputs: InputSpec[] } | null>(null);
  let seq = 0;
  effect(() => {
    const id = projectId();
    const want = name();
    if (!want) return;
    const n = ++seq;
    bridge
      .call('automations.list', { projectId: id })
      .then((list) => {
        const a = list.find((x) => x.name === want);
        return a ? bridge.call('automations.get', { id: a.id }).then((d) => d.definition.inputs) : [];
      })
      .then(
        (inputs) => {
          if (n === seq) state.set({ name: want, inputs });
        },
        () => {
          if (n === seq) state.set({ name: want, inputs: [] });
        },
      );
  });
  return computed(() => {
    const want = name();
    const s = state();
    return want && s?.name === want ? s.inputs : null;
  });
}
