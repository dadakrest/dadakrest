import { google } from "googleapis";
import { prisma } from "@/lib/prisma";

export const GOOGLE_SCOPES = [
  "https://www.googleapis.com/auth/drive.readonly",
  "https://www.googleapis.com/auth/gmail.readonly",
  "https://www.googleapis.com/auth/gmail.send",
  "https://www.googleapis.com/auth/userinfo.email",
];

export function isGoogleConfigured() {
  return Boolean(
    process.env.GOOGLE_CLIENT_ID &&
      process.env.GOOGLE_CLIENT_SECRET &&
      process.env.GOOGLE_REDIRECT_URI
  );
}

export function createOAuthClient() {
  return new google.auth.OAuth2(
    process.env.GOOGLE_CLIENT_ID,
    process.env.GOOGLE_CLIENT_SECRET,
    process.env.GOOGLE_REDIRECT_URI
  );
}

export async function getAuthorizedOAuthClient() {
  const connection = await prisma.googleConnection.findUnique({
    where: { id: "singleton" },
  });
  if (!connection) return null;

  const client = createOAuthClient();
  client.setCredentials({
    access_token: connection.accessToken,
    refresh_token: connection.refreshToken ?? undefined,
    expiry_date: connection.expiryDate?.getTime(),
  });

  client.on("tokens", async (tokens) => {
    await prisma.googleConnection.update({
      where: { id: "singleton" },
      data: {
        accessToken: tokens.access_token ?? connection.accessToken,
        refreshToken: tokens.refresh_token ?? connection.refreshToken,
        expiryDate: tokens.expiry_date ? new Date(tokens.expiry_date) : connection.expiryDate,
      },
    });
  });

  return client;
}
