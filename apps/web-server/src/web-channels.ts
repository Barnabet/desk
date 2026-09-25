import { z } from 'zod';

/** Web-only operations, validated and dispatched by desk web exactly like the bff `channels`. */
export const webChannels = {
  /** Folder names only, under the home folder or a project source, never the Desk data dir (the folder browser). */
  'fs.listDirs': z.object({ path: z.string().min(1).max(4096).optional(), hidden: z.boolean().optional() }),
} as const;

export type WebChannel = keyof typeof webChannels;
export type WebChannelInput<C extends WebChannel> = z.input<(typeof webChannels)[C]>;

/** One folder listing; `parent` is null when the folder above may not be browsed. */
export type DirListing = { path: string; parent: string | null; dirs: Array<{ name: string; path: string }> };

export type WebChannelOutput<C extends WebChannel> = { 'fs.listDirs': DirListing }[C];
