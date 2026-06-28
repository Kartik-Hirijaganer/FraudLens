/**
 * Summary: Shared pagination footer for list screens. Phase 1 uses it only as a
 * reusable primitive; later phases wire it to backend totals and load-more behavior.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - Pagination: render shown/total copy and an optional load-more action.
 *
 * Notes:
 * - The total can equal the shown count until Phase 3 adds backend totals.
 */
import { Button } from "./Button";

interface PaginationProps {
  shown: number;
  total: number;
  hasMore?: boolean;
  onMore?: () => void;
}

export function Pagination({ shown, total, hasMore = false, onMore }: PaginationProps) {
  return (
    <div className="gap-md flex flex-wrap items-center justify-between">
      <span className="text-caption text-mute">
        Showing {shown} of {total}
      </span>
      {hasMore && onMore ? (
        <Button variant="secondary" onClick={onMore}>
          Load more
        </Button>
      ) : null}
    </div>
  );
}
