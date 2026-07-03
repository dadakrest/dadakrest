"use client";

import { updateContractStatus } from "./actions";

const statusStyles: Record<string, string> = {
  draft: "bg-slate-100 text-slate-600",
  active: "bg-green-50 text-green-700",
  expired: "bg-red-50 text-red-700",
};

export default function StatusSelect({ id, status }: { id: string; status: string }) {
  return (
    <form action={updateContractStatus} className="inline">
      <input type="hidden" name="id" value={id} />
      <select
        name="status"
        defaultValue={status}
        onChange={(e) => e.currentTarget.form?.requestSubmit()}
        className={`rounded-full border-0 px-2.5 py-1 text-xs font-medium ${statusStyles[status]}`}
      >
        <option value="draft">Draft</option>
        <option value="active">Active</option>
        <option value="expired">Expired</option>
      </select>
    </form>
  );
}
