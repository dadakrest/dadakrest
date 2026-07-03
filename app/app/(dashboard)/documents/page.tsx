import { google } from "googleapis";
import { prisma } from "@/lib/prisma";
import { isGoogleConfigured, getAuthorizedOAuthClient } from "@/lib/google";
import { disconnectGoogle } from "./actions";

async function listDriveFiles() {
  const client = await getAuthorizedOAuthClient();
  if (!client) return null;

  const drive = google.drive({ version: "v3", auth: client });
  const res = await drive.files.list({
    pageSize: 20,
    fields: "files(id, name, mimeType, modifiedTime, webViewLink, iconLink)",
    orderBy: "modifiedTime desc",
  });
  return res.data.files ?? [];
}

export default async function DocumentsPage() {
  const configured = isGoogleConfigured();
  const connection = configured
    ? await prisma.googleConnection.findUnique({ where: { id: "singleton" } })
    : null;

  let files: Awaited<ReturnType<typeof listDriveFiles>> = null;
  let loadError: string | null = null;

  if (connection) {
    try {
      files = await listDriveFiles();
    } catch {
      loadError =
        "Couldn't load Drive files. The connection may have expired — try reconnecting.";
    }
  }

  return (
    <div>
      <h1 className="text-2xl font-bold text-slate-900">Documents</h1>
      <p className="mt-1 text-sm text-slate-500">
        Files from your connected Google Drive account.
      </p>

      {!configured && (
        <div className="mt-8 max-w-2xl rounded-lg border border-amber-200 bg-amber-50 p-6">
          <h2 className="text-sm font-semibold text-amber-900">Google Drive isn&apos;t configured yet</h2>
          <p className="mt-2 text-sm leading-6 text-amber-800">
            To connect Drive, create OAuth 2.0 credentials in the{" "}
            <span className="font-medium">Google Cloud Console</span> (APIs &amp; Services →
            Credentials), enable the Google Drive API, and set these in your{" "}
            <code className="rounded bg-amber-100 px-1 py-0.5">.env</code> file:
          </p>
          <pre className="mt-3 overflow-x-auto rounded-md bg-amber-100 p-3 text-xs text-amber-900">
{`GOOGLE_CLIENT_ID="..."
GOOGLE_CLIENT_SECRET="..."
GOOGLE_REDIRECT_URI="http://localhost:3000/api/google/callback"`}
          </pre>
          <p className="mt-2 text-xs text-amber-700">
            Add the redirect URI above as an authorized redirect URI on the OAuth client,
            then restart the app.
          </p>
        </div>
      )}

      {configured && !connection && (
        <div className="mt-8 max-w-md rounded-lg border border-slate-200 bg-white p-6 text-center">
          <p className="text-sm text-slate-600">
            No Google account connected yet.
          </p>
          <a
            href="/api/google/auth"
            className="mt-4 inline-block rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-indigo-500"
          >
            Connect Google Drive
          </a>
        </div>
      )}

      {connection && (
        <div className="mt-8">
          <div className="mb-4 flex items-center justify-between rounded-md bg-white px-4 py-3 text-sm text-slate-600 border border-slate-200">
            <span>
              Connected as <span className="font-medium text-slate-900">{connection.email ?? "Google account"}</span>
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
            <div className="overflow-hidden rounded-lg border border-slate-200 bg-white">
              <table className="min-w-full divide-y divide-slate-200 text-sm">
                <thead className="bg-slate-50">
                  <tr>
                    <th className="px-4 py-3 text-left font-semibold text-slate-600">Name</th>
                    <th className="px-4 py-3 text-left font-semibold text-slate-600">Type</th>
                    <th className="px-4 py-3 text-left font-semibold text-slate-600">Modified</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {files?.map((file) => (
                    <tr key={file.id}>
                      <td className="px-4 py-3 font-medium text-slate-900">
                        <a
                          href={file.webViewLink ?? "#"}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="hover:text-indigo-600"
                        >
                          {file.name}
                        </a>
                      </td>
                      <td className="px-4 py-3 text-slate-500">
                        {file.mimeType?.replace("application/vnd.google-apps.", "") ?? "—"}
                      </td>
                      <td className="px-4 py-3 text-slate-500">
                        {file.modifiedTime ? new Date(file.modifiedTime).toLocaleString() : "—"}
                      </td>
                    </tr>
                  ))}
                  {files?.length === 0 && (
                    <tr>
                      <td colSpan={3} className="px-4 py-8 text-center text-slate-400">
                        No files found in Drive.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
