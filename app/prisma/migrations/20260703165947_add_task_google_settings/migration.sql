-- CreateTable
CREATE TABLE "Task" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "title" TEXT NOT NULL,
    "description" TEXT,
    "status" TEXT NOT NULL DEFAULT 'todo',
    "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- CreateTable
CREATE TABLE "GoogleConnection" (
    "id" TEXT NOT NULL PRIMARY KEY DEFAULT 'singleton',
    "email" TEXT,
    "accessToken" TEXT NOT NULL,
    "refreshToken" TEXT,
    "expiryDate" DATETIME,
    "updatedAt" DATETIME NOT NULL
);

-- CreateTable
CREATE TABLE "CompanySettings" (
    "id" TEXT NOT NULL PRIMARY KEY DEFAULT 'singleton',
    "name" TEXT NOT NULL DEFAULT 'Nexora Consulting',
    "tagline" TEXT NOT NULL DEFAULT 'IT & Cybersecurity Consulting',
    "address" TEXT NOT NULL DEFAULT '123 Market Street, Suite 400, Springfield',
    "logoUrl" TEXT,
    "updatedAt" DATETIME NOT NULL
);
