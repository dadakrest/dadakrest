import Link from "next/link";

const services = [
  {
    title: "Security Assessments",
    description:
      "Penetration testing, vulnerability scanning, and risk assessments that identify gaps before attackers do.",
  },
  {
    title: "Managed IT Support",
    description:
      "Proactive monitoring and helpdesk support that keeps your team productive and your systems patched.",
  },
  {
    title: "Compliance Consulting",
    description:
      "Guidance for SOC 2, HIPAA, and PCI-DSS audits, from gap analysis to evidence collection.",
  },
];

const stats = [
  { value: "120+", label: "Clients served" },
  { value: "8", label: "Years in business" },
  { value: "99.9%", label: "Average uptime delivered" },
];

export default function Home() {
  return (
    <div>
      <section className="mx-auto max-w-6xl px-6 py-24 sm:py-32">
        <div className="max-w-2xl">
          <p className="text-sm font-semibold uppercase tracking-wide text-indigo-600">
            IT & Cybersecurity Consulting
          </p>
          <h1 className="mt-4 text-4xl font-bold tracking-tight text-slate-900 sm:text-5xl">
            Secure infrastructure for businesses that can&apos;t afford downtime.
          </h1>
          <p className="mt-6 text-lg leading-8 text-slate-600">
            Nexora Consulting partners with growing companies to assess risk, harden
            networks, and keep IT running smoothly &mdash; so you can focus on your
            business, not your firewall.
          </p>
          <div className="mt-10 flex items-center gap-4">
            <Link
              href="/contact"
              className="rounded-md bg-indigo-600 px-5 py-3 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-indigo-500"
            >
              Talk to an expert
            </Link>
            <Link
              href="/services"
              className="text-sm font-semibold text-slate-900 hover:text-indigo-600"
            >
              View services &rarr;
            </Link>
          </div>
        </div>
      </section>

      <section className="border-y border-slate-200 bg-slate-50">
        <div className="mx-auto grid max-w-6xl grid-cols-1 gap-8 px-6 py-12 sm:grid-cols-3">
          {stats.map((stat) => (
            <div key={stat.label} className="text-center">
              <p className="text-3xl font-bold text-indigo-600">{stat.value}</p>
              <p className="mt-1 text-sm text-slate-600">{stat.label}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-6 py-24">
        <h2 className="text-2xl font-bold tracking-tight text-slate-900">
          What we do
        </h2>
        <div className="mt-10 grid grid-cols-1 gap-8 sm:grid-cols-3">
          {services.map((service) => (
            <div
              key={service.title}
              className="rounded-lg border border-slate-200 p-6 transition-shadow hover:shadow-md"
            >
              <h3 className="text-lg font-semibold text-slate-900">
                {service.title}
              </h3>
              <p className="mt-2 text-sm leading-6 text-slate-600">
                {service.description}
              </p>
            </div>
          ))}
        </div>
      </section>

      <section className="bg-indigo-600">
        <div className="mx-auto max-w-6xl px-6 py-16 text-center">
          <h2 className="text-2xl font-bold text-white">
            Ready to strengthen your security posture?
          </h2>
          <p className="mt-3 text-indigo-100">
            Schedule a free 30-minute consultation with our team.
          </p>
          <Link
            href="/contact"
            className="mt-8 inline-block rounded-md bg-white px-5 py-3 text-sm font-semibold text-indigo-600 shadow-sm transition-colors hover:bg-indigo-50"
          >
            Get in touch
          </Link>
        </div>
      </section>
    </div>
  );
}
