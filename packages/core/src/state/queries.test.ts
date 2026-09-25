import { afterEach, describe, expect, it } from 'vitest';
import type { ToolImage } from '@desk/protocol';
import { openDb } from '../db/open';
import { EventStore } from '../events/store';
import { findToolImage } from './queries';

let close: (() => void) | undefined;
afterEach(() => {
  close?.();
  close = undefined;
});

function setup() {
  const opened = openDb(':memory:');
  close = opened.close;
  return new EventStore(opened.db);
}

const image = (n: number, name: string): ToolImage => ({ sha256: String(n).repeat(64), media_type: 'image/png', width: 4, height: 3, bytes: 10, name });

describe('findToolImage', () => {
  it('finds an image a tool result of the project showed, with the name it was last shown under', () => {
    const store = setup();
    const result = (projectId: string, id: string, images: ToolImage[]) =>
      store.append({ project_id: projectId, agent_id: 'a', type: 'tool.result', payload: { run_id: 'r', tool_call_id: id, name: 'view_image', status: 'ok', content: 'x', images } });
    result('p', 'c1', [image(1, 'old.png')]);
    result('p', 'c2', [image(2, 'b.png'), image(1, 'renders/new.png')]);
    result('q', 'c3', [image(3, 'other.png')]);
    expect(findToolImage(store.db, 'p', '1'.repeat(64))).toEqual(image(1, 'renders/new.png'));
    expect(findToolImage(store.db, 'p', '3'.repeat(64))).toBeUndefined();
    expect(findToolImage(store.db, 'p', 'not-a-digest')).toBeUndefined();
  });

  it('searches only the most recent tool results, so a stale or unknown digest never scans the whole history', () => {
    const store = setup();
    const result = (i: number, images?: ToolImage[]) =>
      store.append({ project_id: 'p', agent_id: 'a', type: 'tool.result', payload: { run_id: 'r', tool_call_id: `c${i}`, name: 'read_file', status: 'ok', content: 'x', ...(images ? { images } : {}) } });
    result(0, [image(1, 'early.png')]);
    for (let i = 1; i <= 5; i++) result(i);
    expect(findToolImage(store.db, 'p', '1'.repeat(64), 6)?.name).toBe('early.png');
    expect(findToolImage(store.db, 'p', '1'.repeat(64), 5)).toBeUndefined();
  });
});
