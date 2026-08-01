"use server";

import { prisma } from "@/lib/prisma";
import { revalidatePath } from "next/cache";

export async function updateBusinessPlan(formData: FormData) {
  const missionStatement = String(formData.get("missionStatement") ?? "").trim();
  const targetMarket = String(formData.get("targetMarket") ?? "").trim();
  const goals = String(formData.get("goals") ?? "").trim();
  const strategy = String(formData.get("strategy") ?? "").trim();

  await prisma.businessPlan.upsert({
    where: { id: "singleton" },
    create: { id: "singleton", missionStatement, targetMarket, goals, strategy },
    update: { missionStatement, targetMarket, goals, strategy },
  });

  revalidatePath("/business-plan");
}
