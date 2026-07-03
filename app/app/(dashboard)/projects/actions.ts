"use server";

import { prisma } from "@/lib/prisma";
import { revalidatePath } from "next/cache";

export async function createProject(formData: FormData) {
  const name = String(formData.get("name") ?? "").trim();
  const clientId = String(formData.get("clientId") ?? "");
  const status = String(formData.get("status") ?? "active");

  if (!name || !clientId) return;

  await prisma.project.create({ data: { name, clientId, status } });
  revalidatePath("/projects");
}

export async function updateProjectStatus(formData: FormData) {
  const id = String(formData.get("id"));
  const status = String(formData.get("status"));
  await prisma.project.update({ where: { id }, data: { status } });
  revalidatePath("/projects");
}

export async function deleteProject(formData: FormData) {
  const id = String(formData.get("id"));
  await prisma.project.delete({ where: { id } });
  revalidatePath("/projects");
}
