import { afterEach, describe, expect, it } from 'vitest';
import { AutomationDefinition, type EventInput, type Grant, type PolicyRule } from '@desk/protocol';
import { evaluatePolicy } from '../policy/evaluate';
import { createHarness, type Harness } from '../testing/harness';
import { openPrTool } from '../tools/git';
import { skillRunTool } from '../tools/skills';
import { webFetchTool } from '../tools/web';
import { addGrant, deriveGrant, grantRules, proposedGrants, sameGrant, widenDomain } from './grants';

const on = { sandboxAvailable: true };

describe('grants', () => {
  it('derives web grants from the exact host', () => {
    expect(deriveGrant('web_fetch', { domain: 'news.example.com' }, 'digest')).toEqual({ tool: 'web_fetch', match: { domain: 'news.example.com' }, action: 'allow' });
    expect(deriveGrant('web_search', {}, 'digest')).toEqual({ tool: 'web_search', action: 'allow' });
  });

  it("derives git grants from the automation's branches", () => {
    expect(deriveGrant('git_push', { branch: 'desk/auto-digest-01jx' }, 'digest')).toEqual({ tool: 'git_push', match: { branch: 'desk/auto-digest-*' }, action: 'allow' });
    expect(deriveGrant('open_pr', {}, 'digest')).toEqual({ tool: 'open_pr', match: { branch: 'desk/auto-digest-*' }, action: 'allow' });
  });

  it('derives a skill_run grant for the script with any arguments, escaped', () => {
    expect(deriveGrant('skill_run', { command: 'mine/fetch.py --since 2026' }, 'digest')).toEqual({ tool: 'skill_run', match: { command: '^mine/fetch\\.py(\\s|$)' }, action: 'allow' });
    expect(deriveGrant('skill_run', { command: 'mine/scripts/run(1).py $HOME' }, 'd').match).toEqual({ command: '^mine/scripts/run\\(1\\)\\.py(\\s|$)' });
    expect(deriveGrant('skill_run', {}, 'd').match).toEqual({ command: '^$' });
  });

  it('derives an exact, anchored command for bash', () => {
    expect(deriveGrant('bash', { command: 'python3 x.py (a) $HOME.' }, 'd')).toEqual({ tool: 'bash', match: { command: '^python3 x\\.py \\(a\\) \\$HOME\\.$' }, action: 'allow' });
    expect(deriveGrant('bash_background', { command: 'npm run dev' }, 'd').match).toEqual({ command: '^npm run dev$' });
    expect(deriveGrant('skill_delete', {}, 'd')).toEqual({ tool: 'skill_delete', action: 'allow' });
  });

  it('widens a domain to its registrable domain', () => {
    expect(widenDomain('news.bbc.co.uk')).toEqual(['bbc.co.uk', '*.bbc.co.uk']);
    expect(widenDomain('api.github.com')).toEqual(['github.com', '*.github.com']);
    expect(widenDomain('example.com')).toEqual(['example.com', '*.example.com']);
    expect(widenDomain('www.shop.example.com.au')).toEqual(['example.com.au', '*.example.com.au']);
    expect(widenDomain('a.b.example.fr')).toEqual(['example.fr', '*.example.fr']);
    expect(widenDomain('192.168.1.10')).toEqual(['192.168.1.10']);
    expect(widenDomain('localhost')).toEqual(['localhost']);
    expect(widenDomain('co.uk')).toEqual(['co.uk']);
  });

  it('turns grants into rules that go before the project policy', () => {
    const grant = deriveGrant('skill_run', { command: 'mine/fetch.py --since 2025' }, 'digest');
    const rules: PolicyRule[] = [...grantRules([grant]), { tool: 'skill_run', action: 'ask' }];
    const run = (script: string, args: string[] = []) => evaluatePolicy(skillRunTool, { name: 'mine', script, args }, rules, on).action;
    expect(run('fetch.py', ['--since', '2026'])).toBe('allow');
    expect(run('fetch.py')).toBe('allow');
    expect(run('fetch.pyx')).toBe('ask');
    expect(run('other.py')).toBe('ask');
    expect(grantRules([{ tool: 'bash', action: 'deny', delegate_to_desk: true } as Grant])).toEqual([{ tool: 'bash', action: 'deny' }]);

    const web = [...grantRules([{ tool: 'web_fetch', match: { domain: 'news.example.com' }, action: 'allow' }]), { tool: 'web_fetch', action: 'ask' as const }];
    expect(evaluatePolicy(webFetchTool, { url: 'https://news.example.com/a' }, web, on).action).toBe('allow');
    expect(evaluatePolicy(webFetchTool, { url: 'https://example.com/a' }, web, on).action).toBe('ask');
  });

  it("lets a branch grant match open_pr on the automation's branch", () => {
    const rules: PolicyRule[] = [...grantRules([deriveGrant('open_pr', {}, 'digest')]), { tool: 'open_pr', action: 'ask' }];
    expect(evaluatePolicy(openPrTool, { title: 't', body: '' }, rules, { ...on, gitBranch: 'desk/auto-digest-01jx' }).action).toBe('allow');
    expect(evaluatePolicy(openPrTool, { title: 't', body: '' }, rules, { ...on, gitBranch: 'desk/fix-1' }).action).toBe('ask');
  });

  it('compares and adds grants without duplicates', () => {
    const a: Grant = { tool: 'web_fetch', match: { domain: 'x.dev' }, action: 'allow' };
    expect(sameGrant(a, { ...a, match: { domain: 'x.dev' } })).toBe(true);
    expect(sameGrant(a, { ...a, action: 'deny' })).toBe(false);
    expect(sameGrant({ tool: 'open_pr', action: 'allow' }, { tool: 'open_pr', match: {}, action: 'allow' })).toBe(true);
    expect(addGrant([a], { ...a })).toEqual([a]);
    expect(addGrant([a], { tool: 'web_search', action: 'allow' })).toHaveLength(2);
  });
});

