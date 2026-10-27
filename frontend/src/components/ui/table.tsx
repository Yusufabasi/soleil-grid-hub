import type { ReactNode } from "react";

export type Column<T> = {
  header: ReactNode;
  cell: (row: T) => ReactNode;
  align?: "left" | "right";
  className?: string;
};

/**
 * A table with a caption, typed rows and one render function per column.
 *
 * Not a data grid: no sorting, no virtualisation, no client-side paging. The list endpoints page by
 * cursor and the demo shows the first page with a "load more" affordance where the backend offers
 * one, because pretending to have the whole table would be a different lie than the one a grid's
 * "showing 25 of 4,120" tells.
 */
export function DataTable<T>({
  caption,
  columns,
  rows,
  rowKey,
  empty,
  onRowClick,
}: {
  caption: ReactNode;
  columns: readonly Column<T>[];
  rows: readonly T[];
  rowKey: (row: T) => string;
  empty?: ReactNode;
  onRowClick?: (row: T) => void;
}) {
  if (rows.length === 0) {
    return (
      <p className="rounded-md border border-dashed border-line bg-paper px-4 py-6 text-sm text-muted">
        {empty ?? "Nothing here yet."}
      </p>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr className="border-b border-line text-left">
            {columns.map((column, index) => (
              <th
                key={index}
                scope="col"
                className={`px-3 py-2 text-xs font-semibold tracking-wide text-muted uppercase ${column.align === "right" ? "text-right" : ""} ${column.className ?? ""}`}
              >
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={rowKey(row)}
              className={`border-b border-line/70 last:border-0 ${onRowClick ? "cursor-pointer hover:bg-accent-soft/60" : ""}`}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
            >
              {columns.map((column, index) => (
                <td
                  key={index}
                  className={`px-3 py-2.5 align-top ${column.align === "right" ? "text-right" : ""} ${column.className ?? ""}`}
                >
                  {column.cell(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** A clickable table row needs a real control inside it for the keyboard, so this is the link cell. */
export function RowLink({
  onClick,
  children,
}: {
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={(event) => {
        event.stopPropagation();
        onClick();
      }}
      className="text-left font-medium text-accent underline decoration-dotted underline-offset-2 hover:decoration-solid"
    >
      {children}
    </button>
  );
}
