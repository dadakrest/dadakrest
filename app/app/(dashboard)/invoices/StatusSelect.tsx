"use client";

import { updateInvoiceStatus } from "./actions";

const statusStyles: Record<string, string> = {
  unpaid: "bg-amber-50 text-amber-700",
  paid: "bg-green-50 text-green-700",
  overdue: "bg-red-50 text-red-700",
};

export default function StatusSelect({ id, status }: { id: string; status: string }) {
  return (
    <form action={updateInvoiceStatus} className="inline">
      <input type="hidden" name="id" value={id} />
      <select
        name="status"
        defaultValue={status}
        onChange={(e) => e.currentTarget.form?.requestSubmit()}
        className={`rounded-full border-0 px-2.5 py-1 text-xs font-medium ${statusStyles[status]}`}
      >
        <option value="unpaid">Unpaid</option>
        <option value="paid">Paid</option>
        <option value="overdue">Overdue</option>
      </select>
    </form>
  );
}
