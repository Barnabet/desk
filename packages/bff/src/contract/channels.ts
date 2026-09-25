/** The single renderer → main channel; the first argument names the operation (see ipc.ts). */
export const INVOKE_CHANNEL = 'desk:invoke';

/** Main → renderer pushes: global state, project events, streamed text, and navigation requests. */
export const pushChannels = ['desk:global', 'desk:event', 'desk:events', 'desk:ephemeral', 'desk:navigate'] as const;
export type PushChannel = (typeof pushChannels)[number];
