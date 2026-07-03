"use server";

import { prisma } from "@/lib/prisma";
import { revalidatePath } from "next/cache";

export async function createContract(formData: FormData) {
  const title = String(formData.get("title") ?? "").trim();
  const clientId = String(formData.get("clientId") ?? "");
  const clauses = String(formData.get("clauses") ?? "").trim();

  if (!title || !clientId || !clauses) return;

  await prisma.contract.create({ data: { title, clientId, clauses } });
  revalidatePath("/contracts");
}

export async function updateContractStatus(formData: FormData) {
  const id = String(formData.get("id"));
  const status = String(formData.get("status"));
  await prisma.contract.update({ where: { id }, data: { status } });
  revalidatePath("/contracts");
}

export async function deleteContract(formData: FormData) {
  const id = String(formData.get("id"));
  await prisma.contract.delete({ where: { id } });
  revalidatePath("/contracts");
}
