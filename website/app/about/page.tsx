import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "About | Nexora Consulting",
  description: "Learn about Nexora Consulting's mission and team.",
};

const values = [
  {
    title: "Security first",
    description:
      "We treat every client's data like our own. Security isn't a checkbox &mdash; it's the reason we exist.",
  },
  {
    title: "Plain-language advice",
    description:
      "We explain risk and trade-offs in terms your whole team can act on, not just other engineers.",
  },
  {
    title: "Long-term partnership",
    description:
      "We aim to be the IT & security team you call for years, not a one-time audit.",
  },
];

export default function AboutPage() {
  return (
    <div className="mx-auto max-w-6xl px-6 py-24">
      <p className="text-sm font-semibold uppercase tracking-wide text-indigo-600">
        About us
      </p>
      <h1 className="mt-4 text-3xl font-bold tracking-tight text-slate-900 sm:text-4xl">
        We help growing companies punch above their weight on security.
      </h1>
      <p className="mt-6 max-w-3xl text-lg leading-8 text-slate-600">
        Nexora Consulting was founded to give small and mid-sized businesses
        access to the same caliber of security and IT expertise that large
        enterprises take for granted. Our team has backgrounds in penetration
        testing, network engineering, and compliance auditing, and we bring
        that experience to every engagement.
      </p>

      <div className="mt-16 grid grid-cols-1 gap-8 sm:grid-cols-3">
        {values.map((value) => (
          <div key={value.title}>
            <h2 className="text-lg font-semibold text-slate-900">
              {value.title}
            </h2>
            <p className="mt-2 text-sm leading-6 text-slate-600">
              {value.description}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}
