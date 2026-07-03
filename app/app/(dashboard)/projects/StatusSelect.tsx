"use client";

import { updateProjectStatus } from "./actions";

const statusStyles: Record<string, string> = {
  active: "bg-green-50 text-green-700",
  completed: "bg-slate-100 text-slate-600",
  on_hold: "bg-amber-50 text-amber-700",
};

export default function StatusSelect({ id, status }: { id: string; status: string }) {
  return (
    <form action={updateProjectStatus} className="inline">
      <input type="hidden" name="id" value={id} />
      <select
        name="status"
        defaultValue={status}
        onChange={(e) => e.currentTarget.form?.requestSubmit()}
        className={`rounded-full border-0 px-2.5 py-1 text-xs font-medium ${statusStyles[status]}`}
      >
        <option value="active">Active</option>
        <option value="on_hold">On hold</option>
        <option value="completed">Completed</option>
      </select>
    </form>
  );
}
