import Link from "next/link";
import { prisma } from "@/lib/prisma";

function formatCurrency(amount: number) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(amount);
}

export default async function DashboardPage() {
  const [clientCount, activeProjects, invoices, expenses, tasks, settings] = await Promise.all([
    prisma.client.count(),
    prisma.project.count({ where: { status: "active" } }),
    prisma.invoice.findMany(),
    prisma.expense.findMany(),
    prisma.task.findMany(),
    prisma.companySettings.upsert({
      where: { id: "singleton" },
      create: { id: "singleton" },
      update: {},
    }),
  ]);

  const paidRevenue = invoices.filter((i) => i.status === "paid").reduce((s, i) => s + i.amount, 0);
  const outstanding = invoices.filter((i) => i.status !== "paid").reduce((s, i) => s + i.amount, 0);
  const totalExpenses = expenses.reduce((s, e) => s + e.amount, 0);
  const netIncome = paidRevenue - totalExpenses;
  const openTasks = tasks.filter((t) => t.status !== "done").length;

  const cards = [
    { label: "Clients", value: clientCount, href: "/clients" },
    { label: "Active projects", value: activeProjects, href: "/projects" },
    { label: "Revenue collected", value: formatCurrency(paidRevenue), href: "/finance" },
    { label: "Outstanding invoices", value: formatCurrency(outstanding), href: "/invoices" },
    { label: "Net income", value: formatCurrency(netIncome), href: "/finance" },
    { label: "Open tasks", value: openTasks, href: "/board" },
  ];

  return (
    <div>
      <h1 className="text-2xl font-bold text-slate-900">{settings.name}</h1>
      <p className="mt-1 text-sm text-slate-500">{settings.tagline}</p>

      <div className="mt-8 grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
        {cards.map((card) => (
          <Link
            key={card.label}
            href={card.href}
            className="rounded-lg border border-slate-200 bg-white p-6 transition-shadow hover:shadow-md"
          >
            <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
              {card.label}
            </p>
            <p className="mt-2 text-2xl font-bold text-slate-900">{card.value}</p>
          </Link>
        ))}
      </div>

      <div className="mt-10 rounded-lg border border-slate-200 bg-white p-6">
        <h2 className="text-sm font-semibold text-slate-900">Quick links</h2>
        <div className="mt-4 flex flex-wrap gap-3 text-sm">
          <Link href="/clients" className="rounded-md bg-slate-100 px-3 py-1.5 text-slate-700 hover:bg-slate-200">
            Add a client
          </Link>
          <Link href="/invoices" className="rounded-md bg-slate-100 px-3 py-1.5 text-slate-700 hover:bg-slate-200">
            Create an invoice
          </Link>
          <Link href="/contracts" className="rounded-md bg-slate-100 px-3 py-1.5 text-slate-700 hover:bg-slate-200">
            Draft a contract
          </Link>
          <Link href="/board" className="rounded-md bg-slate-100 px-3 py-1.5 text-slate-700 hover:bg-slate-200">
            Plan work on the board
          </Link>
          <Link href="/documents" className="rounded-md bg-slate-100 px-3 py-1.5 text-slate-700 hover:bg-slate-200">
            Connect Google Drive
          </Link>
        </div>
      </div>
    </div>
  );
}