describe('proposedGrants', () => {
  let h: Harness;
  afterEach(async () => h?.cleanup());

  const P = 'proj1';
  const e = (type: string, payload: unknown, agent_id: string | null = null) => ({ project_id: P, agent_id, type, payload }) as EventInput;
  const started = (run_id: string, automation_id: string) =>
    e('automation.run_started', { run_id, automation_id, version: 1, trigger: 'test', test: true, inputs: {}, by: 'agent:desk1', deadline_at: '2026-09-29T06:00:00.000Z' });
  const stepAgent = (id: string, run_id: string) =>
    e('agent.created', { role: 'step', model: 'fake-model', title: 'Summarise', brief: 'b', workspace_path: `/tmp/${id}`, parent_id: null, automation: { run_id, step_id: 'sum' } }, id);
  const gate = (subject: string) => ({ tool: 'skill_run', subject, reason: 'Policy rule → ask' });
  const stepChanged = (run_id: string, payload: object) => e('automation.step_changed', { run_id, step_id: 'fetch', attempt: 1, ...payload });

  it("proposes what the user approved in this automation's runs, minus its grants", async () => {
    h = await createHarness();
    let n = 0;
    const approval = (agent: string, tool: string, args: unknown, decision: 'approved' | 'denied', by: 'user' | 'desk' = 'user') => {
      const approval_id = `ap${++n}`;
      h.store.append([
        e(
          'approval.requested',
          { approval_id, run_id: `run-${agent}`, tool_call_id: `tc${n}`, tool, arguments: typeof args === 'string' ? args : JSON.stringify(args), reason: `No policy rule allows ${tool}`, delegate_to_desk: false },
          agent,
        ),
        e('approval.resolved', { approval_id, decision, resolved_by: by }, agent),
      ]);
    };
    const def = AutomationDefinition.parse({ title: 'Digest', steps: [{ id: 'w', title: 'Wait', kind: 'wait', minutes: 1 }] });
    h.store.append([
      e('project.created', { name: 'P', goal: 'G', instructions: '' }),
      e('automation.saved', { automation_id: 'a1', name: 'digest', version: 1, definition: def, origin: 'user', change_note: '', via: 'editor' }),
      started('r1', 'a1'),
      started('r9', 'a9'), // another automation's run
      stepAgent('s1', 'r1'),
      stepAgent('s9', 'r9'),
      e('agent.created', { role: 'thread', model: 'fake-model', title: 'T', brief: 'b', workspace_path: '/tmp/t1', parent_id: null }, 't1'),
    ]);
    approval('s1', 'web_fetch', { url: 'https://news.example.com/x' }, 'approved');
    approval('s1', 'web_fetch', { url: 'https://evil.example.org/' }, 'denied');
    approval('s1', 'web_fetch', { url: 'https://desk.example.net/' }, 'approved', 'desk');
    approval('s9', 'web_fetch', { url: 'https://other.example.com/' }, 'approved'); // another automation
    approval('t1', 'web_fetch', { url: 'https://thread.example.com/' }, 'approved'); // not a step agent
    approval('s1', 'mystery', {}, 'approved'); // no such tool
    approval('s1', 'web_fetch', '{', 'approved'); // unreadable arguments
    h.store.append([
      stepChanged('r1', { status: 'waiting', gate: gate('mine/fetch.py --since 2026') }),
      stepChanged('r1', { status: 'running', answered_by: 'user', gate: gate('mine/fetch.py --since 2026') }), // approved
      stepChanged('r1', { step_id: 'push', status: 'failed', answered_by: 'user', gate: gate('mine/push.py'), error: 'Denied by the user' }),
      stepChanged('r9', { status: 'running', answered_by: 'user', gate: gate('mine/other.py') }),
    ]);
    approval('s1', 'web_fetch', { url: 'https://news.example.com/y' }, 'approved'); // the same grant again

    const tools = (name: string) => [webFetchTool].find((t) => t.name === name);
    const fetchGrant: Grant = { tool: 'web_fetch', match: { domain: 'news.example.com' }, action: 'allow' };
    const scriptGrant: Grant = { tool: 'skill_run', match: { command: '^mine/fetch\\.py(\\s|$)' }, action: 'allow' };
    expect(proposedGrants(h.store.db, 'a1', tools)).toEqual([fetchGrant, scriptGrant]);

    h.store.append(e('automation.grants_set', { automation_id: 'a1', grants: [fetchGrant], reason: 'enabled' }));
    expect(proposedGrants(h.store.db, 'a1', tools)).toEqual([scriptGrant]);
    expect(proposedGrants(h.store.db, 'nope', tools)).toEqual([]);
  });
});
