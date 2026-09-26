import { render, screen } from '@testing-library/angular';
import { beforeEach, describe, expect, it } from 'vitest';
import type { ToolCallView } from '@desk/client';
import type { ToolImage } from '@desk/protocol';
import { FakeDeskBridge } from '../testing/fake-bridge';
import { clearAttachmentCache } from './image-thumbs';
import { ToolGroup } from './tool-group';

beforeEach(() => clearAttachmentCache());

const call = (id: string, name: string, args: unknown): ToolCallView => ({
  id,
  name,
  arguments: typeof args === 'string' ? args : JSON.stringify(args),
  status: 'ok',
  content: null,
});
/** Each call's line: its name, then its arguments on one line. */
const lines = () => [...document.querySelectorAll('.toolgroup li .mono')].map((el) => el.textContent);

describe('ToolGroup', () => {
  it('names a thread by the title titleOf gives, and summarises everything else as before', async () => {
    const calls = [
      call('1', 'read_thread', { thread_id: 'a', mode: 'full' }),
      call('2', 'stop_thread', { thread_id: 'a', reason: 'Duplicate work' }),
      call('3', 'review_diff', { thread_id: 'zz' }),
      call('4', 'read_file', { path: 'notes.md' }),
      call('5', 'bash', 'not json'),
    ];
    await render(ToolGroup, { inputs: { calls, titleOf: (id: string) => (id === 'a' ? 'Auth API' : undefined) }, providers: new FakeDeskBridge().providers });
    expect(lines()).toEqual(['read_thread Auth API', 'stop_thread Auth API', 'review_diff zz', 'read_file notes.md', 'bash not json']);
  });

  it('shows the arguments unchanged without titleOf', async () => {
    await render(ToolGroup, { inputs: { calls: [call('1', 'read_thread', { thread_id: 'a' })] }, providers: new FakeDeskBridge().providers });
    expect(lines()).toEqual(['read_thread a']);
  });

  it('says Using while a call runs, opens when asked, and shows the images the calls looked at below it', async () => {
    const shot: ToolImage = { sha256: 'c'.repeat(64), media_type: 'image/png', width: 10, height: 10, bytes: 100, name: 'shots/home.png' };
    const calls: ToolCallView[] = [
      { ...call('1', 'view_image', { paths: ['shots/home.png'] }), status: 'running', images: [shot] },
      call('2', 'read_file', { path: 'a.md' }),
      call('3', 'read_file', { path: 'b.md' }),
    ];
    await render(ToolGroup, { inputs: { calls, defaultOpen: true }, providers: new FakeDeskBridge().providers });
    const details = document.querySelector('details.toolgroup')!;
    expect(details.hasAttribute('open')).toBe(true);
    expect(details.querySelector('.toolgroup-title')!.textContent).toBe('Using 3 tools');
    expect(details.querySelector('.toolgroup-names')!.textContent).toBe('view_image · read_file ×2');
    expect([...details.querySelectorAll('.tool-status')].map((s) => [s.className, s.textContent])).toEqual([
      ['tool-status tool-status-running', 'running'],
      ['tool-status tool-status-ok', 'ok'],
      ['tool-status tool-status-ok', 'ok'],
    ]);
    // The thumbnails sit after the group, so they show while it is closed.
    expect(details.nextElementSibling!.className).toBe('image-thumbs');
    expect(screen.getByRole('button', { name: 'Open home.png' })).toBeTruthy();
  });
});
