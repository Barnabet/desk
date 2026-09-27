import { useEffect, useState } from 'react';
import type { InputSpec } from '@desk/protocol';
import { scriptFiles, skillChoices, type SkillChoice } from '@desk/ui-core';
import { call } from '../../bridge';

/** The skills a step can name (project, global, built-in), loaded once per project. Empty until loaded or when loading fails. */
export function useSkillChoices(projectId: string): SkillChoice[] {
  const [choices, setChoices] = useState<SkillChoice[]>([]);
  useEffect(() => {
    let live = true;
    Promise.all([call('skills.list', { projectId }), call('skills.list', {}), call('builtins.list', { projectId })])
      .then(([project, global, builtins]) => live && setChoices(skillChoices(project, global, builtins)))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [projectId]);
  return choices;
}

/** The scripts of the skill a script step names, from the copy agents would use. */
export function useSkillScripts(projectId: string, choice: SkillChoice | null): { scripts: string[]; loading: boolean } {
  const [state, setState] = useState<{ key: string; scripts: string[] } | null>(null);
  const key = choice ? `${choice.source}:${choice.name}` : '';
  useEffect(() => {
    if (!choice) return;
    let live = true;
    const load =
      choice.source === 'builtin'
        ? call('builtins.get', { name: choice.name })
        : choice.source === 'project'
          ? call('skills.get', { projectId, name: choice.name })
          : call('skills.get', { name: choice.name });
    load.then((d) => live && setState({ key, scripts: scriptFiles(d.files) })).catch(() => live && setState({ key, scripts: [] }));
    return () => {
      live = false;
    };
    // `key` names the choice; the object itself changes identity on every render of the caller.
  }, [projectId, key]);
  return { scripts: state?.key === key ? state.scripts : [], loading: !!choice && state?.key !== key };
}

/** The project's automation names (Run automation's picker). */
export function useAutomationNames(projectId: string): string[] {
  const [names, setNames] = useState<string[]>([]);
  useEffect(() => {
    let live = true;
    call('automations.list', { projectId })
      .then((list) => live && setNames(list.map((a) => a.name).sort()))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [projectId]);
  return names;
}

/** The inputs of the project's automation called `name`, or null while unknown. */
export function useAutomationInputs(projectId: string, name: string): InputSpec[] | null {
  const [inputs, setInputs] = useState<{ name: string; inputs: InputSpec[] } | null>(null);
  useEffect(() => {
    if (!name) return;
    let live = true;
    call('automations.list', { projectId })
      .then((list) => {
        const a = list.find((x) => x.name === name);
        return a ? call('automations.get', { id: a.id }).then((d) => d.definition.inputs) : [];
      })
      .then((list) => live && setInputs({ name, inputs: list }))
      .catch(() => live && setInputs({ name, inputs: [] }));
    return () => {
      live = false;
    };
  }, [projectId, name]);
  return name && inputs?.name === name ? inputs.inputs : null;
}
