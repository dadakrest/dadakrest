"use server";

import { prisma } from "@/lib/prisma";
import { revalidatePath } from "next/cache";

export async function updateSettings(formData: FormData) {
  const name = String(formData.get("name") ?? "").trim();
  const tagline = String(formData.get("tagline") ?? "").trim();
  const address = String(formData.get("address") ?? "").trim();
  const logoUrl = String(formData.get("logoUrl") ?? "").trim();

  if (!name) return;

  await prisma.companySettings.upsert({
    where: { id: "singleton" },
    create: { id: "singleton", name, tagline, address, logoUrl: logoUrl || null },
    update: { name, tagline, address, logoUrl: logoUrl || null },
  });

  revalidatePath("/settings");
  revalidatePath("/");
}
