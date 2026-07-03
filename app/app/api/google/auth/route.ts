import { NextResponse } from "next/server";
import { createOAuthClient, GOOGLE_SCOPES, isGoogleConfigured } from "@/lib/google";

export async function GET() {
  if (!isGoogleConfigured()) {
    return NextResponse.json(
      { error: "Google OAuth is not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET." },
      { status: 400 }
    );
  }

  const client = createOAuthClient();
  const url = client.generateAuthUrl({
    access_type: "offline",
    prompt: "consent",
    scope: GOOGLE_SCOPES,
  });

  return NextResponse.redirect(url);
}
