import { describe, expect, it } from 'vitest';
import { INVOKE_CHANNEL, pushChannels } from './channels';

describe('channels', () => {
  it('names the bridge channels', () => {
    expect(INVOKE_CHANNEL).toBe('desk:invoke');
    expect(pushChannels).toEqual(['desk:global', 'desk:event', 'desk:events', 'desk:ephemeral', 'desk:navigate']);
  });
});
