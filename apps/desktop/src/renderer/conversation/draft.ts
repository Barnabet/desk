/** Where the conversation composer keeps a project's unsent draft (the web UI uses the same key). */
export const draftKey = (projectId: string): string => `desk.draft.${projectId}`;

/** Puts text in a project's composer (after an unsent draft, on a new paragraph). The conversation reads it on mount. */
export function primeDraft(projectId: string, text: string): void {
  try {
    const current = localStorage.getItem(draftKey(projectId)) ?? '';
    localStorage.setItem(draftKey(projectId), current.trim() ? `${current.trimEnd()}\n\n${text}` : text);
  } catch {
    // Drafts are a convenience.
  }
}
