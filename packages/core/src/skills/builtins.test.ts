import { GLOBAL_PROJECT_ID } from '@desk/protocol';
import { describe, expect, it } from 'vitest';
import { openDb } from '../db/open';
import { EventStore } from '../events/store';
import { builtinEnabled } from './builtins';

describe('built-in skill switch', () => {
  it('is on by default and follows the latest toggle', () => {
    const { db, close } = openDb(':memory:');
    const store = new EventStore(db);
    expect(builtinEnabled(db, 'pdf-toolkit')).toBe(true);
    store.append({ project_id: GLOBAL_PROJECT_ID, agent_id: null, type: 'skill.builtin_toggled', payload: { name: 'pdf-toolkit', enabled: false } });
    expect(builtinEnabled(db, 'pdf-toolkit')).toBe(false);
    store.append({ project_id: GLOBAL_PROJECT_ID, agent_id: null, type: 'skill.builtin_toggled', payload: { name: 'pdf-toolkit', enabled: true } });
    expect(builtinEnabled(db, 'pdf-toolkit')).toBe(true);
    expect(builtinEnabled(db, 'images')).toBe(true);
    close();
  });
});
