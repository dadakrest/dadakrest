import { prisma } from "@/lib/prisma";
import { createInvoice, deleteInvoice } from "./actions";
import StatusSelect from "./StatusSelect";

function formatCurrency(amount: number) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(amount);
}

export default async function InvoicesPage() {
  const [invoices, clients] = await Promise.all([
    prisma.invoice.findMany({
      orderBy: { issueDate: "desc" },
      include: { client: true },
    }),
    prisma.client.findMany({ orderBy: { name: "asc" } }),
  ]);

  return (
    <div>
      <h1 className="text-2xl font-bold text-slate-900">Invoices</h1>
      <p className="mt-1 text-sm text-slate-500">
        Bill clients and track payment status.
      </p>

      <div className="mt-8 grid grid-cols-1 gap-8 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <div className="overflow-hidden rounded-lg border border-slate-200 bg-white">
            <table className="min-w-full divide-y divide-slate-200 text-sm">
              <thead className="bg-slate-50">
                <tr>
                  <th className="px-4 py-3 text-left font-semibold text-slate-600">Number</th>
                  <th className="px-4 py-3 text-left font-semibold text-slate-600">Client</th>
                  <th className="px-4 py-3 text-left font-semibold text-slate-600">Amount</th>
                  <th className="px-4 py-3 text-left font-semibold text-slate-600">Due</th>
                  <th className="px-4 py-3 text-left font-semibold text-slate-600">Status</th>
                  <th className="px-4 py-3" />
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {invoices.map((invoice) => (
                  <tr key={invoice.id}>
                    <td className="px-4 py-3 font-medium text-slate-900">{invoice.number}</td>
                    <td className="px-4 py-3 text-slate-600">{invoice.client.name}</td>
                    <td className="px-4 py-3 text-slate-600">{formatCurrency(invoice.amount)}</td>
                    <td className="px-4 py-3 text-slate-600">
                      {invoice.dueDate.toLocaleDateString()}
                    </td>
                    <td className="px-4 py-3">
                      <StatusSelect id={invoice.id} status={invoice.status} />
                    </td>
                    <td className="px-4 py-3 text-right">
                      <form action={deleteInvoice}>
                        <input type="hidden" name="id" value={invoice.id} />
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
                {invoices.length === 0 && (
                  <tr>
                    <td colSpan={6} className="px-4 py-8 text-center text-slate-400">
                      No invoices yet.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div className="rounded-lg border border-slate-200 bg-white p-6">
          <h2 className="text-sm font-semibold text-slate-900">Create an invoice</h2>
          {clients.length === 0 ? (
            <p className="mt-4 text-sm text-slate-500">
              Add a client first before creating an invoice.
            </p>
          ) : (
            <form action={createInvoice} className="mt-4 space-y-4">
              <div>
                <label className="block text-xs font-medium text-slate-700">Invoice number</label>
                <input
                  name="number"
                  required
                  placeholder="INV-1001"
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
                <label className="block text-xs font-medium text-slate-700">Amount (USD)</label>
                <input
                  name="amount"
                  type="number"
                  min="0"
                  step="0.01"
                  required
                  className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-700">Due date</label>
                <input
                  name="dueDate"
                  type="date"
                  required
                  className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                />
              </div>
              <button
                type="submit"
                className="w-full rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-indigo-500"
              >
                Create invoice
              </button>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
