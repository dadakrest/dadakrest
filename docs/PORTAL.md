# Running the portal live

`python -m datacenter portal` serves the seven databases as web pages behind
two-step sign-in. This guide takes it from a test on your own computer to a
live portal that other people reach over a VPN.

- [What you need](#what-you-need)
- [a. Run it on a computer or a small server](#a-run-it-on-a-computer-or-a-small-server)
- [b. Google sign-in](#b-google-sign-in)
- [c. An email account for sending codes](#c-an-email-account-for-sending-codes)
- [d. A VPN with Tailscale](#d-a-vpn-with-tailscale)
- [e. Backups](#e-backups)
- [f. What the owner can and cannot see](#f-what-the-owner-can-and-cannot-see)
- [Settings](#settings) and [commands](#commands)

How sign-in works, in one paragraph: a person asks for access (with Google,
or with an email address and a password) and confirms their address with a
6-digit code sent to it. The owner approves or declines them in the owner
center. Every sign-in is two steps: first Google or the password, then a
6-digit code emailed to the account's own address, or one of ten one-time
backup codes. The owner is the person whose email is `SDDU13_OWNER_EMAIL`;
the owner's account is approved automatically and still signs in with both
steps.

## What you need

- A computer that stays on: a small Linux server, a spare PC or a Mac.
  Windows works too (use `py` where this guide says `python3`).
- Python 3.10 or newer. Nothing else to install: the portal uses only the
  Python standard library.
- An email account the portal can send from (section c).
- Optional: a Google Cloud project for "Sign in with Google" (section b).
- Optional but recommended: Tailscale, for the VPN (section d).

## a. Run it on a computer or a small server

1. **Copy the code** onto the machine, for example with `git clone` of this
   repository, or by downloading it as a ZIP and unpacking it. The rest of
   this guide runs commands from that folder, and keeps the data in
   `/srv/sddu13` (choose any folder; `--root` says where it is).

2. **Create and fill the databases:**

   ```bash
   python3 -m datacenter --root /srv/sddu13 init
   python3 -m datacenter --root /srv/sddu13 seed
   ```

3. **Name the owner**, either in the environment or stored on the server:

   ```bash
   export SDDU13_OWNER_EMAIL=you@example.com
   # or, stored in the accounts store:
   python3 -m datacenter --root /srv/sddu13 portal-owner you@example.com
   ```

   When both are set, `SDDU13_OWNER_EMAIL` wins, and `portal-owner` says so.

4. **Try it with a test outbox** before setting up real email. Codes are
   written to files instead of being sent:

   ```bash
   python3 -m datacenter --root /srv/sddu13 portal --mail-outbox /srv/sddu13-outbox
   ```

   Open http://127.0.0.1:8013, choose **Ask for access**, and use the owner's
   address. The code is in the newest `.eml` file in `/srv/sddu13-outbox`.
   The outbox holds codes in plain text, so use it only for testing, on a
   machine only you use.

5. **Keep the settings in one private file**, for example
   `/etc/sddu13/portal.env`, readable only by the account that runs the
   portal (`chmod 600`). The full list is under [Settings](#settings).

   ```bash
   SDDU13_OWNER_EMAIL=you@example.com
   SDDU13_PUBLIC_URL=https://portal-server.your-tailnet.ts.net
   SDDU13_SMTP_HOST=smtp.gmail.com
   SDDU13_SMTP_PORT=587
   SDDU13_SMTP_TLS=starttls
   SDDU13_SMTP_USER=portal.sender@gmail.com
   SDDU13_SMTP_PASSWORD=the-app-password
   SDDU13_SMTP_FROM=portal.sender@gmail.com
   SDDU13_GOOGLE_CLIENT_ID=1234-abc.apps.googleusercontent.com
   SDDU13_GOOGLE_CLIENT_SECRET=the-client-secret
   ```

6. **Start it when the machine starts.** On Linux with systemd, create
   `/etc/systemd/system/sddu13-portal.service` (adjust the folders and the
   user):

   ```ini
   [Unit]
   Description=Software da Database User 13 portal
   After=network-online.target tailscaled.service
   Wants=network-online.target

   [Service]
   User=sddu13
   WorkingDirectory=/opt/sddu13
   EnvironmentFile=/etc/sddu13/portal.env
   ExecStart=/usr/bin/python3 -m datacenter --root /srv/sddu13 portal --https-cookies --trust-proxy --allow-network 100.64.0.0/10
   Restart=on-failure

   [Install]
   WantedBy=multi-user.target
   ```

   `WorkingDirectory` is the folder holding the code (the one with the
   `datacenter` folder in it). Then:

   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now sddu13-portal
   journalctl -u sddu13-portal -f      # the start-up report and the access log
   ```

   The start-up report says where the portal listens, how email is sent,
   whether Google sign-in is on, who the owner is, and warns about anything
   unsafe. Logs never contain a password, a code or a session token, and
   access lines show the path only, never the query string.

7. **Sign in as the owner**, open **Owner center → Security checklist**, and
   fix anything it marks as a problem. The **Connection** page shows whether
   the server can reach the internet, DNS, Google and the email server.

The portal listens on `127.0.0.1` (this machine only) unless you pass
`--host` with `--allow-remote`. Section d shows the two safe ways to let
other people in.

## b. Google sign-in

"Sign in with Google" replaces the password step only. The emailed code is
still asked for every time. Without the three Google settings the button is
hidden and the sign-in page says Google sign-in is not set up yet.

Google only accepts an `https://` redirect address (or `http://localhost`),
so set up HTTPS first (section d, option B) and know your public URL, for
example `https://portal-server.your-tailnet.ts.net`.

1. Go to https://console.cloud.google.com and create a project (or pick one).
2. Open **APIs & Services → OAuth consent screen**. Newer consoles call this
   **Google Auth Platform**, with **Branding**, **Audience** and **Data
   access** pages.
   - Branding: an app name people will recognise, and a support email.
   - Audience: **External** for ordinary Google accounts (**Internal** if
     everyone is in your Google Workspace). While the app is in **Testing**,
     only the test users you list can sign in: add each person's Google
     address, or publish the app. With only the three basic scopes below,
     Google does not usually ask for a review.
   - Data access (scopes): `openid`, `.../auth/userinfo.email` and
     `.../auth/userinfo.profile`. Nothing else.
3. Open **Credentials** (or **Clients**) → **Create credentials → OAuth
   client ID** → Application type **Web application**.
   - **Authorized redirect URIs**: your public URL followed by
     `/auth/google/callback`, exactly, for example
     `https://portal-server.your-tailnet.ts.net/auth/google/callback`.
   - Authorized JavaScript origins: leave empty; the portal uses none.
4. Copy the **client ID** and the **client secret** straight away (the
   console may show the secret only once) into the settings file:

   ```bash
   SDDU13_GOOGLE_CLIENT_ID=1234-abc.apps.googleusercontent.com
   SDDU13_GOOGLE_CLIENT_SECRET=the-client-secret
   SDDU13_PUBLIC_URL=https://portal-server.your-tailnet.ts.net
   ```

5. Restart the portal. The start-up report says `Google sign-in: on`, and the
   security checklist shows the redirect URI it uses, which must match the
   one in step 3 character for character.

The portal checks everything Google sends back: the issuer is Google, the
token is for this client and has not expired, the one-time values match this
sign-in, and Google has verified the email address. If the client ID or the
secret is set without the other (or without the public URL), Google sign-in
stays off, and the start-up report and the checklist name what is missing.
A public URL on its own is fine: it is also the portal's own address.

## c. An email account for sending codes

Every sign-in sends a code by email, so the portal will not offer sign-in
until it can send mail. Use an account made for the portal, not your
personal one.

**A Gmail account with an app password**

1. Create a Google account for the portal, for example
   `portal.sender@gmail.com`.
2. Turn on **2-Step Verification** for it (Google Account → Security).
3. Create an **app password** at https://myaccount.google.com/apppasswords
   and copy the 16-character password. (Google does not offer app passwords
   for some accounts, for example those with Advanced Protection.)
4. Put these in the settings file:

   ```bash
   SDDU13_SMTP_HOST=smtp.gmail.com
   SDDU13_SMTP_PORT=587
   SDDU13_SMTP_TLS=starttls
   SDDU13_SMTP_USER=portal.sender@gmail.com
   SDDU13_SMTP_PASSWORD=the-app-password
   SDDU13_SMTP_FROM=portal.sender@gmail.com
   ```

Gmail limits how much one account sends in a day. That is plenty for a team;
for many people, use a mail provider.

**Any other SMTP provider** (your domain's mail host, or a sending service)
takes the same settings: its server name, `587` with `starttls`, or `465`
with `SDDU13_SMTP_TLS=ssl`. There is no unencrypted option: the portal always
uses TLS and checks the server's certificate. A relay that needs no login
can leave out `SDDU13_SMTP_USER` and `SDDU13_SMTP_PASSWORD` (set both or
neither).

**Check it**: the start-up report prints `email: SMTP smtp.gmail.com:587
(starttls)`, and the **Connection** page connects to the server and says
hello (EHLO) without sending anything. If a setting is missing, every
sign-in page says which one.

Remember that codes travel by email: whoever can read a person's mailbox can
read their codes. Ask people to keep their email account protected with its
own two-step sign-in.

## d. A VPN with Tailscale

The portal cannot be a VPN, and it does not give anyone internet access. It
is a web server. A VPN is a separate program that puts your devices on one
private network; Tailscale (built on WireGuard) is the easiest, and plain
WireGuard works the same way. The portal's part is to answer only to that
network (`--allow-network`).

1. **Install Tailscale on the server** (https://tailscale.com/download; on
   Linux `curl -fsSL https://tailscale.com/install.sh | sh`), then run
   `sudo tailscale up` and sign in.
2. **Install Tailscale on each person's device** (phone, laptop) and sign in.
   Add people to your tailnet from the Tailscale admin console (**Users →
   Invite users**), or share just the server with them (**Machines → the
   server → Share**).
3. Every device on the tailnet gets an address in `100.64.0.0/10`.

Then choose one of these.

**Option A: straight over the VPN (simplest)**

```bash
python3 -m datacenter --root /srv/sddu13 portal \
    --host "$(tailscale ip -4)" --allow-remote --allow-network 100.64.0.0/10
```

The portal listens only on the server's Tailscale address and turns away any
address outside the tailnet with a 403. People open
`http://<tailscale-address>:8013`. WireGuard encrypts the traffic, but the
browser sees plain HTTP, so cookies are not marked Secure and Google sign-in
cannot be used (Google needs an `https://` address). Use option B for that.

**Option B: HTTPS with `tailscale serve` (recommended)**

1. In the Tailscale admin console, open **DNS** and turn on **MagicDNS** and
   **HTTPS Certificates**.
2. Keep the portal on `127.0.0.1` and let Tailscale add HTTPS in front of it:

   ```bash
   sudo tailscale serve --bg 8013
   tailscale serve status          # shows https://<machine>.<tailnet>.ts.net
   ```

3. Start the portal with that address:

   ```bash
   python3 -m datacenter --root /srv/sddu13 portal \
       --public-url https://portal-server.your-tailnet.ts.net \
       --https-cookies --trust-proxy --allow-network 100.64.0.0/10
   ```

   `--https-cookies` marks cookies Secure and turns on HSTS. `--trust-proxy`
   takes each visitor's address from the `X-Forwarded-For` header that
   `tailscale serve` adds, so `--allow-network`, the throttles and the
   activity log see the real Tailscale address. The header is only believed
   when the connection comes from this machine. Check **Owner center →
   Activity**: addresses should be `100.x.y.z`, not `127.0.0.1`. If every page
   says "This portal is not open to your network", your Tailscale version
   does not send that header: drop `--allow-network` and `--trust-proxy`.
   The portal still listens only on `127.0.0.1`, so only `tailscale serve`
   can reach it.

People open `https://portal-server.your-tailnet.ts.net`. Do not use
**Tailscale Funnel** for the portal unless you mean to put it on the public
internet: Funnel publishes it to everyone, not just your tailnet.

## e. Backups

```bash
python3 -m datacenter --root /srv/sddu13 backup /srv/backups
```

This copies the seven databases into a dated folder with a manifest, safely
while the portal runs. A backup folder is itself a valid `--root`. Run it
every day, for example with cron (`crontab -e`):

```bash
17 3 * * * cd /opt/sddu13 && python3 -m datacenter --root /srv/sddu13 backup /srv/backups
```

The security checklist marks backups as a problem when there is none, and
asks for a new one when the last is more than a week old.

**The accounts store is separate** (`/srv/sddu13/portal/accounts.db`) and
`backup` leaves it out on purpose. To back it up:

```bash
python3 -c "from datacenter.accounts import AccountStore as S; s = S('/srv/sddu13'); s.backup_to('/srv/backups/accounts.db'); s.close()"
```

It holds only hashes of passwords and codes, but treat it as private: keep it
off shared drives. `portal/secret.key` signs codes and backup codes. Keep a
copy somewhere private; if it is lost, a new one is made, outstanding codes
and backup codes stop working (people sign in with an emailed code and ask
for new backup codes), and passwords keep working.

Keep a copy of the backups off the server too, encrypted. To restore, stop
the portal, copy the files back into the root, and start it again.

## f. What the owner can and cannot see

**The owner can see**, in the owner center: each person's name, email,
company, the reason they gave, how they sign in (password or Google), their
status and the dates it changed; blocked accounts with the reason, the date
and the review date; and the activity log (sign-ins, failed sign-ins,
strikes, blocks, decisions) with the addresses they came from.

**The owner can do**: approve, decline, remove access, block for four months
(with a reason), unblock or keep blocked once the review date has come,
email someone a password reset code, sign someone out everywhere, and post
portal updates.

**Nobody can see, the owner included**: passwords (stored as PBKDF2 hashes),
emailed codes and backup codes (stored as keyed hashes), session tokens
(stored as hashes) and anything from Google sign-in (nothing secret is
stored). The pages, the logs and the commands have no way to show them. A
reset code goes to the person, never to the owner.

Two honest limits. Whoever runs the server can read the files, but finds only
hashes there. And codes travel by email, so whoever controls a person's
mailbox (or the test outbox) can read that person's codes.

The **Audit** page, which every member can open, records portal events by
account number only, with no emails and no block reasons.

## Settings

| Setting | What it is |
|---|---|
| `SDDU13_ROOT` | Where the databases live (or `--root`). |
| `SDDU13_OWNER_EMAIL` | The owner's address. Wins over `portal-owner`. |
| `SDDU13_PUBLIC_URL` | Where people reach the portal (or `--public-url`). Must be `https://` except for localhost. |
| `SDDU13_SMTP_HOST`, `SDDU13_SMTP_FROM` | The mail server and the sender address. Required for SMTP. |
| `SDDU13_SMTP_USER`, `SDDU13_SMTP_PASSWORD` | The login. Both, or neither for a relay that needs none. |
| `SDDU13_SMTP_PORT`, `SDDU13_SMTP_TLS` | `587` with `starttls` (the default), or `465` with `ssl`. |
| `SDDU13_MAIL_OUTBOX` | Testing only: write mail as files in this folder (or `--mail-outbox`). Wins over SMTP. |
| `SDDU13_GOOGLE_CLIENT_ID`, `SDDU13_GOOGLE_CLIENT_SECRET` | The Google OAuth client. Set both, with `SDDU13_PUBLIC_URL`, or none. |
| `SDDU13_PORTAL_SECRET` | Optional: the server secret (at least 32 characters) instead of `portal/secret.key`. |

| `portal` option | What it does |
|---|---|
| `--host H --allow-remote` | Listen on an address other than this machine. Refused without `--allow-remote`. |
| `--port P` | Default 8013. |
| `--allow-network CIDR` | Serve only these networks, e.g. `100.64.0.0/10` for Tailscale. Repeatable. Others get 403. |
| `--trust-proxy` | Take the client address from `X-Forwarded-For`, only from a proxy on this machine. |
| `--https-cookies` | The portal is reached over HTTPS: Secure cookies and HSTS. |
| `--public-url URL` | As `SDDU13_PUBLIC_URL`. With it set, the portal answers only to that host name. |
| `--mail-outbox DIR` | As `SDDU13_MAIL_OUTBOX`. |

## Commands

Run on the server. None of them prints a password or a code.

| Command | Does |
|---|---|
| `portal-owner EMAIL` | Store the owner's email (says so if `SDDU13_OWNER_EMAIL` overrides it). |
| `accounts [--status S]` | List accounts: email, role, status, created. |
| `account-block EMAIL --reason R` | Block for four months and sign them out everywhere. The owner can never be blocked. |
| `account-unblock EMAIL` | Lift a block once its review date has come. Refused before it. Access is not restored: they may ask again. |
| `account-reset EMAIL [--mail-outbox DIR]` | Email them a password reset code. |
