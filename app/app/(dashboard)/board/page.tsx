import { prisma } from "@/lib/prisma";
import { createTask } from "./actions";
import TaskCard from "./TaskCard";

const columns = [
  { key: "todo", label: "To Do" },
  { key: "in_progress", label: "In Progress" },
  { key: "done", label: "Done" },
];

export default async function BoardPage() {
  const tasks = await prisma.task.findMany({ orderBy: { createdAt: "asc" } });

  return (
    <div>
      <div className="flex items-start justify-between gap-6">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Planning board</h1>
          <p className="mt-1 text-sm text-slate-500">
            Track software and business tasks from idea to done.
          </p>
        </div>
        <form action={createTask} className="flex gap-2">
          <input
            name="title"
            required
            placeholder="New task title"
            className="w-48 rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
          />
          <input
            name="description"
            placeholder="Details (optional)"
            className="w-56 rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
          />
          <button
            type="submit"
            className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-indigo-500"
          >
            Add task
          </button>
        </form>
      </div>

      <div className="mt-8 grid grid-cols-1 gap-6 sm:grid-cols-3">
        {columns.map((column) => {
          const columnTasks = tasks.filter((t) => t.status === column.key);
          return (
            <div key={column.key} className="rounded-lg bg-slate-100 p-4">
              <div className="mb-3 flex items-center justify-between">
                <h2 className="text-sm font-semibold text-slate-700">{column.label}</h2>
                <span className="rounded-full bg-white px-2 py-0.5 text-xs font-medium text-slate-500">
                  {columnTasks.length}
                </span>
              </div>
              <div className="space-y-3">
                {columnTasks.map((task) => (
                  <TaskCard
                    key={task.id}
                    id={task.id}
                    title={task.title}
                    description={task.description}
                    status={task.status}
                  />
                ))}
                {columnTasks.length === 0 && (
                  <p className="rounded-md border border-dashed border-slate-300 p-4 text-center text-xs text-slate-400">
                    Nothing here
                  </p>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
