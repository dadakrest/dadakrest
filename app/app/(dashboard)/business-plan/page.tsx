import { prisma } from "@/lib/prisma";
import { updateBusinessPlan } from "./actions";

const defaults = {
  missionStatement:
    "Give growing businesses access to enterprise-grade security and IT expertise, delivered in plain language.",
  targetMarket:
    "Small and mid-sized companies (20-500 employees) in healthcare, retail, and professional services that need security/compliance help but don't have an in-house security team.",
  goals:
    "1. Grow to 150 active clients within 18 months.\n2. Maintain 99.9% uptime across managed infrastructure.\n3. Achieve SOC 2 Type II certification for our own operations.",
  strategy:
    "Land clients through referrals and compliance-driven urgency (SOC 2/HIPAA deadlines), then expand into ongoing managed IT retainers. Price assessments as a low-friction entry point; retain clients on managed support contracts.",
};

const sections: { key: keyof typeof defaults; label: string }[] = [
  { key: "missionStatement", label: "Mission statement" },
  { key: "targetMarket", label: "Target market" },
  { key: "goals", label: "Goals" },
  { key: "strategy", label: "Strategy" },
];

export default async function BusinessPlanPage() {
  const plan = await prisma.businessPlan.upsert({
    where: { id: "singleton" },
    create: { id: "singleton", ...defaults },
    update: {},
  });

  return (
    <div>
      <h1 className="text-2xl font-bold text-slate-900">Business plan</h1>
      <p className="mt-1 text-sm text-slate-500">
        The company&apos;s mission, market, goals, and strategy in one place.
      </p>

      <div className="mt-8 grid grid-cols-1 gap-8 lg:grid-cols-2">
        <div className="space-y-6">
          {sections.map((section) => (
            <div key={section.key} className="rounded-lg border border-slate-200 bg-white p-6">
              <h2 className="text-sm font-semibold text-slate-900">{section.label}</h2>
              <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-600">
                {plan[section.key] || "Not set yet."}
              </p>
            </div>
          ))}
        </div>

        <div className="rounded-lg border border-slate-200 bg-white p-6">
          <h2 className="text-sm font-semibold text-slate-900">Edit</h2>
          <form action={updateBusinessPlan} className="mt-4 space-y-4">
            <div>
              <label className="block text-xs font-medium text-slate-700">Mission statement</label>
              <textarea
                name="missionStatement"
                rows={3}
                defaultValue={plan.missionStatement}
                className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-700">Target market</label>
              <textarea
                name="targetMarket"
                rows={3}
                defaultValue={plan.targetMarket}
                className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-700">Goals</label>
              <textarea
                name="goals"
                rows={4}
                defaultValue={plan.goals}
                className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-700">Strategy</label>
              <textarea
                name="strategy"
                rows={4}
                defaultValue={plan.strategy}
                className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
            </div>
            <button
              type="submit"
              className="w-full rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-indigo-500"
            >
              Save business plan
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
