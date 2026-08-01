-- CreateTable
CREATE TABLE "BusinessPlan" (
    "id" TEXT NOT NULL PRIMARY KEY DEFAULT 'singleton',
    "missionStatement" TEXT NOT NULL DEFAULT '',
    "targetMarket" TEXT NOT NULL DEFAULT '',
    "goals" TEXT NOT NULL DEFAULT '',
    "strategy" TEXT NOT NULL DEFAULT '',
    "updatedAt" DATETIME NOT NULL
);
