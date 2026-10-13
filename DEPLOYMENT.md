# Deploying Amora

How to take Amora from "runs on my laptop" to "real users can register, upload
photos, match and chat". Everything in this repository is ready; what remains
needs your accounts and a small monthly spend.

**Nothing here has been deployed.** No resources have been created and no
charges incurred on your behalf. The steps below are tested as far as they can
be locally — the configuration validator, the production compose file and the
image build all run clean on this machine.

---

## What it costs

| Component | Service | Cost |
| --- | --- | --- |
| API + PostgreSQL + Redis | One small VPS (Hetzner CX22, 2 vCPU / 4GB) | **~€4.50/mo** |
| Photo storage | Cloudflare R2 — 10GB, zero egress | **free** |
| Email | Resend — 3,000/mo, 100/day | **free** |
| Frontend | GitHub Pages | **free** |
| TLS certificates | Let's Encrypt via Caddy | **free** |
| Domain | Any registrar | ~$10–15/yr |

**Total: roughly €4.50/month plus a domain.**

### Why this shape

The expensive parts of a managed stack are the database and the cache. Render
charges about $7 for Postgres and $10 for a Redis-compatible store on top of
$7 for the web service — around $30/month for what one €4.50 VPS runs
comfortably at this scale. Running them as containers on the same host is the
honest trade: you accept operating them (the stack includes nightly backups)
in exchange for a 6x cost reduction.

Storage and email are the opposite case — their free tiers are generous and
genuinely free, and self-hosting either would be worse in every way. R2 in
particular charges **nothing for egress**, which matters because this app
serves a lot of photos.

**If you would rather not operate a database**, Railway runs this same compose
stack with managed Postgres and Redis for roughly $5–10/month. The application
code is identical; only the environment variables change.

---

## Step 1 — Get a server and a domain

1. Create a VPS (Hetzner, DigitalOcean, Vultr — any Debian 12 or Ubuntu 24.04
   box with 2GB+ RAM).
2. Point a DNS **A record** at its IP address, e.g. `api.yourdomain.com`.
   Caddy requests a certificate for exactly this name on first boot, so DNS
   must resolve *before* you deploy.
3. Prepare the server:

   ```bash
   ssh root@YOUR_SERVER_IP
   curl -fsSL https://raw.githubusercontent.com/Prakhar924/amora/main/deploy/provision.sh | bash
   ```

   This installs Docker, creates `/opt/amora`, enables a firewall that allows
   only SSH and HTTPS, turns on automatic security updates, and adds swap.

---

## Step 2 — Create the two free accounts

### Cloudflare R2 (photo storage)

1. Cloudflare dashboard → **R2** → *Create bucket* → name it `amora-photos`.
2. Keep it **private**. The app serves photos through short-lived presigned
   URLs; a public bucket would make every user's photos permanently
   world-readable.
3. **Manage R2 API Tokens** → *Create API token* → permission **Object Read &
   Write**, scoped to that bucket.
4. Note the Access Key ID, Secret Access Key, and your endpoint:
   `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`

### Resend (email)

1. Sign up at resend.com and **verify your sending domain** (a few DNS
   records). Without a verified domain, verification emails land in spam.
2. **API Keys** → create one with *Sending access*.
3. SMTP settings: host `smtp.resend.com`, port `587`, username the literal
   string `resend`, password your API key.

> The free tier allows **100 emails/day**. That covers registration and
> password resets for a soft launch, not a marketing push.

---

## Step 3 — Generate and check the configuration

On your workstation, in the repository:

```bash
python deploy/generate-secrets.py -o .env.production
```

This creates strong, unique values for `SECRET_KEY`, `POSTGRES_PASSWORD` and
`REDIS_PASSWORD`. Open the file and replace every `REPLACE_ME` and
`example.com` with your real values, then validate:

```bash
python deploy/check-config.py .env.production
```

It refuses to pass while any placeholder remains, and it runs the
application's own production guard — the same checks the API performs at boot,
so a config that passes here will start.

Copy it to the server **once**. The deploy script never transmits secrets:

```bash
scp .env.production root@YOUR_SERVER_IP:/opt/amora/
ssh root@YOUR_SERVER_IP 'chmod 600 /opt/amora/.env.production'
```

---

## Step 4 — Deploy

```bash
./deploy/deploy.sh root@YOUR_SERVER_IP
```

The script syncs the code, builds the image on the server, runs migrations as
a separate step (so a failed migration never leaves new code on an old
schema), starts the stack, and waits for `/health/ready` to report every
dependency healthy before declaring success.

Verify:

```bash
curl https://api.yourdomain.com/health/ready
# {"status":"ok","checks":{"api":"ok","database":"ok","redis":"ok","object_storage":"ok"}}
```

The first request may take a minute while Caddy obtains its certificate.

---

## Step 5 — Point the frontend at it

In GitHub → **Settings → Secrets and variables → Actions → Variables → New
repository variable**:

