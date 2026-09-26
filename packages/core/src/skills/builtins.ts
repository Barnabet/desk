import { eq } from 'drizzle-orm';
import type { Db } from '../db/open';
import { builtinSkillSettings } from '../db/schema';

/** Whether the user left a built-in skill on (the default). */
export function builtinEnabled(db: Db, name: string): boolean {
  const row = db.select({ enabled: builtinSkillSettings.enabled }).from(builtinSkillSettings).where(eq(builtinSkillSettings.name, name)).get();
  return row?.enabled ?? true;
}
