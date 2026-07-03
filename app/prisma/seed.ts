import "dotenv/config";
import { PrismaClient } from "../app/generated/prisma/client";
import { PrismaBetterSqlite3 } from "@prisma/adapter-better-sqlite3";

const adapter = new PrismaBetterSqlite3({
  url: process.env.DATABASE_URL ?? "file:./dev.db",
});
const prisma = new PrismaClient({ adapter });

async function main() {
  const acme = await prisma.client.create({
    data: { name: "Dana Whitfield", email: "dana@acme-retail.com", company: "Acme Retail" },
  });
  const brightline = await prisma.client.create({
    data: { name: "Marcus Lee", email: "marcus@brightlinehealth.com", company: "Brightline Health" },
  });

  await prisma.project.createMany({
    data: [
      { name: "Network Security Assessment", status: "active", clientId: acme.id },
      { name: "SOC 2 Readiness", status: "on_hold", clientId: brightline.id },
      { name: "Firewall Migration", status: "completed", clientId: acme.id },
    ],
  });

  await prisma.invoice.createMany({
    data: [
      {
        number: "INV-1001",
        clientId: acme.id,
        amount: 4200,
        status: "paid",
        dueDate: new Date("2026-05-15"),
      },
      {
        number: "INV-1002",
        clientId: brightline.id,
        amount: 7800,
        status: "unpaid",
        dueDate: new Date("2026-07-20"),
      },
      {
        number: "INV-1003",
        clientId: acme.id,
        amount: 1500,
        status: "overdue",
        dueDate: new Date("2026-06-01"),
      },
    ],
  });

  await prisma.contract.create({
    data: {
      title: "Master Services Agreement",
      clientId: acme.id,
      status: "active",
      clauses:
        "1. Scope of work: security assessments and remediation guidance as outlined in each statement of work.\n" +
        "2. Payment terms: net 30 from invoice date.\n" +
        "3. Confidentiality: both parties agree to keep findings confidential.\n" +
        "4. Termination: either party may terminate with 30 days written notice.",
    },
  });

  await prisma.expense.createMany({
    data: [
      { description: "Security scanning tool license", category: "Software", amount: 300 },
      { description: "Contractor payroll", category: "Payroll", amount: 5200 },
      { description: "Office internet", category: "Office", amount: 120 },
    ],
  });

  await prisma.task.createMany({
    data: [
      { title: "Draft Q3 client outreach plan", status: "todo" },
      { title: "Review Brightline SOC 2 gap analysis", status: "in_progress" },
      { title: "Renew security scanning license", status: "done" },
    ],
  });

  await prisma.companySettings.upsert({
    where: { id: "singleton" },
    create: { id: "singleton" },
    update: {},
  });

  console.log("Seed complete.");
}

main()
  .catch((e) => {
    console.error(e);
    process.exit(1);
  })
  .finally(async () => {
    await prisma.$disconnect();
  });
