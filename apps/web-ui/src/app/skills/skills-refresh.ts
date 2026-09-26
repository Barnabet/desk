/**
 * The skills screen, as its panels and sheets see it. A delete, restore, copy, switch or catalog install that lands after
 * its panel, group or review sheet closed (the route left it while the call ran) cannot emit `changed` any more, so the
 * component tells the screen through this instead, and the new, removed or switched skill still shows (SkillsScreen
 * provides it).
 */
export abstract class SkillsRefresh {
  /** List the skills, the catalog and the built-ins again. */
  abstract changed(): void;
}
