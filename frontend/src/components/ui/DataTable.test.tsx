import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { DataTable, type Column } from "./DataTable";

interface Row {
  id: string;
  name: string;
}

const rows: Row[] = [{ id: "r1", name: "First row" }];

const columns: Column<Row>[] = [
  { id: "name", header: "Name", cell: (row) => row.name },
  {
    id: "action",
    header: "Action",
    srOnlyHeader: true,
    cell: () => <button type="button">Act</button>,
  },
];

describe("DataTable", () => {
  it("renders the empty slot", () => {
    render(
      <DataTable
        caption="Rows"
        columns={columns}
        rows={[]}
        rowKey={(row) => row.id}
        empty={<p>No rows</p>}
      />,
    );
    expect(screen.getByText("No rows")).toBeInTheDocument();
  });

  it("activates rows by click and keyboard", () => {
    const onRowClick = vi.fn();
    render(
      <DataTable
        caption="Rows"
        columns={columns}
        rows={rows}
        rowKey={(row) => row.id}
        onRowClick={onRowClick}
      />,
    );
    const row = screen.getByText("First row").closest("tr");
    expect(row).not.toBeNull();
    fireEvent.click(row!);
    fireEvent.keyDown(row!, { key: "Enter" });
    expect(onRowClick).toHaveBeenCalledTimes(2);
    expect(onRowClick).toHaveBeenCalledWith(rows[0]);
  });

  it("does not trigger row click from an action cell button", async () => {
    const onRowClick = vi.fn();
    render(
      <DataTable
        caption="Rows"
        columns={columns}
        rows={rows}
        rowKey={(row) => row.id}
        onRowClick={onRowClick}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Act" }));
    expect(onRowClick).not.toHaveBeenCalled();
  });
});

it("preserves native button keyboard activation without firing the row", async () => {
  const action = vi.fn();
  const rowAction = vi.fn();
  render(
    <DataTable
      caption="Rows"
      columns={[
        {
          id: "action",
          header: "Action",
          cell: () => (
            <button type="button" onClick={action}>
              Act
            </button>
          ),
        },
      ]}
      rows={rows}
      rowKey={(row) => row.id}
      onRowClick={rowAction}
    />,
  );
  const user = userEvent.setup();
  await user.tab();
  await user.tab();
  await user.keyboard("{Enter} ");
  expect(action).toHaveBeenCalledTimes(2);
  expect(rowAction).not.toHaveBeenCalled();
});
