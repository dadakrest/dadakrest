import { prisma } from "@/lib/prisma";
import { createExpense, deleteExpense } from "./actions";

function formatCurrency(amount: number) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(amount);
}

export default async function FinancePage() {
  const [invoices, expenses] = await Promise.all([
    prisma.invoice.findMany(),
    prisma.expense.findMany({ orderBy: { date: "desc" } }),
  ]);

  const paidRevenue = invoices
    .filter((i) => i.status === "paid")
    .reduce((sum, i) => sum + i.amount, 0);
  const outstandingRevenue = invoices
    .filter((i) => i.status !== "paid")
    .reduce((sum, i) => sum + i.amount, 0);
  const totalExpenses = expenses.reduce((sum, e) => sum + e.amount, 0);
  const netIncome = paidRevenue - totalExpenses;

  const expensesByCategory = expenses.reduce<Record<string, number>>((acc, e) => {
    acc[e.category] = (acc[e.category] ?? 0) + e.amount;
    return acc;
  }, {});

  return (
    <div>
      <h1 className="text-2xl font-bold text-slate-900">Finance</h1>
      <p className="mt-1 text-sm text-slate-500">
        Company economics: revenue collected, outstanding, and expenses.
      </p>

      <div className="mt-8 grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-4">
        <div className="rounded-lg border border-slate-200 bg-white p-6">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
            Revenue collected
          </p>
          <p className="mt-2 text-2xl font-bold text-green-600">
            {formatCurrency(paidRevenue)}
          </p>
        </div>
        <div className="rounded-lg border border-slate-200 bg-white p-6">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
            Outstanding
          </p>
          <p className="mt-2 text-2xl font-bold text-amber-600">
            {formatCurrency(outstandingRevenue)}
          </p>
        </div>
        <div className="rounded-lg border border-slate-200 bg-white p-6">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
            Total expenses
          </p>
          <p className="mt-2 text-2xl font-bold text-red-600">
            {formatCurrency(totalExpenses)}
          </p>
        </div>
        <div className="rounded-lg border border-slate-200 bg-white p-6">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
            Net income
          </p>
          <p className={`mt-2 text-2xl font-bold ${netIncome >= 0 ? "text-slate-900" : "text-red-600"}`}>
            {formatCurrency(netIncome)}
          </p>
        </div>
      </div>

      {Object.keys(expensesByCategory).length > 0 && (
        <div className="mt-8 rounded-lg border border-slate-200 bg-white p-6">
          <h2 className="text-sm font-semibold text-slate-900">Expenses by category</h2>
          <div className="mt-4 space-y-3">
            {Object.entries(expensesByCategory)
              .sort((a, b) => b[1] - a[1])
              .map(([category, amount]) => (
                <div key={category}>
                  <div className="flex justify-between text-sm">
                    <span className="text-slate-700">{category}</span>
                    <span className="font-medium text-slate-900">
                      {formatCurrency(amount)}
                    </span>
                  </div>
                  <div className="mt-1 h-2 w-full rounded-full bg-slate-100">
                    <div
                      className="h-2 rounded-full bg-indigo-600"
                      style={{
                        width: `${totalExpenses > 0 ? (amount / totalExpenses) * 100 : 0}%`,
                      }}
                    />
                  </div>
                </div>
              ))}
          </div>
        </div>
      )}

      <div className="mt-8 grid grid-cols-1 gap-8 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <div className="overflow-hidden rounded-lg border border-slate-200 bg-white">
            <table className="min-w-full divide-y divide-slate-200 text-sm">
              <thead className="bg-slate-50">
                <tr>
                  <th className="px-4 py-3 text-left font-semibold text-slate-600">Description</th>
                  <th className="px-4 py-3 text-left font-semibold text-slate-600">Category</th>
                  <th className="px-4 py-3 text-left font-semibold text-slate-600">Amount</th>
                  <th className="px-4 py-3 text-left font-semibold text-slate-600">Date</th>
                  <th className="px-4 py-3" />
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {expenses.map((expense) => (
                  <tr key={expense.id}>
                    <td className="px-4 py-3 font-medium text-slate-900">{expense.description}</td>
                    <td className="px-4 py-3 text-slate-600">{expense.category}</td>
                    <td className="px-4 py-3 text-slate-600">{formatCurrency(expense.amount)}</td>
                    <td className="px-4 py-3 text-slate-600">{expense.date.toLocaleDateString()}</td>
                    <td className="px-4 py-3 text-right">
                      <form action={deleteExpense}>
                        <input type="hidden" name="id" value={expense.id} />
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
                {expenses.length === 0 && (
                  <tr>
                    <td colSpan={5} className="px-4 py-8 text-center text-slate-400">
                      No expenses logged yet.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div className="rounded-lg border border-slate-200 bg-white p-6">
          <h2 className="text-sm font-semibold text-slate-900">Log an expense</h2>
          <form action={createExpense} className="mt-4 space-y-4">
            <div>
              <label className="block text-xs font-medium text-slate-700">Description</label>
              <input
                name="description"
                required
                className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-700">Category</label>
              <input
                name="category"
                required
                placeholder="Software, Payroll, Office..."
                className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
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
            <button
              type="submit"
              className="w-full rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-indigo-500"
            >
              Log expense
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
