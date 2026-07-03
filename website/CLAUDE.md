# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@AGENTS.md

## What this is

The public marketing site for Nexora Consulting (a placeholder IT/cybersecurity
consulting company). Next.js 16 (App Router, Turbopack) + TypeScript + Tailwind CSS v4.

> **Next.js 16 note:** this version has breaking changes vs. older training data.
> When in doubt, check `node_modules/next/dist/docs/01-app/` before relying on
> memorized API shapes (e.g. `params`/`searchParams` are `Promise`s in Server Components,
> `middleware.ts` is deprecated in favor of `proxy.ts`).

## Commands

```
npm run dev      # start dev server (Turbopack)
npm run build    # production build (also type-checks)
npm run lint     # eslint
npm run start    # run a production build
```

There is no test suite.

## Structure

- `app/page.tsx`, `app/services/page.tsx`, `app/about/page.tsx`, `app/contact/page.tsx` — the four routes.
- `app/components/Navbar.tsx` / `Footer.tsx` — shared chrome, wired into `app/layout.tsx`.
- `app/contact/ContactForm.tsx` — client component; posts to `app/api/contact/route.ts`, which
  currently just validates and `console.log`s the submission (no email service wired up).
- Content (copy, services list, stats) is inline in each page as plain arrays/JSX — there is no CMS.

## Conventions

- Tailwind utility classes only, no CSS modules. Color scheme: indigo-600 accent on slate/white.
- Keep pages as Server Components; only add `"use client"` where interactivity is required
  (see `ContactForm.tsx` for the pattern — client component handles state, server route handles the POST).
