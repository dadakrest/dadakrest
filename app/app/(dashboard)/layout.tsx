import Link from "next/link";
import { auth, signOut } from "@/auth";

const links = [
  { href: "/", label: "Dashboard" },
  { href: "/clients", label: "Clients" },
  { href: "/projects", label: "Projects" },
  { href: "/invoices", label: "Invoices" },
  { href: "/contracts", label: "Contracts" },
  { href: "/finance", label: "Finance" },
  { href: "/board", label: "Board" },
  { href: "/documents", label: "Documents" },
  { href: "/email", label: "Email" },
  { href: "/settings", label: "Settings" },
  { href: "/help", label: "Help" },
];

export default async function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const session = await auth();

  return (
    <div className="flex min-h-screen">
      <aside className="hidden w-56 flex-shrink-0 border-r border-slate-200 bg-white sm:block">
        <div className="flex h-16 items-center gap-2 border-b border-slate-200 px-5">
          <span className="flex h-7 w-7 items-center justify-center rounded-md bg-indigo-600 text-xs font-bold text-white">
            N
          </span>
          <span className="text-sm font-semibold text-slate-900">Nexora Ops</span>
        </div>
        <nav className="flex flex-col gap-1 p-3">
          {links.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className="rounded-md px-3 py-2 text-sm font-medium text-slate-600 transition-colors hover:bg-slate-100 hover:text-slate-900"
            >
              {link.label}
            </Link>
          ))}
        </nav>
      </aside>

      <div className="flex flex-1 flex-col">
        <header className="flex h-16 items-center justify-between border-b border-slate-200 bg-white px-6">
          <p className="text-sm text-slate-500 sm:hidden">Nexora Ops</p>
          <div className="ml-auto flex items-center gap-4">
            <span className="text-sm text-slate-600">{session?.user?.email}</span>
            <form
              action={async () => {
                "use server";
                await signOut({ redirectTo: "/login" });
              }}
            >
              <button
                type="submit"
                className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-100"
              >
                Sign out
              </button>
            </form>
          </div>
        </header>
        <main className="flex-1 p-6 sm:p-10">{children}</main>
      </div>
    </div>
  );
}
