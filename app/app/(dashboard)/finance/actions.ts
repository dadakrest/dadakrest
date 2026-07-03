"use server";

import { prisma } from "@/lib/prisma";
import { revalidatePath } from "next/cache";

export async function createExpense(formData: FormData) {
  const description = String(formData.get("description") ?? "").trim();
  const category = String(formData.get("category") ?? "").trim();
  const amount = Number(formData.get("amount"));

  if (!description || !category || !amount) return;

  await prisma.expense.create({ data: { description, category, amount } });
  revalidatePath("/finance");
  revalidatePath("/");
}

export async function deleteExpense(formData: FormData) {
  const id = String(formData.get("id"));
  await prisma.expense.delete({ where: { id } });
  revalidatePath("/finance");
  revalidatePath("/");
}
