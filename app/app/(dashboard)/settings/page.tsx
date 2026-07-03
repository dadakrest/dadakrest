import { prisma } from "@/lib/prisma";
import { updateSettings } from "./actions";

export default async function SettingsPage() {
  const settings = await prisma.companySettings.upsert({
    where: { id: "singleton" },
    create: { id: "singleton" },
    update: {},
  });

  return (
    <div>
      <h1 className="text-2xl font-bold text-slate-900">Company settings</h1>
      <p className="mt-1 text-sm text-slate-500">
        This information appears across the dashboard.
      </p>

      <div className="mt-8 max-w-xl rounded-lg border border-slate-200 bg-white p-6">
        <form action={updateSettings} className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-slate-700">Company name</label>
            <input
              name="name"
              required
              defaultValue={settings.name}
              className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-700">Tagline</label>
            <input
              name="tagline"
              defaultValue={settings.tagline}
              className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-700">Address</label>
            <input
              name="address"
              defaultValue={settings.address}
              className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-700">Logo URL (optional)</label>
            <input
              name="logoUrl"
              defaultValue={settings.logoUrl ?? ""}
              placeholder="https://..."
              className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            />
          </div>
          <button
            type="submit"
            className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-indigo-500"
          >
            Save settings
          </button>
        </form>
      </div>
    </div>
  );
}
