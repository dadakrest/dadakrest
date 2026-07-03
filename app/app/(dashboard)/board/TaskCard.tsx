"use client";

import { moveTask, deleteTask } from "./actions";

const ORDER = ["todo", "in_progress", "done"];

export default function TaskCard({
  id,
  title,
  description,
  status,
}: {
  id: string;
  title: string;
  description: string | null;
  status: string;
}) {
  const index = ORDER.indexOf(status);

  async function move(direction: -1 | 1) {
    const target = ORDER[index + direction];
    if (!target) return;
    const formData = new FormData();
    formData.set("id", id);
    formData.set("status", target);
    await moveTask(formData);
  }

  return (
    <div className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
      <p className="text-sm font-medium text-slate-900">{title}</p>
      {description && (
        <p className="mt-1 text-xs leading-5 text-slate-500">{description}</p>
      )}
      <div className="mt-3 flex items-center justify-between">
        <div className="flex gap-1">
          <button
            type="button"
            disabled={index === 0}
            onClick={() => move(-1)}
            className="rounded border border-slate-200 px-2 py-1 text-xs text-slate-600 disabled:opacity-30 hover:bg-slate-50"
          >
            &larr;
          </button>
          <button
            type="button"
            disabled={index === ORDER.length - 1}
            onClick={() => move(1)}
            className="rounded border border-slate-200 px-2 py-1 text-xs text-slate-600 disabled:opacity-30 hover:bg-slate-50"
          >
            &rarr;
          </button>
        </div>
        <form action={deleteTask}>
          <input type="hidden" name="id" value={id} />
          <button type="submit" className="text-xs font-medium text-red-600 hover:text-red-800">
            Delete
          </button>
        </form>
      </div>
    </div>
  );
}
