import { eq } from 'drizzle-orm';
import type { PlanItem } from '@desk/protocol';
import type { Db } from '../db/open';
import { plans } from '../db/schema';

export type PlanRow = typeof plans.$inferSelect;

export const getPlan = (db: Db, projectId: string): PlanRow | undefined => db.select().from(plans).where(eq(plans.project_id, projectId)).get();

export function formatPlan(items: PlanItem[]): string {
  if (!items.length) return '(no plan yet)';
  const mark = { todo: '[ ]', in_progress: '[~]', done: '[x]', dropped: '[-]' } as const;
  return items
    .map((i) => `${mark[i.status]} ${i.title} (${i.id})${i.thread_ids.length ? ` — threads: ${i.thread_ids.join(', ')}` : ''}${i.notes ? ` — ${i.notes}` : ''}`)
    .join('\n');
}
