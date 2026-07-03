import { prisma } from "@/lib/prisma";
import { createContract, deleteContract } from "./actions";
import StatusSelect from "./StatusSelect";

export default async function ContractsPage() {
  const [contracts, clients] = await Promise.all([
    prisma.contract.findMany({
      orderBy: { effectiveDate: "desc" },
      include: { client: true },
    }),
    prisma.client.findMany({ orderBy: { name: "asc" } }),
  ]);

  return (
    <div>
      <h1 className="text-2xl font-bold text-slate-900">Contracts</h1>
      <p className="mt-1 text-sm text-slate-500">
        Store agreements and the specific clauses/terms attached to each client.
      </p>

      <div className="mt-8 grid grid-cols-1 gap-8 lg:grid-cols-3">
        <div className="lg:col-span-2 space-y-4">
          {contracts.map((contract) => (
            <div key={contract.id} className="rounded-lg border border-slate-200 bg-white p-6">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <h2 className="font-semibold text-slate-900">{contract.title}</h2>
                  <p className="text-xs text-slate-500">
                    {contract.client.name} &middot; effective{" "}
                    {contract.effectiveDate.toLocaleDateString()}
                  </p>
                </div>
                <div className="flex items-center gap-3">
                  <StatusSelect id={contract.id} status={contract.status} />
                  <form action={deleteContract}>
                    <input type="hidden" name="id" value={contract.id} />
                    <button
                      type="submit"
                      className="text-xs font-medium text-red-600 hover:text-red-800"
                    >
                      Delete
                    </button>
                  </form>
                </div>
              </div>
              <p className="mt-4 whitespace-pre-wrap text-sm leading-6 text-slate-600">
                {contract.clauses}
              </p>
            </div>
          ))}
          {contracts.length === 0 && (
            <div className="rounded-lg border border-dashed border-slate-300 p-8 text-center text-slate-400">
              No contracts yet.
            </div>
          )}
        </div>

        <div className="rounded-lg border border-slate-200 bg-white p-6">
          <h2 className="text-sm font-semibold text-slate-900">Add a contract</h2>
          {clients.length === 0 ? (
            <p className="mt-4 text-sm text-slate-500">
              Add a client first before creating a contract.
            </p>
          ) : (
            <form action={createContract} className="mt-4 space-y-4">
              <div>
                <label className="block text-xs font-medium text-slate-700">Title</label>
                <input
                  name="title"
                  required
                  placeholder="Master Services Agreement"
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
              <div>
                <label className="block text-xs font-medium text-slate-700">
                  Clauses / terms
                </label>
                <textarea
                  name="clauses"
                  required
                  rows={6}
                  placeholder="1. Scope of work...&#10;2. Payment terms...&#10;3. Confidentiality..."
                  className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                />
              </div>
              <button
                type="submit"
                className="w-full rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-indigo-500"
              >
                Save contract
              </button>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
