/**
 * @file_name: CostPopover.tsx
 * @author: Bin Liang
 * @date: 2026-03-12
 * @description: Token usage popover - shows LLM API token consumption summary
 *
 * Displays a small activity button in the header.
 * Click to open a popover with total tokens, per-model breakdown, and daily trend.
 */

import { useState } from 'react';
import { Activity, RefreshCw } from 'lucide-react';
import { Popover, PopoverTrigger, PopoverContent } from '@/components/ui/popover';
import { Button } from '@/components/ui';
import { usePreloadStore, useConfigStore } from '@/stores';
import { cn } from '@/lib/utils';
import type { CostSummary } from '@/types/api';

/** Format token count (e.g. 12345 -> "12.3k") */
function formatTokens(n: number): string {
  if (n < 1000) return n.toString();
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(2)}M`;
}

/** Short model name for display (drop date suffixes) */
function shortModelName(model: string): string {
  if (model === 'claude-code') return 'Claude Code';
  return model.replace(/-\d{4}-?\d{2}-?\d{2}$/, '').replace(/-\d{8}$/, '');
}

function SummaryContent({ summary }: { summary: CostSummary }) {
  const totalTokens = summary.total_input_tokens + summary.total_output_tokens;
  const models = Object.entries(summary.by_model).sort(
    ([, a], [, b]) => (b.input_tokens + b.output_tokens) - (a.input_tokens + a.output_tokens)
  );

  return (
    <div className="space-y-3">
      {/* Total */}
      <div className="text-center pb-2 border-b border-[var(--border-subtle)]">
        <div className="text-2xl font-bold text-[var(--text-primary)]">
          {formatTokens(totalTokens)}
        </div>
        <div className="text-[10px] text-[var(--text-tertiary)] mt-0.5">
          {formatTokens(summary.total_input_tokens)} in / {formatTokens(summary.total_output_tokens)} out
        </div>
      </div>

      {/* Per-model breakdown */}
      {models.length > 0 && (
        <div className="space-y-1.5">
          <div className="text-[10px] font-medium text-[var(--text-tertiary)] uppercase tracking-wider">
            By Model
          </div>
          {models.map(([model, data]) => (
            <div key={model} className="flex items-center justify-between text-xs">
              <span className="text-[var(--text-secondary)] truncate max-w-[140px]" title={model}>
                {shortModelName(model)}
              </span>
              <div className="flex items-center gap-2">
                <span className="text-[10px] text-[var(--text-tertiary)]">
                  x{data.call_count}
                </span>
                <span className="font-medium text-[var(--text-primary)] min-w-[50px] text-right">
                  {formatTokens(data.input_tokens + data.output_tokens)}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Daily trend (last few days) */}
      {summary.daily.length > 0 && (
        <div className="space-y-1.5 pt-1 border-t border-[var(--border-subtle)]">
          <div className="text-[10px] font-medium text-[var(--text-tertiary)] uppercase tracking-wider">
            Daily
          </div>
          {summary.daily.slice(-5).map((entry) => (
            <div key={entry.date} className="flex items-center justify-between text-xs">
              <span className="text-[var(--text-tertiary)]">{entry.date.slice(5)}</span>
              <span className="font-medium text-[var(--text-primary)]">
                {formatTokens(entry.input_tokens + entry.output_tokens)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function CostPopover() {
  const [isRefreshing, setIsRefreshing] = useState(false);
  const { agentId } = useConfigStore();
  const { costSummary, costLoading, refreshCost } = usePreloadStore();

  const handleRefresh = async () => {
    setIsRefreshing(true);
    try {
      await refreshCost(agentId);
    } finally {
      setIsRefreshing(false);
    }
  };

  const totalTokens = costSummary
    ? costSummary.total_input_tokens + costSummary.total_output_tokens
    : 0;

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="icon" className="relative" title="Token Usage">
          <Activity className="w-5 h-5" />
          {totalTokens > 0 && (
            <span className="absolute -top-1 -right-1 h-4 min-w-4 px-0.5 flex items-center justify-center text-[9px] font-medium bg-[var(--accent-primary)] text-white rounded-full">
              {formatTokens(totalTokens)}
            </span>
          )}
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        sideOffset={8}
        className="w-[260px] p-3 bg-[var(--bg-primary)] border border-[var(--border-default)] rounded-xl shadow-lg"
      >
        {/* Header */}
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs font-medium text-[var(--text-secondary)]">
            Token Usage (7d)
          </span>
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6"
            onClick={handleRefresh}
            disabled={isRefreshing || costLoading}
          >
            <RefreshCw className={cn('w-3 h-3', (isRefreshing || costLoading) && 'animate-spin')} />
          </Button>
        </div>

        {/* Content */}
        {costLoading && !costSummary ? (
          <div className="py-4 text-center text-xs text-[var(--text-tertiary)]">Loading...</div>
        ) : costSummary ? (
          <SummaryContent summary={costSummary} />
        ) : (
          <div className="py-4 text-center text-xs text-[var(--text-tertiary)]">
            No usage data yet
          </div>
        )}
      </PopoverContent>
    </Popover>
  );
}
