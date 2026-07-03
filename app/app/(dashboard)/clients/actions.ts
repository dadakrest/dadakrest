"use server";

import { prisma } from "@/lib/prisma";
import { revalidatePath } from "next/cache";

export async function createClient(formData: FormData) {
  const name = String(formData.get("name") ?? "").trim();
  const email = String(formData.get("email") ?? "").trim();
  const company = String(formData.get("company") ?? "").trim();

  if (!name || !email) return;

  await prisma.client.create({
    data: { name, email, company: company || null },
  });

  revalidatePath("/clients");
}

export async function deleteClient(formData: FormData) {
  const id = String(formData.get("id"));
  await prisma.client.delete({ where: { id } });
  revalidatePath("/clients");
}
