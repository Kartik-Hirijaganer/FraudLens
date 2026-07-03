/**
 * Summary: Shared pagination footer for list screens. It renders a "showing" summary on
 * the left and, on the right, either Prev/Next page controls (keyset pagination) or a
 * single load-more action — whichever the caller wires up. When a range is supplied it
 * reads "Showing X–Y of Z"; otherwise it falls back to "Showing N of Z".
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - Pagination: render the shown/total copy plus optional Prev/Next or load-more controls.
 *
 * Notes:
 * - Prev/Next take precedence over load-more when both are provided; a missing handler
 *   simply omits that control so the footer degrades gracefully.
 */
import { Button } from "./Button";

interface PaginationProps {
  shown?: number;
  total: number;
  rangeStart?: number;
  rangeEnd?: number;
  hasMore?: boolean;
  onMore?: () => void;
  hasPrev?: boolean;
  hasNext?: boolean;
  onPrev?: () => void;
  onNext?: () => void;
}

function summary(props: PaginationProps): string {
  const { rangeStart, rangeEnd, total, shown } = props;
  if (rangeStart !== undefined && rangeEnd !== undefined) {
    const start = rangeEnd === 0 ? 0 : rangeStart;
    return `Showing ${start.toLocaleString()}–${rangeEnd.toLocaleString()} of ${total.toLocaleString()}`;
  }
  return `Showing ${(shown ?? 0).toLocaleString()} of ${total.toLocaleString()}`;
}

export function Pagination(props: PaginationProps) {
  const { hasMore = false, onMore, hasPrev, hasNext, onPrev, onNext } = props;
  const usePrevNext = Boolean(onPrev || onNext);

  return (
    <div className="gap-md flex flex-wrap items-center justify-between">
      <span className="text-body-sm text-mute">{summary(props)}</span>
      {usePrevNext ? (
        <div className="gap-sm flex items-center">
          <Button variant="secondary" size="sm" onClick={onPrev} disabled={!hasPrev}>
            ← Prev
          </Button>
          <Button variant="secondary" size="sm" onClick={onNext} disabled={!hasNext}>
            Next →
          </Button>
        </div>
      ) : hasMore && onMore ? (
        <Button variant="secondary" onClick={onMore}>
          Load more
        </Button>
      ) : null}
    </div>
  );
}
