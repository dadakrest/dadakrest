import { NextRequest, NextResponse } from "next/server";
import { createOAuthClient } from "@/lib/google";
import { prisma } from "@/lib/prisma";
import { google } from "googleapis";

export async function GET(request: NextRequest) {
  const code = request.nextUrl.searchParams.get("code");
  if (!code) {
    return NextResponse.redirect(new URL("/documents?error=missing_code", request.url));
  }

  const client = createOAuthClient();

  try {
    const { tokens } = await client.getToken(code);
    client.setCredentials(tokens);

    let email: string | null = null;
    try {
      const oauth2 = google.oauth2({ version: "v2", auth: client });
      const info = await oauth2.userinfo.get();
      email = info.data.email ?? null;
    } catch {
      // best-effort, not required for the connection to work
    }

    await prisma.googleConnection.upsert({
      where: { id: "singleton" },
      create: {
        id: "singleton",
        email,
        accessToken: tokens.access_token ?? "",
        refreshToken: tokens.refresh_token ?? null,
        expiryDate: tokens.expiry_date ? new Date(tokens.expiry_date) : null,
      },
      update: {
        email,
        accessToken: tokens.access_token ?? undefined,
        refreshToken: tokens.refresh_token ?? undefined,
        expiryDate: tokens.expiry_date ? new Date(tokens.expiry_date) : undefined,
      },
    });

    return NextResponse.redirect(new URL("/documents", request.url));
  } catch (error) {
    console.error("Google OAuth callback failed:", error);
    return NextResponse.redirect(new URL("/documents?error=oauth_failed", request.url));
  }
}
