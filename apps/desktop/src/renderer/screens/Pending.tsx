import { EmptyState } from '../components/EmptyState';

export function Pending({ title }: { title: string }) {
  return (
    <div className="page">
      <EmptyState title={title}>This screen is on its way in the next build.</EmptyState>
    </div>
  );
}
