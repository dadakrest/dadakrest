# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@AGENTS.md

## What this is

"Nexora Ops" — an internal business management dashboard for Nexora Consulting
(a placeholder IT/cybersecurity consulting company). Next.js 16 (App Router,
Turbopack) + TypeScript + Tailwind CSS v4 + Prisma/SQLite + next-auth (Auth.js v5).

## Commands

```
npm run dev         # start dev server (Turbopack), http://localhost:3000
npm run build       # production build (also type-checks)
npm run lint        # eslint
npm run db:migrate  # prisma migrate dev — apply schema changes, generates a migration
npm run db:seed     # reset/populate sample data (prisma/seed.ts)
```

There is no test suite. `postinstall` runs `prisma generate` automatically after `npm install`.

**After editing `prisma/schema.prisma`, you must run `npx prisma generate` (or `db:migrate`,
which does this too) before the app will see new models** — the generated client at
`app/generated/prisma` is not regenerated automatically by anything else.

## Auth

Two independent, unrelated auth systems — don't conflate them:

1. **App login** (`auth.ts`, `proxy.ts`) — next-auth Credentials provider gating every route
   except `/login` and `/api/auth/*`. Single hardcoded admin user via `ADMIN_EMAIL`/`ADMIN_PASSWORD`
   env vars (defaults: `admin@nexoraconsulting.com` / `changeme123` — see `.env.example`). This is
   deliberately simple (no real user table) since it's a single-tenant internal tool.
2. **Google OAuth connection** (`lib/google.ts`, `app/api/google/{auth,callback}/route.ts`) — a
   *separate* OAuth2 flow (via `googleapis`, not next-auth) used only to authorize the Documents
   (Drive) and Email (Gmail) pages. Tokens are stored in the `GoogleConnection` singleton DB row,
   not in the session. Requires `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`/`GOOGLE_REDIRECT_URI`
   from a Google Cloud OAuth client the operator must create themselves — `isGoogleConfigured()`
   gates a graceful "not configured" state on both pages when those are unset, instead of crashing.

Note: `middleware.ts` was renamed to `proxy.ts` per the Next.js 16 convention (Middleware →
Proxy); the next-auth `auth()` wrapper works unchanged either way.

## Data model & adding a new module

`prisma/schema.prisma` defines: `Client` (root entity), `Project`, `Invoice`, `Contract`
(clauses/terms as free text), `Expense`, `Task` (kanban board), `GoogleConnection` and
`CompanySettings` (both singleton rows keyed `id: "singleton"`, fetched with `upsert` so they
always exist).

**Important:** Prisma 7's `prisma-client` generator requires a driver adapter — there is no
built-in query engine binary anymore. `lib/prisma.ts` constructs the shared client with
`@prisma/adapter-better-sqlite3`; any other place a `PrismaClient` gets constructed (e.g.
`prisma/seed.ts`) needs the same adapter wiring or it will throw at construction time.

To add a new CRUD module, follow the existing pattern under `app/(dashboard)/<name>/`:
- `page.tsx` — Server Component; fetches with `prisma`, renders a table/list + an inline create form.
- `actions.ts` — `"use server"` functions (create/update/delete), each ending in `revalidatePath(...)`.
- Any inline `onChange`-driven form (e.g. a status `<select>` that auto-submits) must live in its
  own `"use client"` component — Server Components can't hold event handlers directly (see
  `projects/StatusSelect.tsx` for the pattern).
- Add the route to the nav list in `app/(dashboard)/layout.tsx`.

All dashboard routes live under the `app/(dashboard)/` route group so they share one layout
(sidebar nav + sign-out) without leaking into `/login`.

## Environment

Copy `.env.example` to `.env` and fill in as needed. `AUTH_SECRET` should be generated with
`openssl rand -base64 32`. `DATABASE_URL` defaults to a local `file:./dev.db` SQLite file.
