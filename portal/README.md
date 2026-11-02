# AI Data Center Portal

`ai-data-center-portal.html` is the source of the sign-in portal for
Software da Database User 13. It is published as a private page on claude.ai
and only works there: it reads who is signed in and keeps its records through
the claude.ai page runtime. Opened anywhere else, it shows the signed-out
screen.

Live page (private; only people the owner shares it with can open it):
https://claude.ai/artifact/1WMH6ABBvjhC9F6po7JrK2

## How people get in

1. They open the link while signed in to their Claude account. Claude's own
   sign-in offers Google or email, and checks the email address.
2. They send an access request from the page.
3. The owner approves, declines or blocks them in the owner console.

The portal never asks for or stores a password.

## Who can do what

These rules are set when the page is published, and the claude.ai database
enforces them:

| Records | Who can read | Who can change |
| --- | --- | --- |
| `requests/<person>` | the owner, and that person | the owner, and that person |
| `members/<person>` (status, block dates) | the owner, and that person | the owner only |
| `blocks/<person>` (block reason) | the owner only | the owner only |
| `audit/...` (activity log) | the owner only | the owner only |
| `data/users/<person>/...` (private notes) | that person only | that person only |
| anything else | signed-in Contributors | the owner only |

People must be shared on the page as **Contributor** to send a request.

## Blocking

The owner can block a person for four months. During those four months the
block cannot be lifted. After the review date it stays in place until the
owner chooses **Unblock** or **Keep blocked 4 more months**. A blocked person
sees a blocked notice and cannot send new requests. To stop them opening the
page at all, the owner also removes them in the page's Share menu.

The page does not detect break-in attempts by itself. A visitor's own browser
runs the page, so any tripwire in it could be switched off. The database
refuses reads and writes of other people's records whatever the browser does,
and the owner decides who to block.
