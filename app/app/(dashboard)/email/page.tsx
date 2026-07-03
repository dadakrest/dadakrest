import { google } from "googleapis";
import { prisma } from "@/lib/prisma";
import { isGoogleConfigured, getAuthorizedOAuthClient } from "@/lib/google";
import { disconnectGoogle } from "../documents/actions";
import SendEmailForm from "./SendEmailForm";

async function listMessages() {
  const client = await getAuthorizedOAuthClient();
  if (!client) return null;

  const gmail = google.gmail({ version: "v1", auth: client });
  const list = await gmail.users.messages.list({ userId: "me", maxResults: 10 });
  const ids = list.data.messages ?? [];

  const messages = await Promise.all(
    ids.map(async (m) => {
      const full = await gmail.users.messages.get({
        userId: "me",
        id: m.id!,
        format: "metadata",
        metadataHeaders: ["From", "Subject", "Date"],
      });
      const headers = full.data.payload?.headers ?? [];
      const get = (name: string) => headers.find((h) => h.name === name)?.value ?? "";
      return {
        id: m.id!,
        from: get("From"),
        subject: get("Subject") || "(no subject)",
        date: get("Date"),
        snippet: full.data.snippet ?? "",
      };
    })
  );

  return messages;
}

export default async function EmailPage() {
  const configured = isGoogleConfigured();
  const connection = configured
    ? await prisma.googleConnection.findUnique({ where: { id: "singleton" } })
    : null;

  let messages: Awaited<ReturnType<typeof listMessages>> = null;
  let loadError: string | null = null;

  if (connection) {
    try {
      messages = await listMessages();
    } catch {
      loadError = "Couldn't load Gmail messages. Try reconnecting your Google account.";
    }
  }

  return (
    <div>
      <h1 className="text-2xl font-bold text-slate-900">Email</h1>
      <p className="mt-1 text-sm text-slate-500">
        Recent business email and a quick way to send a new message via Gmail.
      </p>

      {!configured && (
        <div className="mt-8 max-w-2xl rounded-lg border border-amber-200 bg-amber-50 p-6">
          <h2 className="text-sm font-semibold text-amber-900">Gmail isn&apos;t configured yet</h2>
          <p className="mt-2 text-sm leading-6 text-amber-800">
            Gmail uses the same Google OAuth client as Drive. Set{" "}
            <code className="rounded bg-amber-100 px-1 py-0.5">GOOGLE_CLIENT_ID</code>,{" "}
            <code className="rounded bg-amber-100 px-1 py-0.5">GOOGLE_CLIENT_SECRET</code>, and{" "}
            <code className="rounded bg-amber-100 px-1 py-0.5">GOOGLE_REDIRECT_URI</code> in{" "}
            <code className="rounded bg-amber-100 px-1 py-0.5">.env</code>, and make sure the
            Gmail API is enabled for your project in the Google Cloud Console.
          </p>
        </div>
      )}

      {configured && !connection && (
        <div className="mt-8 max-w-md rounded-lg border border-slate-200 bg-white p-6 text-center">
          <p className="text-sm text-slate-600">No Google account connected yet.</p>
          <a
            href="/api/google/auth"
            className="mt-4 inline-block rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-indigo-500"
          >
            Connect Gmail
          </a>
        </div>
      )}

      {connection && (
        <div className="mt-8 grid grid-cols-1 gap-8 lg:grid-cols-3">
          <div className="lg:col-span-2">
            <div className="mb-4 flex items-center justify-between rounded-md border border-slate-200 bg-white px-4 py-3 text-sm text-slate-600">
              <span>
                Connected as{" "}
                <span className="font-medium text-slate-900">
                  {connection.email ?? "Google account"}
                </span>
              </span>
              <form action={disconnectGoogle}>
                <button type="submit" className="text-xs font-medium text-red-600 hover:text-red-800">
                  Disconnect
                </button>
              </form>
            </div>

            {loadError && (
              <p className="rounded-md bg-red-50 px-4 py-3 text-sm text-red-700">{loadError}</p>
            )}

            {!loadError && (
              <div className="divide-y divide-slate-100 overflow-hidden rounded-lg border border-slate-200 bg-white">
                {messages?.map((message) => (
                  <div key={message.id} className="p-4">
                    <div className="flex items-center justify-between">
                      <p className="text-sm font-medium text-slate-900">{message.subject}</p>
                      <p className="text-xs text-slate-400">{message.date}</p>
                    </div>
                    <p className="mt-1 text-xs text-slate-500">{message.from}</p>
                    <p className="mt-2 text-sm text-slate-600">{message.snippet}</p>
                  </div>
                ))}
                {messages?.length === 0 && (
                  <p className="p-8 text-center text-slate-400">No messages found.</p>
                )}
              </div>
            )}
          </div>

          <div className="rounded-lg border border-slate-200 bg-white p-6">
            <h2 className="text-sm font-semibold text-slate-900">Send an email</h2>
            <SendEmailForm />
          </div>
        </div>
      )}
    </div>
  );
}
