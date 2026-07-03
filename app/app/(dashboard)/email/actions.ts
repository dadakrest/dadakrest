"use server";

import { google } from "googleapis";
import { getAuthorizedOAuthClient } from "@/lib/google";
import { revalidatePath } from "next/cache";

function encodeMessage(to: string, subject: string, body: string, from?: string | null) {
  const lines = [
    `To: ${to}`,
    from ? `From: ${from}` : null,
    `Subject: ${subject}`,
    "Content-Type: text/plain; charset=utf-8",
    "",
    body,
  ].filter(Boolean);

  return Buffer.from(lines.join("\r\n"))
    .toString("base64")
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

export async function sendEmail(formData: FormData) {
  const to = String(formData.get("to") ?? "").trim();
  const subject = String(formData.get("subject") ?? "").trim();
  const body = String(formData.get("body") ?? "").trim();

  if (!to || !subject || !body) {
    return { error: "All fields are required." };
  }

  const client = await getAuthorizedOAuthClient();
  if (!client) {
    return { error: "Google account is not connected." };
  }

  const gmail = google.gmail({ version: "v1", auth: client });
  const raw = encodeMessage(to, subject, body);

  await gmail.users.messages.send({
    userId: "me",
    requestBody: { raw },
  });

  revalidatePath("/email");
  return { success: true };
}
