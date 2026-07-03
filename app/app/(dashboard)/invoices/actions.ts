"use server";

import { prisma } from "@/lib/prisma";
import { revalidatePath } from "next/cache";

export async function createInvoice(formData: FormData) {
  const number = String(formData.get("number") ?? "").trim();
  const clientId = String(formData.get("clientId") ?? "");
  const amount = Number(formData.get("amount"));
  const dueDate = String(formData.get("dueDate") ?? "");

  if (!number || !clientId || !amount || !dueDate) return;

  await prisma.invoice.create({
    data: { number, clientId, amount, dueDate: new Date(dueDate) },
  });

  revalidatePath("/invoices");
  revalidatePath("/finance");
  revalidatePath("/");
}

export async function updateInvoiceStatus(formData: FormData) {
  const id = String(formData.get("id"));
  const status = String(formData.get("status"));
  await prisma.invoice.update({ where: { id }, data: { status } });
  revalidatePath("/invoices");
  revalidatePath("/finance");
  revalidatePath("/");
}

export async function deleteInvoice(formData: FormData) {
  const id = String(formData.get("id"));
  await prisma.invoice.delete({ where: { id } });
  revalidatePath("/invoices");
  revalidatePath("/finance");
  revalidatePath("/");
}
