/**
 * EmbeddingBanner - Lightweight chat-area notification
 *
 * Displayed at the top of the chat panel when embedding rebuild
 * is in progress or incomplete. Auto-hides when all_done.
 */

import { useEffect } from 'react';
import { useEmbeddingStore } from '@/stores/embeddingStore';
import { cn } from '@/lib/utils';

export function EmbeddingBanner() {
  const { status, fetchStatus, startPolling } = useEmbeddingStore();

  useEffect(() => {
    fetchStatus();
    startPolling();
    return () => useEmbeddingStore.getState().stopPolling();
  }, []);

  // Don't render if no status or all done
  if (!status || status.all_done) return null;

  const { migration } = status;
  const isRebuilding = migration.is_running;
  const totalMissing = Object.values(status.stats).reduce((sum, s) => sum + s.missing, 0);

  if (totalMissing === 0 && !isRebuilding) return null;

  return (
    <div
      className={cn(
        'px-4 py-1.5 flex items-center gap-2 text-[11px] border-b',
        'bg-[var(--color-warning)]/5 border-[var(--color-warning)]/20 text-[var(--color-warning)]'
      )}
    >
      {isRebuilding ? (
        <div className="w-1.5 h-1.5 rounded-full bg-[var(--color-warning)] animate-pulse" />
      ) : (
        <div className="w-1.5 h-1.5 rounded-full bg-[var(--color-warning)]" />
      )}
      <span>
        {isRebuilding
          ? `Rebuilding vector index (${migration.progress_pct}%)... History search may be incomplete.`
          : `Vector index incomplete (${totalMissing} missing). History search may be incomplete.`}
      </span>
    </div>
  );
}
