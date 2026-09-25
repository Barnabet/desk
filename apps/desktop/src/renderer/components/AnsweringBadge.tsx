/** "answering Frontend" with a live dot: a thread's answer run in progress. Its status chip does not change (design spec §8 item 3). */
export function AnsweringBadge({ label }: { label: string }) {
  return (
    <span className="answering-badge">
      <span className="live-dot" aria-hidden="true" />
      {label}
    </span>
  );
}
