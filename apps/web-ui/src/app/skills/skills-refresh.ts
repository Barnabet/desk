/**
 * The skills screen, as its panels see it. A delete, restore or copy that lands after its panel closed (the route left
 * the skill while the call ran) cannot emit `changed` any more, so the panel tells the screen through this instead, and
 * the new or removed skill still shows (SkillsScreen provides it).
 */
export abstract class SkillsRefresh {
  /** List the skills, the catalog and the built-ins again. */
  abstract changed(): void;
}
