import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { TemplateForkModal } from "@/components/widgets/TemplateForkModal";

const files = [
  { id: "file-1", original_filename: "orders.csv" },
  { id: "file-2", original_filename: "customers.csv" },
] as never;

function mockFetchOnce(payload: unknown, status = 200) {
  (global.fetch as unknown as ReturnType<typeof vi.fn>) = vi.fn(async () => ({
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload,
    text: async () => JSON.stringify(payload),
  })) as unknown as typeof fetch;
}

describe("TemplateForkModal", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("loads template placeholders and submits file mappings", async () => {
    mockFetchOnce({
      id: "sales_revenue_report",
      name: "Sales Report",
      description: "d",
      category: "c",
      required_files: [
        { placeholder: "orders_file_id" },
        { placeholder: "customers_file_id" },
      ],
      yaml_config: "pipeline:\n  name: x\n",
    });

    const onApply = vi.fn();
    render(
      <TemplateForkModal
        templateId="sales_revenue_report"
        availableFiles={files}
        onApply={onApply}
        onClose={() => {}}
      />
    );

    await waitFor(() => {
      expect(screen.getByText(/orders_file_id/)).toBeDefined();
    });

    const selects = screen.getAllByRole("combobox");
    expect(selects).toHaveLength(2);
    fireEvent.change(selects[0], { target: { value: "file-1" } });
    fireEvent.change(selects[1], { target: { value: "file-2" } });
    fireEvent.change(screen.getByDisplayValue("Sales_Report"), {
      target: { value: "my_sales" },
    });

    mockFetchOnce({ pipeline_name: "my_sales", yaml: "pipeline:\n  name: my_sales\n", template_id: "sales_revenue_report" });
    fireEvent.click(screen.getByRole("button", { name: "Use template" }));

    await waitFor(() => {
      expect(onApply).toHaveBeenCalledTimes(1);
    });
    expect(onApply.mock.calls[0][1]).toBe("my_sales");
    expect(onApply.mock.calls[0][0]).toContain("my_sales");
  });

  it("stays disabled until every placeholder has a file", async () => {
    mockFetchOnce({
      id: "t",
      name: "T",
      description: "d",
      category: "c",
      required_files: [{ placeholder: "a" }, { placeholder: "b" }],
      yaml_config: "pipeline:\n  name: x\n",
    });

    render(
      <TemplateForkModal
        templateId="t"
        availableFiles={files}
        onApply={() => {}}
        onClose={() => {}}
      />
    );

    await waitFor(() => {
      expect(screen.getAllByRole("combobox")).toHaveLength(2);
    });

    const button = screen.getByRole("button", { name: "Use template" }).closest("button");
    expect(button?.hasAttribute("disabled")).toBe(true);

    const selects = screen.getAllByRole("combobox");
    fireEvent.change(selects[0], { target: { value: "file-1" } });
    expect(screen.getByRole("button", { name: "Use template" }).closest("button")?.hasAttribute("disabled")).toBe(true);
    fireEvent.change(selects[1], { target: { value: "file-2" } });
    expect(screen.getByRole("button", { name: "Use template" }).closest("button")?.hasAttribute("disabled")).toBe(false);
  });
});
