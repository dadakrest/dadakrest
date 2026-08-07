# Dadakrest Security — website

A single-file static website. No build step, no dependencies, no framework.
Open `index.html` in a browser to see it.

## What still needs your input

Four spots in `index.html` are marked with `<!-- ===== EDIT ME ===== -->`:

| Where | What's needed |
|---|---|
| About section | Your name, and any wording you'd put differently |
| FAQ → "What does it cost?" | Your real pricing, once you've set it |
| Contact section | Your real email address |
| Contact section | Optional phone number or booking link |

Everything else is written and ready to go live.

## Positioning — why the copy says what it says

The site sells **security fundamentals for small businesses**, not penetration
testing. That's deliberate:

- It's work that can be delivered well right now, at the current certification
  level, without overstating capability.
- Overstating capability in security is a real liability. It attracts clients
  who can't be served properly, and misrepresenting security work to a paying
  client carries legal and reputational risk.
- The fundamentals market is large, underserved, and pays. Most small
  businesses fail on passwords, backups and patching — not on anything a
  pentest would uncover.

The FAQ answers "do you do penetration testing?" honestly and offers a
referral. That answer builds more trust than a vague yes, and it can be
rewritten the moment the credentials behind it change.

**Do not add certifications to the About panel that haven't been earned.**
The panel deliberately shows in-progress items as in-progress. That honesty is
load-bearing for the whole page.

## Putting it on the internet

Free, in rough order of least effort:

1. **GitHub Pages** — move these files to their own repository, then
   Settings → Pages → deploy from branch. Gives you `username.github.io/reponame`.
2. **Netlify** or **Cloudflare Pages** — connect the repository and every push
   redeploys automatically. Both have free tiers and both support custom
   domains at no extra cost.

A domain such as `dadakrestsecurity.com` runs roughly $12/year and can point at
any of the above. Buy the domain before printing anything.

## How this relates to Quickbase

Quickbase is an internal database platform — suited to running operations
(clients, engagements, findings), not to serving a public marketing page. The
normal arrangement is:

- **This site** is the public front door. Anyone can visit it.
- **Quickbase**, if kept, sits behind it holding business data, visible only to
  you and your team.

Connecting the two later goes through the Quickbase REST API
(`api.quickbase.com/v1`), authenticated with a user token sent as
`Authorization: QB-USER-TOKEN user_token=...` plus a `QB-Realm-Hostname` header.

That token is a live credential. It belongs in an environment variable — never
committed to a repository, never pasted into a chat.
