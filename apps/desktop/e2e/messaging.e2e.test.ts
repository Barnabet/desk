import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { _electron as electron, type ElectronApplication, type Page } from 'playwright';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { clientFromDataDir } from '@desk/client/node';
import { startDaemon, type RunningDaemon } from '@desk/daemon';
import { call, startFakeModel, text, tools, type ChatRequest, type FakeModelServer, type FakeReply } from '@desk/fake-model';

const appDir = fileURLToPath(new URL('..', import.meta.url));
let dir: string;
let fake: FakeModelServer;
let daemon: RunningDaemon;
let app: ElectronApplication;

const content = (m: Record<string, unknown> | undefined) => (typeof m?.content === 'string' ? m.content : JSON.stringify(m?.content ?? ''));
const system = (req: ChatRequest) => content(req.messages[0]);
const userTurns = (req: ChatRequest) => req.messages.filter((m) => m.role === 'user').map(content);
/** The tools the latest assistant turn called. */
const lastCalls = (req: ChatRequest) => {
  const assistant = [...req.messages].reverse().find((m) => m.role === 'assistant' && Array.isArray(m.tool_calls));
  return ((assistant?.tool_calls as Array<{ function: { name: string } }> | undefined) ?? []).map((c) => c.function.name);
};
/** Whether a user turn holds the runtime's header for a thread's `completed` notice. */
const completed = (said: string, title: string) => new RegExp(`^\\[message #\\d+ from thread "${title}" \\(.+\\) — completed\\]$`, 'm').test(said);
const ANSWERED = /^\[message #\d+ from thread "Auth API" \(.+\) — answer to your question #\d+\]$/m;
const ANSWER_MODE = '[Desk runtime — answer mode]';

/** Desk: Auth API first, then Frontend once Auth API is done; What's up rewritten in every turn that changes things. */
function desk(req: ChatRequest): FakeReply {
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

/** Auth API: completes at once; later, woken only to answer, it answers Frontend and then the user. */
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

beforeAll(async () => {
  dir = mkdtempSync(join(tmpdir(), 'desk-messaging-'));
  fake = await startFakeModel((req) => {
    const s = system(req);
    if (s.startsWith('You are Desk,')) return desk(req);
    if (s.includes('## Your assignment: Auth API')) return authApi(req);
    if (s.includes('## Your assignment: Frontend')) return frontend(req);
    return text('Done.');
  });
  daemon = await startDaemon({ dataDir: join(dir, 'data'), port: 0, modelConfig: { baseURL: fake.url, apiKey: 'test' } });
  const { default: electronPath } = await import('electron');
  app = await electron.launch({
    executablePath: electronPath as unknown as string,
    args: [appDir],
    env: { ...process.env, DESK_DATA_DIR: join(dir, 'data'), DESK_USER_DATA: join(dir, 'user'), DESK_E2E: '1' },
  });
});

afterAll(async () => {
  await app?.close();
  await daemon?.stop();
  await fake?.close();
  rmSync(dir, { recursive: true, force: true });
});

/** Set DESK_E2E_SHOTS=<dir> to keep screenshots of each step for visual review. */
async function shot(page: Page, name: string) {
  const out = process.env.DESK_E2E_SHOTS;
  if (out) await page.screenshot({ path: join(out, `${name}.png`) });
}

async function go(page: Page, hash: string) {
  await page.evaluate((h) => (window.location.hash = h), hash);
}

describe('messages between threads, end to end', () => {
  it("shows one thread's question to another and its answer, and a done thread answers the user's Ask and stays Done", async () => {
    const client = clientFromDataDir(join(dir, 'data'));
    const { project } = await client.projects.create({ name: 'Sign-in', goal: 'Ship a sign-in page' });
    const page = await app.firstWindow();
    await page.evaluate(() => localStorage.setItem('desk.onboarded', '1'));
    await go(page, `#/p/${project.id}/conversation`);

    const composer = page.getByLabel('Message Desk');
    await composer.fill('Build the sign-in page: the login API first, then the form.');
    await composer.press('Enter');

    // Frontend asks Auth API, which is done: Auth API answers in an answer run, and one digest holds both messages.
    const digest = page.getByRole('button', { name: 'Between threads · 2 messages', exact: true });
    await digest.waitFor({ timeout: 45_000 });
    await shot(page, 'messaging-1-digest');
    const threads = await client.threads.list(project.id);
    const auth = threads.find((t) => t.title === 'Auth API')!;
    const front = threads.find((t) => t.title === 'Frontend')!;

    // Frontend's lane marks its question to Auth API, filled once answered, and the legend explains the ring.
    const mark = page.getByRole('button', { name: /^Frontend asked Auth API, \d\d:\d\d$/ });
    await expect.poll(() => mark.getAttribute('class')).toContain('line-q-answered');
    expect(await page.locator('.line-legend').textContent()).toContain('question');
    // The ring sits on the lane among labels and chips, so the click goes to it directly.
    await mark.dispatchEvent('click');
    const sheet = page.getByRole('dialog', { name: 'Frontend ⇄ Auth API' });
    await sheet.waitFor();
    // One question, with Auth API's answer nested under it.
    expect(await sheet.locator('.pair-rows > li').count()).toBe(1);
    await sheet.locator('.pair-rows > li .pair-answers').getByText('A JWT signed with RS256').waitFor();
    await shot(page, 'messaging-2-pair');
    await sheet.getByRole('button', { name: 'Close' }).click();
    await sheet.waitFor({ state: 'detached' });

    // The digest's pair line opens the same sheet; the answer's link opens Frontend where it received it.
    await digest.click();
    await page.getByRole('button', { name: /^Frontend ⇄ Auth API · 2/ }).click();
    const inFront = sheet.getByRole('link', { name: 'show in Frontend transcript' });
    expect(await inFront.getAttribute('href')).toMatch(new RegExp(`/threads/${front.id}\\?at=\\d+$`));
    await inFront.click();
    // Frontend's question and Auth API's answer are two cards in one stop, which the link selected.
    const both = page.getByRole('button', { name: /^Stop \d+: .*, 2 messages$/ });
    await both.waitFor({ timeout: 15_000 });
    await expect.poll(() => both.getAttribute('aria-pressed')).toBe('true');
    const tr = page.getByRole('complementary', { name: 'Transcript' });
    await tr.locator('.tr-card', { hasText: 'Answer from Auth API' }).first().waitFor();
    expect(await tr.locator('.tr-card', { hasText: 'Asked Auth API' }).count()).toBe(1);
    await shot(page, 'messaging-3-asker');

    // The answerer's route: Frontend's question is a card, and the answer run is one stop.
    await go(page, `#/p/${project.id}/threads/${auth.id}`);
    await page.getByRole('button', { name: /^Stop \d+: Answered Frontend/ }).waitFor({ timeout: 15_000 });
    expect(await tr.locator('.tr-card', { hasText: 'Frontend asked' }).count()).toBe(1);

    // The user asks the done thread; it answers from its context and stays Done.
    const transcript = page.getByRole('complementary', { name: 'Transcript' });
    await transcript.getByLabel('Ask this thread').fill('How long do tokens last?');
    await transcript.getByRole('button', { name: 'Ask', exact: true }).click();
    await page.getByRole('button', { name: /^Stop \d+: You asked/ }).waitFor({ timeout: 15_000 });
    await page.getByRole('button', { name: /^Stop \d+: Answered you/ }).waitFor({ timeout: 15_000 });
    await transcript.getByText('Tokens last 15 minutes.').first().waitFor();
    await expect.poll(() => page.locator('.thread-status-line').first().textContent()).toMatch(/^Done/);
    expect((await client.threads.list(project.id)).find((t) => t.id === auth.id)?.status).toBe('done');
    await shot(page, 'messaging-4-ask');
  });
});
