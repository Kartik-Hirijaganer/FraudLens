/**
 * Summary: Generic, token-only data table primitive. It owns the shared table
 * chrome, empty slot, row click handling, and action-cell isolation used by alerts
 * and transactions so pages define only columns and row data.
 *
 * Key classes:
 * - Column: describes one rendered table column.
 *
 * Key functions:
 * - DataTable: render accessible tabular data with optional clickable rows.
 *
 * Notes:
 * - Buttons/links inside cells keep their native behavior; row activation is scoped
 *   to row clicks and keyboard activation on the row itself.
 */
import type { KeyboardEvent, ReactNode } from "react";

import { cx } from "../../lib/cx";

export interface Column<T> {
  id: string;
  header: ReactNode;
  cell: (row: T) => ReactNode;
  align?: "left" | "right";
  srOnlyHeader?: boolean;
}

interface DataTableProps<T> {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  onRowClick?: (row: T) => void;
  empty?: ReactNode;
  caption: string;
}

function isInteractiveTarget(target: EventTarget): boolean {
  return target instanceof Element && Boolean(target.closest("a,button,input,select,textarea"));
}

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  onRowClick,
  empty,
  caption,
}: DataTableProps<T>) {
  if (rows.length === 0 && empty) {
    return <>{empty}</>;
  }

  function activateRow(row: T, eventTarget: EventTarget): void {
    if (onRowClick && !isInteractiveTarget(eventTarget)) {
      onRowClick(row);
    }
  }

  function onKeyDown(row: T, event: KeyboardEvent<HTMLTableRowElement>): void {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      activateRow(row, event.target);
    }
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-left">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr className="bg-canvas-soft text-caption text-mute">
            {columns.map((column) => (
              <th
                key={column.id}
                scope="col"
                className={cx(
                  "px-lg py-md font-semibold first:rounded-l-md last:rounded-r-md",
                  column.align === "right" ? "text-right" : "text-left",
                )}
              >
                <span className={column.srOnlyHeader ? "sr-only" : undefined}>{column.header}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const clickable = Boolean(onRowClick);
            return (
              <tr
                key={rowKey(row)}
                tabIndex={clickable ? 0 : undefined}
                onClick={(event) => activateRow(row, event.target)}
                onKeyDown={clickable ? (event) => onKeyDown(row, event) : undefined}
                className={cx(
                  "border-canvas-soft border-t",
                  clickable ? "hover:bg-canvas-soft focus:bg-canvas-soft cursor-pointer" : "",
                )}
              >
                {columns.map((column) => (
                  <td
                    key={column.id}
                    className={cx(
                      "px-lg py-md align-middle text-body-sm",
                      column.align === "right" ? "text-right" : "text-left",
                    )}
                  >
                    {column.cell(row)}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
