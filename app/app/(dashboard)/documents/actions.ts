"use server";

import { prisma } from "@/lib/prisma";
import { revalidatePath } from "next/cache";

export async function disconnectGoogle() {
  await prisma.googleConnection.deleteMany({ where: { id: "singleton" } });
  revalidatePath("/documents");
  revalidatePath("/email");
}
