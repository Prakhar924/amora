# Deploying Amora on $0/month

A zero-cost deployment using only free tiers. **Read [What you give
up](#what-you-give-up-honestly) before committing to this** — the limits are
real and some of them affect whether strangers can actually sign up.

For the paid alternative (~€4.50/month, no sleeping, real email), see
[DEPLOYMENT.md](DEPLOYMENT.md).

---

## The stack

| Layer | Service | Free tier | Verified limitation |
| --- | --- | --- | --- |
| Frontend | GitHub Pages | unlimited static | — |
| API | Render Web Service | 750 instance-hours/mo | **Sleeps after 15 min idle**; ~1 min cold start |
| Database | Supabase Postgres | 500 MB | **Pauses after 7 days** of no activity |
| Cache / rate limits | Upstash Redis | 10k commands/day | No pub/sub over TCP → single instance only |
| Photos | Supabase Storage | 1 GB (same project) | S3-compatible; 5 GB egress/mo |
| Email | Resend | 3,000/mo, 100/day | **Needs a domain to email anyone but yourself** |

Everything below is already implemented in the repository. Your job is to
create the accounts and paste five values into Render.

---

## What you give up, honestly

These are not hypothetical. Each one is documented by the provider and each
changes how the app behaves.

### 1. The API sleeps — and that is visible to users

Render spins a free service down after **15 minutes without traffic**. The
next request wakes it, taking roughly **50–60 seconds**.

What the app does about it: the client detects a slow request and shows
*"Waking the server up — this can take up to a minute on the free hosting
tier"*, and it pings `/health` as soon as the app loads so the wake overlaps
with the user reading the welcome screen. That makes it survivable, not good.

A **WebSocket cannot connect while the service is asleep**, so the first chat
message after an idle period waits for the wake too. The client reconnects
automatically with backoff, so it recovers on its own.

> Render's own documentation says not to use free instances for production.
> Keep-alive pingers are a widely-shared workaround, but they consume your 750
> monthly hours (a month is ~730 hours, so a permanently-awake service uses
> essentially all of them) and they are not supported.

### 2. Email only reaches you, unless you own a domain

This is the one that decides whether **real users can register**.

Resend's shared `onboarding@resend.dev` sender can only deliver to the email
address that owns the Resend account. Sending anywhere else returns `403`. To
email arbitrary users you must verify a domain you control via DNS.

So on a strict $0 budget you choose one of:

| Option | Real users can sign up? | Trade-off |
| --- | --- | --- |
| **A.** Buy a cheap domain (~$1–3/yr for `.xyz`) and verify it with Resend | Yes | Not strictly $0, but the cheapest real fix |
| **B.** Set `REQUIRE_EMAIL_VERIFICATION=false` | Yes | **No proof anyone owns their address. Password reset does not work.** |
| **C.** Leave verification on, no domain | No — only you | Fine for a portfolio demo |

Option B is implemented and tested, and the API logs a loud warning on every
boot while it is active. Understand what it costs: anyone can register with
someone else's address, there is no account recovery, and spam signups are
unimpeded. It is a demo setting, not a launch setting.

### 3. The database pauses after a week

Supabase pauses a free project after **7 days of inactivity**. Restoring it is
a button in their dashboard, but the API will fail until you press it. If
nobody uses your app for a week, it is down until you notice.

### 4. One instance, so chat is single-process

Render free runs exactly one instance with no autoscaling. The app's Redis
pub/sub fan-out exists to deliver a chat message to a recipient connected to a
*different* worker — with one worker there is no such recipient, so
`WS_FANOUT_ENABLED=false` is set and messages are delivered in-process.

This is correct and tested for one instance. **If you ever scale beyond one
instance, chat will silently half-break** — messages will reach only users who
happen to land on the sender's instance. At that point you need a Redis that
supports pub/sub over TCP and must set `WS_FANOUT_ENABLED=true`.

Upstash is still used for **rate limiting**, which only needs `INCR`/`EXPIRE`.

### 5. Storage and quotas

1 GB of Supabase Storage is roughly **1,000 profile photos** at 1 MB each.
That is the tightest quota in the stack and the first one you will hit. The
5 GB monthly egress allowance matters too: a photo-heavy feed serves the same
images repeatedly, and discovery is almost entirely photo traffic.

Cloudflare R2 would give 10 GB and unmetered egress for the same $0, but only
after a checkout flow — see [step 3](#-3-supabase-storage--photos-no-extra-account).

Upstash's free tier is **10,000 commands/day**. Every rate-limit check is one
or two commands, so a few thousand requests a day is fine; a traffic spike will
exhaust it, and the limiter then **fails open** (allows the request and logs an
error) rather than locking everyone out.

---

## Automated provisioning

Most of this is automated. Once you have created the accounts and generated
API tokens — the parts that genuinely require a browser and accepting terms —
the script does the rest:

```bash
cp deploy/tokens.env.example deploy/.secrets/tokens.env
# paste in whichever tokens you have
python deploy/provision-free.py --status     # what is ready, what is blocked
python deploy/provision-free.py --dry-run    # show the calls without making them
python deploy/provision-free.py              # provision
```

It creates the Supabase project, the Upstash database, and the Render service
with all its environment variables, then sets the GitHub Actions variable and
triggers a frontend rebuild. Every step is idempotent, so re-running after
filling in a missing token simply continues from where it stopped. Secrets are
masked in all output and `deploy/.secrets/` is git-ignored.

It will **ask before creating the Render service**, since that is the step
that publishes the backend publicly.

Then verify the result end to end:

```bash
python deploy/verify-deployment.py https://<your-service>.onrender.com
```

## Account checklist

Four accounts, none requiring a card. Tick them off in order.

### ☐ 1. Supabase — PostgreSQL

1. supabase.com → new project. Choose a region near your Render region.
2. Save the database password it shows you; it is not shown again.
3. **Connect** → **Connection string** → **Transaction pooler** (port 6543).
   You need a URL shaped like:

   ```
   postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres
   ```

> **Use the pooler host, not `db.<ref>.supabase.co`.** The direct host is
> IPv6-only and is not available on the free plan, so Render cannot reach it.
> The app detects the pooler automatically and disables asyncpg's prepared
> statement cache — without that you get intermittent
> `InvalidSQLStatementNameError` under concurrency, which passes every local
> test and only fails in production.

### ☐ 2. Upstash — Redis

1. upstash.com → **Create database** → Regional, near your Render region.
2. Copy the **TCP** connection string (`rediss://default:<pw>@<host>:6379`),
   not the REST URL.

### ☐ 3. Supabase Storage — photos (no extra account)

Photos live in the Supabase project you already created in step 1, so there is
nothing new to sign up for.

1. Supabase dashboard → **Storage** → *New bucket* named `amora-photos`.
2. **Keep it private** (leave "Public bucket" off). The app serves photos
   through presigned URLs that expire; a public bucket would make every user's
   photos permanently world-readable.
3. **Project Settings → Storage → S3 access keys** → *New access key*.
4. Note the Access Key ID, the Secret Access Key, and the S3 endpoint:
   `https://<project-ref>.storage.supabase.co/storage/v1/s3`

> **Why not Cloudflare R2?** R2 is the better service — 10 GB and zero egress
> charges, against Supabase's 1 GB and 5 GB monthly egress. But Cloudflare's
> own documentation says you must complete a *"checkout flow"* and that
> *"You must purchase R2 before you can generate an API token."* That is a
> dashboard-only step with no API route, and it conflicts with a strict
> no-payment-method budget.
>
> Switching to R2 later needs **no code changes** — point `S3_ENDPOINT_URL`,
> `S3_PUBLIC_ENDPOINT_URL`, `S3_ACCESS_KEY` and `S3_SECRET_KEY` at it and set
> `S3_REGION=auto`. The app's S3 client already uses SigV4 with path-style
> addressing, which both services require.

### ☐ 4. Resend — email

1. resend.com → **API Keys** → create one with *Sending access*.
2. **If you have a domain**, add and verify it, then use an address on it.
3. **If you do not**, you can only email yourself — see
   [limitation 2](#2-email-only-reaches-you-unless-you-own-a-domain).

### ☐ 5. Render — the API

1. render.com → **New** → **Blueprint** → connect this repository.
   Render reads [`render.yaml`](render.yaml) and creates the service.
2. Fill in the prompted secrets:

   | Variable | Value |
   | --- | --- |
   | `DATABASE_URL_OVERRIDE` | the Supabase **pooler** URL from step 1 |
   | `REDIS_URL` | the Upstash `rediss://` URL from step 2 |
   | `S3_ENDPOINT_URL` | `https://<ACCOUNT_ID>.r2.cloudflarestorage.com` |
   | `S3_PUBLIC_ENDPOINT_URL` | the same value |
   | `S3_ACCESS_KEY` / `S3_SECRET_KEY` | from step 3 |
   | `RESEND_API_KEY` | from step 4 |
   | `SMTP_FROM` | an address on your verified domain |
   | `FRONTEND_BASE_URL` | `https://<you>.github.io/amora` |
   | `CORS_ORIGINS` | `https://<you>.github.io` — **origin only, no path** |

   `SECRET_KEY` is generated by Render automatically.

3. Deploy. The container applies Alembic migrations on boot, so your Supabase
   database gets its schema with no manual step.
4. Check it: `https://<service>.onrender.com/health/ready` should report
   `database`, `redis` and `object_storage` all `ok`. If one is `unavailable`,
   that credential is wrong — the probe tells you which.

### ☐ 6. Point the frontend at the API

GitHub → **Settings → Secrets and variables → Actions → Variables** →
new repository variable:

| Name | Value |
| --- | --- |
| `EXPO_PUBLIC_API_URL` | `https://<service>.onrender.com/api/v1` |

Re-run the *Deploy web app to Pages* workflow. The build fails deliberately if
the URL is not `https://`, because GitHub Pages is HTTPS and a plain-HTTP API
would be blocked as mixed content.

`wss://` for chat is derived automatically from that URL and is verified by
tests.

### ☐ 7. Make yourself an admin

Production has no seeded accounts — `app/seed.py` refuses to run when
`APP_ENV=production`, and a test asserts it. Register normally, then in the
Supabase **SQL Editor**:

```sql
UPDATE users SET is_admin = true WHERE email = 'you@example.com';
```

You need this: photos stay invisible until a moderator approves them, so
without an admin nobody becomes discoverable.

---

## Never commit credentials

Every secret above lives in Render's encrypted environment settings or
GitHub's variables. `.gitignore` excludes `.env*`, and nothing in the
repository contains a real key. `EXPO_PUBLIC_API_URL` is deliberately a
*variable* rather than a secret: it is compiled into a public JavaScript
bundle, so it is not confidential, and masking it would only make a wrong
value harder to debug.

If you ever paste a key into a file, rotate it — assume anything committed to
a public repository is permanently public.

---

## Backups

Supabase's free plan does **not** include automatic backups. Take them
yourself.

**Manual export** (run locally; `pg_dump` must match the server's major
version):

```bash
pg_dump "postgresql://postgres.<ref>:<pw>@aws-0-<region>.pooler.supabase.com:5432/postgres" \
  --clean --if-exists --no-owner --no-privileges \
  | gzip -9 > "amora-$(date +%F).sql.gz"
```

> Use port **5432** (session mode) for `pg_dump`, not 6543. Transaction
> pooling breaks the long-lived snapshot a dump needs.

**Restore:**

```bash
gunzip -c amora-2026-10-14.sql.gz \
  | psql "postgresql://postgres.<ref>:<pw>@aws-0-<region>.pooler.supabase.com:5432/postgres"
```

**Photos** live in R2 and are not covered by a database dump:

```bash
rclone sync r2:amora-photos ./photo-backup    # configure rclone with your R2 keys
```

Do a restore drill once, into a scratch Supabase project, before you have
anything worth losing. An untested backup is not a backup.

---

## Is this production-ready?

**No — and a successful deploy does not make it so.** It is a working public
demo. Specifically:

- The host sleeps, so the first visitor each hour waits a minute.
- Without a domain, either nobody but you can receive email, or accounts are
  unverified and unrecoverable.
- The database pauses after a week of quiet and needs a manual click.
- Backups are manual and, until you run one, nonexistent.
- One instance means no redundancy: a crash is an outage until Render restarts
  it.
- The legal documents are placeholder copy that no lawyer has reviewed, which
  matters more for a dating app than for most products.

For something people rely on, the ~€4.50/month path in
[DEPLOYMENT.md](DEPLOYMENT.md) removes the first four problems outright.
