import { describe, expect, it } from 'vitest';
import type { DeskClient } from '@desk/client';
import { channels, type Channel, type ChannelOutput, type ChannelOutputs, type DaemonStatus, type GlobalState } from './index';

/** True only when A and B are the same type (the typecheck enforces the `true` literals below). */
type Equal<A, B> = (<T>() => T extends A ? 1 : 2) extends <T>() => T extends B ? 1 : 2 ? true : false;

describe('ChannelOutput', () => {
  it('names an output for every operation and nothing else', () => {
    const exact: Equal<keyof ChannelOutputs, Channel> = true;
    expect(exact).toBe(true);
    expect(Object.keys(channels)).toHaveLength(81);
  });

  it("passes deskd's results through and names the host's", () => {
    const checks: [
      Equal<ChannelOutput<'projects.get'>, Awaited<ReturnType<DeskClient['projects']['get']>>>,
      Equal<ChannelOutput<'threads.file'>, Uint8Array>,
      Equal<ChannelOutput<'broker.snapshot'>, GlobalState>,
      Equal<ChannelOutput<'daemon.status'>, DaemonStatus>,
      Equal<ChannelOutput<'app.pickFolder'>, string | null>,
      Equal<ChannelOutput<'attachments.get'>, string>,
    ] = [true, true, true, true, true, true];
    expect(checks.every(Boolean)).toBe(true);
  });
});
