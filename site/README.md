# Company site — starter

A single-file static website. No build step, no dependencies, no framework.
Open `index.html` in a browser to see it.

## Filling it in

Every place that needs your words is marked in `index.html` with a comment:

```html
<!-- ===== EDIT ME: ... ===== -->
```

There are seven of them. Work through them top to bottom and the placeholder
site becomes your site. Nothing else needs to change to go live.

## Putting it on the internet

Free, in rough order of least effort:

1. **GitHub Pages** — move these files to their own repository, then
   Settings → Pages → deploy from branch. You get
   `username.github.io/reponame` at no cost.
2. **Netlify or Cloudflare Pages** — connect the repository, and every push
   redeploys automatically. Both have free tiers.

A custom domain (`yourcompany.com`) is roughly $12/year from a registrar and
can be pointed at any of the above.

## How this relates to Quickbase

Quickbase is an internal database platform — good for running operations
(clients, jobs, inventory), not for a public marketing page. The usual
arrangement is:

- **This site** is the public front door. Anyone can visit it.
- **Quickbase** sits behind it, holding the business data, visible only to you
  and your team.

Connecting the two is possible later through the Quickbase REST API
(`api.quickbase.com/v1`), authenticated with a user token. That token is a live
credential and belongs in an environment variable — never committed to a
repository, and never pasted into a chat.
