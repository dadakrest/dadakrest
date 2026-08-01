export default function ProductivityPage() {
  return (
    <div className="max-w-3xl">
      <h1 className="text-2xl font-bold text-slate-900">AI &amp; productivity</h1>
      <p className="mt-1 text-sm text-slate-500">
        Practical ways to use AI tools like Claude in day-to-day consulting work.
      </p>

      <div className="mt-8 space-y-8">
        <section>
          <h2 className="text-lg font-semibold text-slate-900">Client-facing work</h2>
          <ul className="mt-2 space-y-2 text-sm leading-6 text-slate-600">
            <li>
              <span className="font-medium text-slate-800">Draft first, edit second:</span> use AI
              to produce a first draft of assessment reports, SOWs, or client emails from your
              notes, then edit for accuracy and tone. Faster than a blank page, never send
              unreviewed.
            </li>
            <li>
              <span className="font-medium text-slate-800">Summarize findings:</span> turn raw
              scan output or meeting notes into a plain-language summary a non-technical client
              can act on &mdash; this is usually the highest-leverage use of AI in a security
              practice.
            </li>
            <li>
              <span className="font-medium text-slate-800">Always verify facts and figures:</span>{" "}
              AI-drafted content can state things confidently and incorrectly. Check numbers,
              client names, and compliance claims before anything goes out.
            </li>
          </ul>
        </section>

        <section>
          <h2 className="text-lg font-semibold text-slate-900">Internal operations</h2>
          <ul className="mt-2 space-y-2 text-sm leading-6 text-slate-600">
            <li>
              <span className="font-medium text-slate-800">Triage the inbox:</span> use the Email
              page here to scan recent messages before deciding what needs a reply now versus
              later.
            </li>
            <li>
              <span className="font-medium text-slate-800">Keep a living plan:</span> update the{" "}
              <a href="/business-plan" className="text-indigo-600 hover:underline">
                Business plan
              </a>{" "}
              page as goals and strategy shift, instead of leaving it in a doc no one reopens.
            </li>
            <li>
              <span className="font-medium text-slate-800">Track work on the board:</span> break
              larger initiatives into tasks on the{" "}
              <a href="/board" className="text-indigo-600 hover:underline">
                planning board
              </a>{" "}
              so nothing lives only in someone&apos;s head.
            </li>
          </ul>
        </section>

        <section>
          <h2 className="text-lg font-semibold text-slate-900">Building this app itself</h2>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            This dashboard was built and is maintained with Claude Code. See the{" "}
            <a href="/help" className="text-indigo-600 hover:underline">
              Help
            </a>{" "}
            page for how that works day to day &mdash; CLAUDE.md, common commands, and where to
            add new features.
          </p>
        </section>

        <section>
          <h2 className="text-lg font-semibold text-slate-900">A few ground rules</h2>
          <ul className="mt-2 space-y-2 text-sm leading-6 text-slate-600">
            <li>Never paste client-confidential data into a tool your contract doesn&apos;t cover.</li>
            <li>Treat AI output as a draft from a fast, inexperienced junior &mdash; useful, not final.</li>
            <li>For anything security- or compliance-related, a human signs off before it ships.</li>
          </ul>
        </section>
      </div>
    </div>
  );
}
