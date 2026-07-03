import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Services | Nexora Consulting",
  description: "Security, IT support, and compliance services from Nexora Consulting.",
};

const services = [
  {
    title: "Security Assessments",
    description:
      "We simulate real-world attacks against your network, applications, and cloud infrastructure to find weaknesses before adversaries do.",
    items: [
      "External & internal penetration testing",
      "Vulnerability scanning & remediation planning",
      "Cloud security posture review",
    ],
  },
  {
    title: "Managed IT Support",
    description:
      "A dedicated helpdesk and proactive monitoring team that keeps your infrastructure patched, backed up, and running.",
    items: [
      "24/7 monitoring & alerting",
      "Patch management & endpoint protection",
      "Backup & disaster recovery planning",
    ],
  },
  {
    title: "Network Architecture",
    description:
      "We design and harden network infrastructure &mdash; from segmentation to zero-trust access &mdash; so it scales with your business.",
    items: [
      "Network segmentation & firewall design",
      "VPN & remote access architecture",
      "Active Directory hardening",
    ],
  },
  {
    title: "Compliance Consulting",
    description:
      "We guide you through audit preparation for the frameworks your customers and regulators expect.",
    items: [
      "SOC 2 Type I & II readiness",
      "HIPAA & PCI-DSS gap analysis",
      "Policy & evidence documentation",
    ],
  },
];

export default function ServicesPage() {
  return (
    <div className="mx-auto max-w-6xl px-6 py-24">
      <p className="text-sm font-semibold uppercase tracking-wide text-indigo-600">
        Services
      </p>
      <h1 className="mt-4 text-3xl font-bold tracking-tight text-slate-900 sm:text-4xl">
        Everything you need to stay secure and online.
      </h1>
      <p className="mt-4 max-w-2xl text-lg text-slate-600">
        Nexora Consulting offers focused engagements or ongoing managed
        services &mdash; pick what fits where your business is today.
      </p>

      <div className="mt-16 grid grid-cols-1 gap-10 sm:grid-cols-2">
        {services.map((service) => (
          <div
            key={service.title}
            className="rounded-lg border border-slate-200 p-8"
          >
            <h2 className="text-xl font-semibold text-slate-900">
              {service.title}
            </h2>
            <p className="mt-3 text-sm leading-6 text-slate-600">
              {service.description}
            </p>
            <ul className="mt-5 space-y-2">
              {service.items.map((item) => (
                <li
                  key={item}
                  className="flex items-start gap-2 text-sm text-slate-700"
                >
                  <span className="mt-1 h-1.5 w-1.5 flex-shrink-0 rounded-full bg-indigo-600" />
                  {item}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>

      <div className="mt-16 rounded-lg bg-slate-50 p-8 text-center">
        <h2 className="text-xl font-semibold text-slate-900">
          Not sure where to start?
        </h2>
        <p className="mt-2 text-slate-600">
          Book a free consultation and we&apos;ll help you prioritize.
        </p>
        <Link
          href="/contact"
          className="mt-6 inline-block rounded-md bg-indigo-600 px-5 py-3 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-indigo-500"
        >
          Contact us
        </Link>
      </div>
    </div>
  );
}
