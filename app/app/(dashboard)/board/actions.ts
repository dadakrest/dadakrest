"use server";

import { prisma } from "@/lib/prisma";
import { revalidatePath } from "next/cache";

export async function createTask(formData: FormData) {
  const title = String(formData.get("title") ?? "").trim();
  const description = String(formData.get("description") ?? "").trim();

  if (!title) return;

  await prisma.task.create({ data: { title, description: description || null } });
  revalidatePath("/board");
}

export async function moveTask(formData: FormData) {
  const id = String(formData.get("id"));
  const status = String(formData.get("status"));
  await prisma.task.update({ where: { id }, data: { status } });
  revalidatePath("/board");
}

export async function deleteTask(formData: FormData) {
  const id = String(formData.get("id"));
  await prisma.task.delete({ where: { id } });
  revalidatePath("/board");
}
