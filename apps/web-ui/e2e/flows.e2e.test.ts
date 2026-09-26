import { execFileSync } from 'node:child_process';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import type { Page } from 'playwright';
import { call, text, tools, type ChatRequest, type FakeReply } from '@desk/fake-model';
import { startWebE2E, type SignedIn, type WebE2E } from './harness';

let e2e: WebE2E;
/** The Checkout project's git source, for its Desk's spawn_thread: the repository case sets it before it briefs Desk. */
let checkoutSource = '';

const content = (m: Record<string, unknown> | undefined) => (typeof m?.content === 'string' ? m.content : JSON.stringify(m?.content ?? ''));
const system = (req: ChatRequest) => content(req.messages[0]);
const userTurns = (req: ChatRequest) => req.messages.filter((m) => m.role === 'user').map(content);
/** The tools the latest assistant turn called. */
const lastCalls = (req: ChatRequest) => {
  const assistant = [...req.messages].reverse().find((m) => m.role === 'assistant' && Array.isArray(m.tool_calls));
  return ((assistant?.tool_calls as Array<{ function: { name: string } }> | undefined) ?? []).map((c) => c.function.name);
};
/** Whether a user turn holds a runtime message header of this kind, e.g. `[message #12 from thread "…" (…) — completed]`. */
const hasHeader = (said: string, kind: string) => new RegExp(`^\\[message #\\d+ from .+ — ${kind}\\]$`, 'm').test(said);
/** Whether a user turn holds the runtime's header for a thread's `completed` notice. */
const completed = (said: string, title: string) => new RegExp(`^\\[message #\\d+ from thread "${title}" \\(.+\\) — completed\\]$`, 'm').test(said);
const ANSWERED = /^\[message #\d+ from thread "Auth API" \(.+\) — answer to your question #\d+\]$/m;
const ANSWER_MODE = '[Desk runtime — answer mode]';

/** Onboarding revamp's Desk: dispatch a thread and ask a question; send the first result back for revision; report on the second. */
function flowsDesk(req: ChatRequest): FakeReply {
  const last = req.messages.at(-1);
  if (last?.role === 'tool') return text('On it.');
  const said = content(last);
  if (said.includes('Relaunch onboarding')) {
    return tools(
      call('spawn_thread', { title: 'Signup checklist', brief: 'Draft the signup checklist.' }),
      call('ask_user', { question: 'Data source or teammate invite first?', options: ['Data source', 'Teammate invite'] }),
    );
  }
  if (hasHeader(said, 'completed')) {
    const spawned = req.messages.map(content).join('\n').match(/Spawned thread (\S+) /)?.[1] ?? '';
    const results = req.messages.filter((m) => m.role === 'user' && hasHeader(content(m), 'completed')).length;
    return results === 1
      ? tools(call('message_thread', { thread_id: spawned, kind: 'revision', text: 'Item 3 reads as salesy; tighten it.' }))
      : tools(call('report', { headline: 'The signup checklist is in', progress: 'Two rounds, now tight.', needs_you: ['Review the checklist copy'], results: [] }));
  }
  return text('Noted.');
}

/** Signup checklist: run a command that needs approval, then complete; complete again after a revision; acknowledge steering (W2). */
function checklist(req: ChatRequest): FakeReply {
  const last = req.messages.at(-1);
  if (last?.role === 'tool') return lastCalls(req)[0] === 'bash' ? tools(call('complete', { summary: 'Checklist drafted: five steps, one first win.' })) : text('Done.');
  const said = content(last);
  if (said.includes('Keep it short')) return text('Will do.');
  if (hasHeader(said, 'revision')) return tools(call('complete', { summary: 'Checklist tightened.' }));
  return tools(call('bash', { command: 'echo sudo make me a checklist' }));
}

/** Sign-in's Desk: Auth API first, then Frontend once Auth API is done; What's up rewritten in every turn that changes things. */
function signInDesk(req: ChatRequest): FakeReply {
  const last = req.messages.at(-1);
  if (last?.role === 'tool') return text('On it.');
  const said = content(last);
  if (said.includes('Build the sign-in page')) {
    return tools(
      call('spawn_thread', { title: 'Auth API', brief: 'Build the login API: POST /login returns a token.' }),
      call('update_whats_up', { text: 'Auth API is building the login API. Frontend starts when it is done.' }),
    );
  }
  if (completed(said, 'Auth API')) {
    return tools(
      call('spawn_thread', { title: 'Frontend', brief: 'Build the login form against the Auth API.' }),
      call('update_whats_up', { text: 'Auth API is done. Frontend is building the login form.' }),
    );
  }
  if (completed(said, 'Frontend')) return tools(call('update_whats_up', { text: 'The sign-in page is built.' }));
  return text('Noted.');
}

/** Auth API: completes at once; later, woken only to answer, it answers Frontend (and, in W2, the user). */
function authApi(req: ChatRequest): FakeReply {
  const last = req.messages.at(-1);
  if (last?.role === 'tool') return text('Done.');
  const said = content(last);
  if (said.includes(`${ANSWER_MODE} You were woken only to answer the user's question`)) return text('Tokens last 15 minutes.');
  if (said.includes(ANSWER_MODE)) return text('A JWT signed with RS256, sent as a Bearer token.');
  return tools(call('complete', { summary: 'Auth API ready: POST /login returns a JWT.' }));
}

/** Frontend: asks Auth API about its tokens, waits, and completes once the answer is in. */
function frontend(req: ChatRequest): FakeReply {
  if (req.messages.at(-1)?.role === 'tool' && lastCalls(req).includes('complete')) return text('Done.');
  if (userTurns(req).some((t) => ANSWERED.test(t))) return tools(call('complete', { summary: 'Login form built: it sends the JWT as a Bearer token.' }));
  if (lastCalls(req).includes('message_thread')) return tools(call('wait_for_reply', {}));
  return tools(call('message_thread', { thread_id: 'Auth API', kind: 'question', text: 'Which token format does /login return?' }), call('wait_for_reply', {}));
}

/** Checkout's Desk: one thread on the repository, then thanks. */
function checkoutDesk(req: ChatRequest): FakeReply {
  const last = req.messages.at(-1);
  if (last?.role === 'tool') return text('On it.');
  if (content(last).includes('Rewrite the checkout copy')) {
    // The brief avoids the user's words, so the thread's completed notice cannot look like a new brief.
    return tools(call('spawn_thread', { title: 'Checkout copy', brief: 'Improve copy.md and add a badge.', git_source_id: checkoutSource }));
  }
  return text('Thanks.');
}

/** The badge Checkout copy writes, and the bytes "Save a copy…" must download. */
const badgeSvg = '<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><circle cx="4" cy="4" r="4"/></svg>\n';

/** Checkout copy: writes the copy and a badge in its branch, then completes. */
function checkoutCopy(req: ChatRequest): FakeReply {
  if (req.messages.at(-1)?.role === 'tool') {
    return lastCalls(req).includes('write_file') ? tools(call('complete', { summary: 'Rewrote the checkout copy and added a badge.' })) : text('Done.');
  }
  return tools(
    call('write_file', { path: 'copy.md', content: '# Checkout\n\nPay in one step.\n' }),
    call('write_file', { path: 'badge.svg', content: badgeSvg }),
  );
}

/** One fake model for every scenario: each project's Desk by the project it coordinates, each thread by its assignment. */
function script(req: ChatRequest): FakeReply {
  const s = system(req);
  if (s.startsWith('You are Desk, the coordinator of the project "Onboarding revamp"')) return flowsDesk(req);
  if (s.startsWith('You are Desk, the coordinator of the project "Sign-in"')) return signInDesk(req);
  if (s.includes('## Your assignment: Signup checklist')) return checklist(req);
  if (s.includes('## Your assignment: Auth API')) return authApi(req);
  if (s.includes('## Your assignment: Frontend')) return frontend(req);
  if (s.startsWith('You are Desk, the coordinator of the project "Checkout"')) return checkoutDesk(req);
  if (s.includes('## Your assignment: Checkout copy')) return checkoutCopy(req);
  return text('Done.');
}

beforeAll(async () => {
  e2e = await startWebE2E({ script });
});

afterAll(async () => {
  await e2e?.close();
});

async function go(page: Page, hash: string) {
  await page.evaluate((h) => (window.location.hash = h), hash);
}

/** A signed-in browser past onboarding, on `hash`. */
async function openAt(hash: string): Promise<SignedIn> {
  const signed = await e2e.signIn();
  await signed.page.evaluate(() => localStorage.setItem('desk.onboarded', '1'));
  await go(signed.page, hash);
  return signed;
}

describe('the core loop in the browser', () => {
  it('briefs Desk, forks a thread, answers Desk, approves from Attention with ⌘⏎, the report lands as a hand-off, and the thread reopens with a message', async () => {
    const client = e2e.client();
    const { project } = await client.projects.create({ name: 'Onboarding revamp', goal: 'Relaunch onboarding next month' });
    const { context, page, problems } = await openAt(`#/p/${project.id}/conversation`);

    // Brief Desk from the composer.
    const composer = page.getByLabel('Message Desk');
    await composer.fill('Relaunch onboarding: start with a signup checklist.');
    await composer.press('Enter');
    await page.getByText('Relaunch onboarding: start with a signup checklist.').first().waitFor();

    // The thread forks on the line diagram, and Desk's question arrives.
    const diagram = page.getByRole('region', { name: /Line diagram/ });
    await diagram.getByRole('link', { name: /Signup checklist/ }).first().waitFor({ timeout: 20_000 });
    await page.getByRole('button', { name: 'Data source', exact: true }).click();
    await page.getByText('Answered').waitFor({ timeout: 15_000 });
    await e2e.shot(page, 'flows-1-conversation-forked');

    // The title bar's pill opens Attention, which inspects the thread's command and names it in the route.
    await page.getByRole('link', { name: /need you$/ }).click();
    const inspector = page.getByRole('article', { name: /clearance request/ });
    await expect.poll(() => inspector.getByLabel('Command').textContent(), { timeout: 15_000 }).toBe('$ echo sudo make me a checklist');
    await expect.poll(() => page.evaluate(() => window.location.hash)).toMatch(/^#\/attention\?item=approval%3A/);
    await inspector.getByLabel('Note to the thread (optional)').fill('Fine, it only echoes.');
    await e2e.shot(page, 'flows-2-attention');

    // ⌘⏎ (Ctrl+Enter off macOS) approves from the note, and the note goes with the decision.
    await page.keyboard.press('ControlOrMeta+Enter');
    await expect
      .poll(async () => (await client.approvals.list(project.id, 'approved')).map((a) => [a.tool, a.note, a.resolved_by]), { timeout: 10_000 })
      .toEqual([['bash', 'Fine, it only echoes.', 'user']]);

    // The thread reports, Desk sends it back, it reports again, and Desk reports to you.
    await go(page, `#/p/${project.id}/conversation`);
    await page.getByRole('heading', { name: 'The signup checklist is in' }).waitFor({ timeout: 30_000 });
    await diagram.getByText(/^sent back/).first().waitFor();
    const needsYou = page.getByRole('link', { name: 'Review the checklist copy' });
    await needsYou.waitFor();
    await e2e.shot(page, 'flows-3-conversation-report');

    // What the report needs from you is the one thing left: the pill counts it, and its link racks it in HANDOFFS.
    // Attention counts are global across projects: a later scenario must not leave items in Attention before this one runs.
    await page.getByRole('link', { name: '1 need you', exact: true }).waitFor({ timeout: 10_000 });
    await needsYou.click();
    const handoff = page.getByRole('article', { name: 'Selected: from a report' });
    await handoff.getByText('The signup checklist is in').waitFor();
    const handoffs = page.getByRole('region', { name: 'Strip rack' }).getByRole('group', { name: 'HANDOFFS: From reports, 1' });
    const strip = handoffs.getByRole('button', { name: /^DOC, Onboarding revamp: Review the checklist copy\. From Desk's report\. Waiting / });
    expect(await strip.getAttribute('aria-current')).toBe('true');
    await e2e.shot(page, 'flows-4-handoff');
    await handoff.getByRole('button', { name: 'Dismiss' }).click();
    await page.getByRole('heading', { name: 'All clear' }).waitFor({ timeout: 10_000 });
    await page.getByRole('link', { name: 'All clear' }).waitFor();

    // The thread's route shows the revision. The thread is done, so its box asks; reopen it with a message instead.
    const [t] = await client.threads.list(project.id);
    await go(page, `#/p/${project.id}/threads/${t!.id}`);
    await page.getByRole('button', { name: /Desk sent it back/ }).waitFor({ timeout: 15_000 });
    const transcript = page.getByRole('complementary', { name: 'Transcript' });
    await transcript.getByLabel('Ask this thread').fill('Keep it short');
    await transcript.getByRole('button', { name: 'Reopen with this…' }).click();
    await page.getByRole('dialog', { name: 'Reopen this thread?' }).getByRole('button', { name: 'Reopen', exact: true }).click();
    await transcript.getByText(/^You steered/).first().waitFor({ timeout: 15_000 });
    await transcript.getByText('Will do.').waitFor({ timeout: 15_000 });
    await e2e.shot(page, 'flows-5-thread');
    // It works in a scratch workspace: Diff says so.
    await page.getByRole('tab', { name: 'Diff' }).click();
    await page.getByText('No diff for this thread').waitFor();
    await go(page, `#/p/${project.id}/threads`);
    await page.getByRole('heading', { name: 'Threads' }).waitFor();
    await page.getByRole('link', { name: /Signup checklist/ }).waitFor();
    await e2e.shot(page, 'flows-6-roster');

    expect(problems).toEqual([]);
    await context.close();
  }, 180_000);

  it("shows one thread's question to another on the asker's lane, answered, in the pair sheet, the digest and both transcripts, and a done thread answers the user's Ask and stays Done", async () => {
    const client = e2e.client();
    const { project } = await client.projects.create({ name: 'Sign-in', goal: 'Ship a sign-in page' });
    const { context, page, problems } = await openAt(`#/p/${project.id}/conversation`);

    const composer = page.getByLabel('Message Desk');
    await composer.fill('Build the sign-in page: the login API first, then the form.');
    await composer.press('Enter');

    // Frontend asks Auth API, which is done: Auth API answers in an answer run, and one digest holds both messages.
    const digest = page.getByRole('button', { name: 'Between threads · 2 messages', exact: true });
    await digest.waitFor({ timeout: 45_000 });
    await e2e.shot(page, 'messaging-1-digest');
    const front = (await client.threads.list(project.id)).find((t) => t.title === 'Frontend')!;

    // Frontend's lane marks its question to Auth API, filled once answered, and the legend explains the ring.
    const mark = page.getByRole('button', { name: /^Frontend asked Auth API, \d\d:\d\d$/ });
    await expect.poll(() => mark.getAttribute('class')).toContain('line-q-answered');
    expect(await page.locator('.line-legend').textContent()).toContain('question');
    // A real click: the ring must be on top of what else sits on its lane.
    await mark.click();
    const sheet = page.getByRole('dialog', { name: 'Frontend ⇄ Auth API' });
    await sheet.waitFor();
    // One question, with Auth API's answer nested under it.
    expect(await sheet.locator('.pair-rows > li').count()).toBe(1);
    await sheet.locator('.pair-rows > li .pair-answers').getByText('A JWT signed with RS256').waitFor();
    await e2e.shot(page, 'messaging-2-pair');
    await sheet.getByRole('button', { name: 'Close' }).click();
    await sheet.waitFor({ state: 'detached' });

    // The digest's pair line opens the same sheet, whose answer links to Frontend where it received it.
    await digest.click();
    await page.getByRole('button', { name: /^Frontend ⇄ Auth API · 2/ }).click();
    const inFront = sheet.getByRole('link', { name: 'show in Frontend transcript' });
    expect(await inFront.getAttribute('href')).toMatch(new RegExp(`/threads/${front.id}\\?at=\\d+$`));
    await e2e.shot(page, 'messaging-3-digest-pair');

    // The answer's link opens Frontend where it received it: its question and Auth API's answer are two cards in one stop.
    const auth = (await client.threads.list(project.id)).find((t) => t.title === 'Auth API')!;
    await inFront.click();
    const both = page.getByRole('button', { name: /^Stop \d+: .*, 2 messages$/ });
    await both.waitFor({ timeout: 15_000 });
    await expect.poll(() => both.getAttribute('aria-pressed')).toBe('true');
    const tr = page.getByRole('complementary', { name: 'Transcript' });
    await tr.locator('.tr-card', { hasText: 'Answer from Auth API' }).first().waitFor();
    expect(await tr.locator('.tr-card', { hasText: 'Asked Auth API' }).count()).toBe(1);
    await e2e.shot(page, 'messaging-4-asker');

    // The answerer's route: Frontend's question is a card, and the answer run is one stop.
    await go(page, `#/p/${project.id}/threads/${auth.id}`);
    await page.getByRole('button', { name: /^Stop \d+: Answered Frontend/ }).waitFor({ timeout: 15_000 });
    expect(await tr.locator('.tr-card', { hasText: 'Frontend asked' }).count()).toBe(1);

    // The user asks the done thread; it answers from its context and stays Done.
    await tr.getByLabel('Ask this thread').fill('How long do tokens last?');
    await tr.getByRole('button', { name: 'Ask', exact: true }).click();
    await page.getByRole('button', { name: /^Stop \d+: You asked/ }).waitFor({ timeout: 15_000 });
    await page.getByRole('button', { name: /^Stop \d+: Answered you/ }).waitFor({ timeout: 15_000 });
    await tr.getByText('Tokens last 15 minutes.').first().waitFor();
    await expect.poll(() => page.locator('.thread-status-line').first().textContent()).toMatch(/^Done/);
    expect((await client.threads.list(project.id)).find((t) => t.id === auth.id)?.status).toBe('done');
    await e2e.shot(page, 'messaging-5-ask');

    expect(problems).toEqual([]);
    await context.close();
  }, 180_000);

  it('works a thread on a repository: the roster card with its branch, its route, the Diff, and Files with an SVG shown as text and saved as a download', async () => {
    // A git repository in the harness's home folder, as the project's source: the thread gets a branch of its own.
    const repo = join(e2e.home, 'code', 'checkout');
    mkdirSync(repo, { recursive: true });
    writeFileSync(join(repo, 'README.md'), '# Checkout\n');
    const git = (...args: string[]) => execFileSync('git', ['-c', 'user.email=t@t', '-c', 'user.name=t', ...args], { cwd: repo, stdio: 'pipe' });
    git('init', '-q', '-b', 'main');
    git('add', '.');
    git('commit', '-qm', 'init');
    const client = e2e.client();
    const { project, sources } = await client.projects.create({ name: 'Checkout', goal: 'Clearer checkout copy', sources: [{ path: repo }] });
    expect(sources.map((s) => s.kind)).toEqual(['git']);
    checkoutSource = sources[0]!.id;
    const { context, page, problems } = await openAt(`#/p/${project.id}/conversation`);

    const composer = page.getByLabel('Message Desk');
    await composer.fill('Rewrite the checkout copy.');
    await composer.press('Enter');
    await expect.poll(async () => (await client.threads.list(project.id)).find((t) => t.title === 'Checkout copy')?.status, { timeout: 30_000 }).toBe('done');
    const thread = (await client.threads.list(project.id)).find((t) => t.title === 'Checkout copy')!;
    expect(thread.git_branch).toBeTruthy();

    // The roster card names its branch; it opens the thread.
    await go(page, `#/p/${project.id}/threads`);
    const card = page.getByRole('link', { name: /Checkout copy/ });
    await card.waitFor();
    expect(await card.textContent()).toContain(thread.git_branch!);
    expect(await card.textContent()).toContain('Done');
    await card.click();
    await page.getByRole('heading', { name: 'Checkout copy', level: 1 }).waitFor();
    await page.getByRole('button', { name: /^Stop \d+: Reported to Desk/ }).waitFor({ timeout: 15_000 });
    expect(await page.locator('.thread-chips .chip .mono').textContent()).toBe(thread.git_branch);
    await e2e.shot(page, 'threads-1-route');

    // Diff: the branch against its base, both files added, and the patch.
    await page.getByRole('tab', { name: 'Diff' }).click();
    const note = page.locator('.diff-tab > p.small');
    await note.waitFor();
    expect(await note.textContent()).toContain(`${thread.git_branch} against `);
    expect(await note.textContent()).toContain('2 files changed.');
    const rows = page.locator('.diff-files tr');
    expect(await rows.allTextContents()).toEqual([expect.stringMatching(/^Abadge\.svg\+1/), expect.stringMatching(/^Acopy\.md\+3/)]);
    expect(await page.getByLabel('Patch').textContent()).toContain('+Pay in one step.');
    await e2e.shot(page, 'threads-2-diff');

    // Files: the SVG is source text, never an image (spec §4.11), and Save a copy… downloads it.
    await page.getByRole('tab', { name: 'Files' }).click();
    await page.getByRole('button', { name: /badge\.svg/ }).click();
    const viewer = page.locator('.file-viewer');
    await viewer.locator('.codeblock code', { hasText: '<circle' }).waitFor();
    expect(await viewer.locator('img, svg').count()).toBe(0);
    const [download] = await Promise.all([page.waitForEvent('download'), viewer.getByRole('button', { name: 'Save a copy…' }).click()]);
    expect(download.suggestedFilename()).toBe('badge.svg');
    // From the page's own Blob, byte for byte the source.
    expect(download.url()).toMatch(/^blob:/);
    expect(readFileSync(await download.path(), 'utf8')).toBe(badgeSvg);
    // Markdown renders.
    await page.getByRole('button', { name: /copy\.md/ }).click();
    await viewer.getByRole('heading', { name: 'Checkout' }).waitFor();
    await viewer.getByText('Pay in one step.').waitFor();
    await e2e.shot(page, 'threads-3-files');

    expect(problems).toEqual([]);
    await context.close();
  }, 180_000);
});
