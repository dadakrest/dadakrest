import { prisma } from "@/lib/prisma";
import { createProject, deleteProject } from "./actions";
import StatusSelect from "./StatusSelect";

export default async function ProjectsPage() {
  const [projects, clients] = await Promise.all([
    prisma.project.findMany({
      orderBy: { createdAt: "desc" },
      include: { client: true },
    }),
    prisma.client.findMany({ orderBy: { name: "asc" } }),
  ]);

  return (
    <div>
      <h1 className="text-2xl font-bold text-slate-900">Projects</h1>
      <p className="mt-1 text-sm text-slate-500">
        Track engagements in progress for each client.
      </p>

      <div className="mt-8 grid grid-cols-1 gap-8 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <div className="overflow-hidden rounded-lg border border-slate-200 bg-white">
            <table className="min-w-full divide-y divide-slate-200 text-sm">
              <thead className="bg-slate-50">
                <tr>
                  <th className="px-4 py-3 text-left font-semibold text-slate-600">Project</th>
                  <th className="px-4 py-3 text-left font-semibold text-slate-600">Client</th>
                  <th className="px-4 py-3 text-left font-semibold text-slate-600">Status</th>
                  <th className="px-4 py-3" />
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {projects.map((project) => (
                  <tr key={project.id}>
                    <td className="px-4 py-3 font-medium text-slate-900">{project.name}</td>
                    <td className="px-4 py-3 text-slate-600">{project.client.name}</td>
                    <td className="px-4 py-3">
                      <StatusSelect id={project.id} status={project.status} />
                    </td>
                    <td className="px-4 py-3 text-right">
                      <form action={deleteProject}>
                        <input type="hidden" name="id" value={project.id} />
                        <button
                          type="submit"
                          className="text-xs font-medium text-red-600 hover:text-red-800"
                        >
                          Delete
                        </button>
                      </form>
                    </td>
                  </tr>
                ))}
                {projects.length === 0 && (
                  <tr>
                    <td colSpan={4} className="px-4 py-8 text-center text-slate-400">
                      No projects yet.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div className="rounded-lg border border-slate-200 bg-white p-6">
          <h2 className="text-sm font-semibold text-slate-900">Add a project</h2>
          {clients.length === 0 ? (
            <p className="mt-4 text-sm text-slate-500">
              Add a client first before creating a project.
            </p>
          ) : (
            <form action={createProject} className="mt-4 space-y-4">
              <div>
                <label className="block text-xs font-medium text-slate-700">Project name</label>
                <input
                  name="name"
                  required
                  className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-700">Client</label>
                <select
                  name="clientId"
                  required
                  className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                >
                  {clients.map((client) => (
                    <option key={client.id} value={client.id}>
                      {client.name}
                    </option>
                  ))}
                </select>
              </div>
              <button
                type="submit"
                className="w-full rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-indigo-500"
              >
                Add project
              </button>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
