import type { Metadata } from "next";
import ContactForm from "./ContactForm";

export const metadata: Metadata = {
  title: "Contact | Nexora Consulting",
  description: "Get in touch with Nexora Consulting.",
};

export default function ContactPage() {
  return (
    <div className="mx-auto max-w-4xl px-6 py-24">
      <p className="text-sm font-semibold uppercase tracking-wide text-indigo-600">
        Contact
      </p>
      <h1 className="mt-4 text-3xl font-bold tracking-tight text-slate-900 sm:text-4xl">
        Let&apos;s talk about your security.
      </h1>
      <p className="mt-4 max-w-2xl text-lg text-slate-600">
        Fill out the form and a member of our team will follow up within one
        business day.
      </p>

      <div className="mt-12 grid grid-cols-1 gap-12 sm:grid-cols-3">
        <div className="sm:col-span-2">
          <ContactForm />
        </div>
        <div className="space-y-6 text-sm text-slate-600">
          <div>
            <p className="font-semibold text-slate-900">Email</p>
            <p>hello@nexoraconsulting.com</p>
          </div>
          <div>
            <p className="font-semibold text-slate-900">Phone</p>
            <p>(555) 123-4567</p>
          </div>
          <div>
            <p className="font-semibold text-slate-900">Office</p>
            <p>123 Market Street, Suite 400</p>
            <p>Springfield, USA</p>
          </div>
        </div>
      </div>
    </div>
  );
}
