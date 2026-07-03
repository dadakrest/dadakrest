export default function HelpPage() {
  return (
    <div className="max-w-3xl">
      <h1 className="text-2xl font-bold text-slate-900">Working with Claude Code</h1>
      <p className="mt-1 text-sm text-slate-500">
        This project (the marketing site and this business app) was built with Claude
        Code. Here&apos;s what that means for maintaining it going forward.
      </p>

      <div className="mt-8 space-y-8">
        <section>
          <h2 className="text-lg font-semibold text-slate-900">What is Claude Code?</h2>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            Claude Code is Anthropic&apos;s command-line coding agent. You describe a task
            in plain English &mdash; add a feature, fix a bug, scaffold a project &mdash;
            and it reads your codebase, plans an approach, writes and edits files, runs
            commands (installs, builds, tests), and reports back. It runs in your
            terminal, in a web/mobile session like this one, or in an IDE extension.
          </p>
        </section>

        <section>
          <h2 className="text-lg font-semibold text-slate-900">CLAUDE.md</h2>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            Each project in this repository (<code className="rounded bg-slate-100 px-1 py-0.5">website/</code> and{" "}
            <code className="rounded bg-slate-100 px-1 py-0.5">app/</code>) has a{" "}
            <code className="rounded bg-slate-100 px-1 py-0.5">CLAUDE.md</code> file.
            Claude Code reads it automatically at the start of a session, so it&apos;s the
            place to document build/test commands and architecture notes that would
            otherwise need re-explaining every time. Keep it updated as the app grows.
          </p>
        </section>

        <section>
          <h2 className="text-lg font-semibold text-slate-900">Everyday commands</h2>
          <ul className="mt-2 space-y-2 text-sm text-slate-600">
            <li>
              <span className="font-medium text-slate-800">Ask for a change:</span> describe
              what you want in plain language (&quot;add a delete button to the clients
              table&quot;). Claude Code finds the relevant files and edits them directly.
            </li>
            <li>
              <span className="font-medium text-slate-800">/init</span> &mdash; regenerates
              CLAUDE.md from the current state of a codebase.
            </li>
            <li>
              <span className="font-medium text-slate-800">Plan mode</span> &mdash; for
              larger changes, ask Claude to plan first so you can review the approach
              before any files are edited.
            </li>
            <li>
              <span className="font-medium text-slate-800">Hooks &amp; permissions</span> &mdash;
              configured in <code className="rounded bg-slate-100 px-1 py-0.5">.claude/settings.json</code>,
              these control what commands Claude can run without asking, and can run your
              linter/tests automatically after edits.
            </li>
          </ul>
        </section>

        <section>
          <h2 className="text-lg font-semibold text-slate-900">This app specifically</h2>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            Next.js (App Router) + Prisma/SQLite + next-auth. To add a new data model:
            edit <code className="rounded bg-slate-100 px-1 py-0.5">prisma/schema.prisma</code>,
            run <code className="rounded bg-slate-100 px-1 py-0.5">npx prisma migrate dev</code>,
            then add a page + server actions under{" "}
            <code className="rounded bg-slate-100 px-1 py-0.5">app/(dashboard)/</code>. See this
            project&apos;s <code className="rounded bg-slate-100 px-1 py-0.5">CLAUDE.md</code>{" "}
            for the full rundown &mdash; that&apos;s the fastest way to get Claude Code
            productive here again in a future session.
          </p>
        </section>

        <section>
          <h2 className="text-lg font-semibold text-slate-900">Learn more</h2>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            Official docs: <span className="text-indigo-600">code.claude.com/docs</span>.
            They cover installation, slash commands, subagents, MCP server connections
            (like the Google Drive/Gmail integration used on the Documents and Email
            pages here), and configuring hooks in depth.
          </p>
        </section>
      </div>
    </div>
  );
}
