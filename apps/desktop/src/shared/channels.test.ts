import { describe, expect, it } from 'vitest';
import { INVOKE_CHANNEL, PUSH_CHANNELS } from './channels';

describe('channels', () => {
  it('names the bridge channels', () => {
    expect(INVOKE_CHANNEL).toBe('desk:invoke');
    expect(PUSH_CHANNELS).toEqual(['desk:global', 'desk:event', 'desk:ephemeral', 'desk:navigate']);
  });
});
