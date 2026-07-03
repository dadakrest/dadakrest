# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository purpose

The repo name (`dadakrest`) matches the GitHub username, so the root `README.md` is rendered
on the user's GitHub profile page (https://github.com/dadakrest). Beyond that profile README,
this repo also hosts two independent Next.js projects built for a placeholder company
("Nexora Consulting"):

- `website/` — the public marketing site. See `website/CLAUDE.md`.
- `app/` — "Nexora Ops", an internal business management dashboard (clients, projects,
  invoices, contracts, finance, a kanban planning board, Google Drive/Gmail integration).
  See `app/CLAUDE.md`.

Each has its own commands, dependencies, and CLAUDE.md — treat them as separate projects
that happen to live in this repo, not as something that shares tooling with the root.

## Working in this repository

- For profile README edits: the only meaningful content is the root `README.md`. Preserve the
  author's voice and self-introduction (career switch from performing arts to IT, CompTIA
  A+/Network+ studies, interest in pentesting/security) unless asked to change it. Keep
  formatting consistent with GitHub-profile-README conventions (emoji bullet list, HTML
  comment at the bottom explaining the special repo).
- For `website/` or `app/` work, read that subproject's own `CLAUDE.md` first — it has the
  real commands and architecture notes.
