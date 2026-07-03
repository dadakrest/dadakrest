"use client";

import { useActionState } from "react";
import { sendEmail } from "./actions";

const initialState: { error?: string; success?: boolean } = {};

export default function SendEmailForm() {
  const [state, formAction, pending] = useActionState(async (_: typeof initialState, formData: FormData) => {
    return sendEmail(formData);
  }, initialState);

  return (
    <form action={formAction} className="mt-4 space-y-4">
      <div>
        <label className="block text-xs font-medium text-slate-700">To</label>
        <input
          name="to"
          type="email"
          required
          className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
        />
      </div>
      <div>
        <label className="block text-xs font-medium text-slate-700">Subject</label>
        <input
          name="subject"
          required
          className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
        />
      </div>
      <div>
        <label className="block text-xs font-medium text-slate-700">Message</label>
        <textarea
          name="body"
          required
          rows={5}
          className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
        />
      </div>
      {state.error && <p className="text-sm text-red-600">{state.error}</p>}
      {state.success && <p className="text-sm text-green-600">Email sent.</p>}
      <button
        type="submit"
        disabled={pending}
        className="w-full rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-indigo-500 disabled:opacity-60"
      >
        {pending ? "Sending..." : "Send email"}
      </button>
    </form>
  );
}