| Name | Value |
| --- | --- |
| `EXPO_PUBLIC_API_URL` | `https://api.yourdomain.com/api/v1` |

Then re-run the *Deploy web app to Pages* workflow (Actions tab → Run
workflow). The build fails deliberately if the URL is not `https://`, because
GitHub Pages is served over HTTPS and a plain-HTTP API would be blocked by the
browser as mixed content — a failure that otherwise looks like a server
outage.

A variable, not a secret: the value is compiled into a public JavaScript
bundle, so it is not confidential, and secrets are masked in logs, which makes
a wrong value much harder to debug.

Once set, the "no backend attached" notice on the welcome screen disappears
automatically and sign-in works.

---

## Step 6 — Create your admin account

There are no seeded accounts in production — `app/seed.py` refuses to run with
`APP_ENV=production`, and a test asserts it. Register through the app like any
user, then promote yourself:

```bash
ssh root@YOUR_SERVER_IP
cd /opt/amora
docker compose -f docker-compose.prod.yml --env-file .env.production \
  exec postgres psql -U amora -d amora \
  -c "UPDATE users SET is_admin = true WHERE email = 'you@yourdomain.com';"
```

The moderation dashboard then appears under *Me → Moderation dashboard*. You
need it: photos stay invisible until approved, so without a moderator nobody
becomes discoverable.

---

## Operating it

```bash
cd /opt/amora
C="docker compose -f docker-compose.prod.yml --env-file .env.production"

$C ps                      # service status
$C logs -f api             # follow API logs
$C exec api alembic upgrade head   # apply migrations manually
$C restart api             # restart just the API
```

**Deploying an update** is the same command as the first deploy:
`./deploy/deploy.sh root@YOUR_SERVER_IP`.

### Backups

A `backup` container dumps the database nightly to a Docker volume, keeping 7
days. **This is not yet a real backup** — it lives on the same machine as the
database, so it protects against a dropped table, not a dead server.

To make it one, set `S3_BACKUP_BUCKET` in `.env.production` and use an image
with the AWS CLI, or pull the dumps from your workstation on a schedule:

```bash
ssh root@YOUR_SERVER_IP \
  'docker run --rm -v amora-prod_postgres_backups:/b alpine tar cz -C /b .' \
  > "amora-backups-$(date +%F).tar.gz"
```

**Restoring:**

```bash
$C exec -T postgres psql -U amora -d amora < <(gunzip -c backup.sql.gz)
```

Test this before you need it.

---

## What is enforced in production

The API refuses to start if any of these is wrong — it is not advisory:

| Check | Why |
| --- | --- |
| `SECRET_KEY` unique, ≥32 chars | A default key lets anyone forge a token for any account |
| `POSTGRES_PASSWORD` not a default | |
| `S3_SECRET_KEY` not a dev value | |
| `DEBUG=false` | Debug output leaks internals |
| `CORS_ORIGINS` all `https://`, no localhost | Tokens must not cross plain HTTP; a localhost origin means the frontend was never configured |
| `ALLOWED_HOSTS` not `*` | A wildcard allows Host-header poisoning of emailed reset links |
| `S3_PUBLIC_ENDPOINT_URL` / `FRONTEND_BASE_URL` are `https://` | |
| `EMAIL_CONSOLE_FALLBACK=false` | Otherwise a failed password-reset email is silently logged and the user waits forever |
| `SMTP_HOST` is a real provider | |
| `BCRYPT_ROUNDS` ≥ 10 | |

Beyond that, the production stack differs from development in ways that
matter:

- **Postgres and Redis publish no host ports.** They are reachable only on the
  internal Docker network, so the database is not exposed even if the firewall
  is misconfigured.
- **Redis requires a password**, because it holds rate-limit state and the
  chat fan-out.
- **OpenAPI docs are off** (`ENABLE_DOCS=false`). The schema is a map of the
  attack surface.
- **The bucket is private**; photos are served only through presigned URLs
  that expire after 15 minutes.
- **Security headers** (HSTS, `nosniff`, `DENY` framing, a `default-src 'none'`
  CSP) are set by both the API and Caddy, so the API stays protected if it is
  ever fronted by something else.
- **Log rotation and memory limits** on every container, so one runaway
  process cannot fill the disk or take down the host.

---

## Before real users

See [TODO.md](TODO.md) for the full list. The ones that genuinely block a
public launch:

1. **Replace the placeholder legal documents.** They describe what the
   software does but have not been reviewed by a lawyer. A dating app handles
   sensitive personal data under GDPR-style rules.
2. **Verify your email sending domain**, or verification mail goes to spam.
3. **Test a backup restore.** An untested backup is not a backup.
4. **Plan moderation.** Photos queue for human approval; nobody becomes
   discoverable until someone clicks approve.
5. **Decide on an abuse budget.** The free Resend tier is 100 emails/day; a
   spike of registrations will hit it.
