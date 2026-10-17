#!/usr/bin/env python3
"""Provision the $0 Amora stack from provider APIs.

    python deploy/provision-free.py --status      # what is done, what is blocked
    python deploy/provision-free.py --dry-run     # show calls without making them
    python deploy/provision-free.py               # provision everything possible

Reads API tokens from `deploy/.secrets/tokens.env` (git-ignored) and creates:

  * a Supabase Postgres project          (Management API)
  * an Upstash Redis database            (Developer API)
  * a Render web service, with env vars  (REST API)
  * the GitHub Actions variable that points the published frontend at it

Every step is idempotent — it reuses a resource that already exists rather
than creating a second one — so the script is safe to re-run after fixing a
credential or filling in a value that was missing.

Two things it deliberately does NOT do, because no provider API supports them:

  * create accounts, or accept terms of service
  * create Supabase Storage S3 access keys, which are dashboard-only

Secrets are masked in all output. Nothing is ever written to the repository.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import string
import sys
import time
from pathlib import Path
from typing import Any

try:
    import httpx
except ImportError:  # pragma: no cover
    sys.exit("This script needs httpx:  pip install httpx")

ROOT = Path(__file__).resolve().parent.parent
SECRETS_DIR = ROOT / "deploy" / ".secrets"
TOKENS_FILE = SECRETS_DIR / "tokens.env"
STATE_FILE = SECRETS_DIR / "state.json"

GITHUB_REPO = "Prakhar924/amora"
FRONTEND_ORIGIN = "https://prakhar924.github.io"
FRONTEND_URL = f"{FRONTEND_ORIGIN}/amora"
SERVICE_NAME = "amora-api"
BUCKET = "amora-photos"

GREEN, RED, YELLOW, BLUE, DIM, BOLD, RESET = (
    "\033[32m", "\033[31m", "\033[33m", "\033[36m", "\033[2m", "\033[1m", "\033[0m",
)

DRY_RUN = False


# --------------------------------------------------------------------------
# Output helpers — these are the only place anything is printed, so masking
# can be enforced in one spot.
# --------------------------------------------------------------------------
def step(text: str) -> None:
    print(f"\n{BOLD}{BLUE}==>{RESET} {BOLD}{text}{RESET}")


def ok(text: str) -> None:
    print(f"  {GREEN}OK{RESET}    {text}")


def info(text: str) -> None:
    print(f"  {DIM}·     {text}{RESET}")


def warn(text: str) -> None:
    print(f"  {YELLOW}WARN{RESET}  {text}")


def fail(text: str) -> None:
    print(f"  {RED}FAIL{RESET}  {text}")


def blocked(what: str, action: str) -> None:
    print(f"  {YELLOW}BLOCKED{RESET} {what}")
    for line in action.strip().splitlines():
        print(f"          {line}")


def mask(value: str | None) -> str:
    """Render a secret as a short fingerprint, never in full."""
    if not value:
        return "(unset)"
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}…{value[-2:]} ({len(value)} chars)"


def generate_password(length: int = 32) -> str:
    # Alphanumeric only: these end up inside connection URLs, where '@', ':'
    # and '/' need escaping and will eventually be got wrong by somebody.
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


# --------------------------------------------------------------------------
# Token + state handling
# --------------------------------------------------------------------------
def load_tokens() -> dict[str, str]:
    if not TOKENS_FILE.is_file():
        return {}
    values: dict[str, str] = {}
    for raw in TOKENS_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        cleaned = value.strip().strip('"').strip("'")
        if cleaned:
            values[key.strip()] = cleaned
    return values


def load_state() -> dict[str, Any]:
    if STATE_FILE.is_file():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def save_state(state: dict[str, Any]) -> None:
    """Persist non-secret resource identifiers so re-runs are idempotent.

    Connection strings contain passwords, so the file is written with
    restrictive permissions and lives in the git-ignored secrets directory.
    """
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    try:
        STATE_FILE.chmod(0o600)
    except OSError:
        pass  # Windows; the directory is git-ignored regardless.


def git_token() -> str | None:
    """Reuse the credential git already has, rather than asking for another."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n\n",
            capture_output=True,
            text=True,
            timeout=15,
            cwd=ROOT,
        )
        for line in result.stdout.splitlines():
            if line.startswith("password="):
                return line.split("=", 1)[1]
    except Exception:  # noqa: BLE001
        pass
    return None


# --------------------------------------------------------------------------
# Supabase — PostgreSQL
# --------------------------------------------------------------------------
SUPABASE_API = "https://api.supabase.com/v1"


def provision_supabase(tokens: dict[str, str], state: dict[str, Any]) -> None:
    step("Supabase — PostgreSQL")
    token = tokens.get("SUPABASE_ACCESS_TOKEN")
    if not token:
        blocked(
            "no SUPABASE_ACCESS_TOKEN",
            "1. Sign in at https://supabase.com (GitHub login works)\n"
            "2. Account Settings -> Access Tokens -> Generate new token\n"
            "3. Put it in deploy/.secrets/tokens.env as SUPABASE_ACCESS_TOKEN=",
        )
        return

    headers = {"Authorization": f"Bearer {token}"}
    info(f"token {mask(token)}")

    with httpx.Client(timeout=60, headers=headers) as client:
        # --- organization ------------------------------------------------
        response = client.get(f"{SUPABASE_API}/organizations")
        if response.status_code == 401:
            fail("Supabase rejected the token (401). Generate a fresh one.")
            return
        if response.status_code != 200:
            fail(f"could not list organizations: HTTP {response.status_code}")
            return

        orgs = response.json()
        if not orgs:
            blocked(
                "the Supabase account has no organization",
                "Create one at https://supabase.com/dashboard, then re-run.",
            )
            return
        org_slug = orgs[0]["slug"]
        ok(f"organization: {orgs[0].get('name', org_slug)}")

        # --- reuse an existing project if there is one --------------------
        ref = tokens.get("SUPABASE_PROJECT_REF") or state.get("supabase_ref")
        if not ref:
            projects = client.get(f"{SUPABASE_API}/projects")
            if projects.status_code == 200:
                for project in projects.json():
                    if project.get("name") == "amora":
                        ref = project["ref"]
                        ok(f"reusing existing project '{project['name']}' ({ref})")
                        break

        # --- create ------------------------------------------------------
        if not ref:
            db_password = tokens.get("SUPABASE_DB_PASSWORD") or generate_password()

            regions = client.get(f"{SUPABASE_API}/projects-available-regions")
            region = None
            if regions.status_code == 200:
                try:
                    available = regions.json()
                    names = [
                        r.get("name") if isinstance(r, dict) else r for r in available
                    ]
                    # Prefer a European region to sit near Render's frankfurt.
                    region = next(
                        (n for n in names if n and "eu" in str(n).lower()), names[0]
                    )
                except Exception:  # noqa: BLE001
                    region = None
            region = region or "eu-west-1"

            payload = {
                "name": "amora",
                "organization_slug": org_slug,
                "db_pass": db_password,
                "region": region,
            }
            if DRY_RUN:
                info(f"[dry-run] POST {SUPABASE_API}/projects region={region}")
                return

            created = client.post(f"{SUPABASE_API}/projects", json=payload)
            if created.status_code not in (200, 201):
                fail(
                    f"project creation failed: HTTP {created.status_code} "
                    f"{created.text[:200]}"
                )
                return
            body = created.json()
            ref = body["ref"]
            state["supabase_db_password"] = db_password
            ok(f"created project 'amora' ({ref}) in {region}")

            # Provisioning takes a couple of minutes.
            info("waiting for the project to become ACTIVE (can take ~2 min)")
            deadline = time.monotonic() + 420
            while time.monotonic() < deadline:
                status_response = client.get(f"{SUPABASE_API}/projects/{ref}")
                if status_response.status_code == 200:
                    status = status_response.json().get("status", "")
                    if status == "ACTIVE_HEALTHY":
                        ok("project is active")
                        break
                    info(f"status: {status}")
                time.sleep(15)
            else:
                warn("project is still provisioning; re-run later to finish")

        state["supabase_ref"] = ref

        # --- connection string -------------------------------------------
        password = (
            tokens.get("SUPABASE_DB_PASSWORD")
            or state.get("supabase_db_password")
        )
        if not password:
            blocked(
                "the database password is not known",
                "It is shown only once, at project creation. Either put it in\n"
                "tokens.env as SUPABASE_DB_PASSWORD=, or reset it at\n"
                "Project Settings -> Database -> Reset database password.",
            )
            return

        # The pooler host is not returned by the API, so it is constructed and
        # then VERIFIED by connecting. The transaction pooler (6543) is the
        # only option that works from Render: the direct db.<ref>.supabase.co
        # host is IPv6-only and not available on the free plan.
        project = client.get(f"{SUPABASE_API}/projects/{ref}")
        region = project.json().get("region", "eu-west-1") if project.status_code == 200 else "eu-west-1"
        url = (
            f"postgresql://postgres.{ref}:{password}"
            f"@aws-0-{region}.pooler.supabase.com:6543/postgres"
        )
        state["database_url"] = url
        ok(f"pooler connection string built for region {region}")
        info("verify it with:  python deploy/provision-free.py --check-db")


def check_database(state: dict[str, Any]) -> bool:
    """Actually connect, because a constructed URL is a guess until it works."""
    step("Verifying the database connection")
    url = state.get("database_url")
    if not url:
        fail("no database URL in state; run provisioning first")
        return False
    try:
        import psycopg
    except ImportError:
        warn("psycopg not installed here; skipping (the API will verify on boot)")
        return True

    sync_url = url.replace("postgresql://", "postgresql://", 1)
    try:
        with psycopg.connect(sync_url, connect_timeout=20) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT version()")
                version = cur.fetchone()[0]
        ok(f"connected: {version.split(',')[0]}")
        return True
    except Exception as exc:  # noqa: BLE001
        fail(f"could not connect: {str(exc)[:200]}")
        info(
            "If the host is wrong, copy the exact Transaction-pooler string "
            "from the Supabase dashboard (Connect -> Transaction pooler)."
        )
        return False


# --------------------------------------------------------------------------
# Upstash — Redis
# --------------------------------------------------------------------------
UPSTASH_API = "https://api.upstash.com/v2"


def provision_upstash(tokens: dict[str, str], state: dict[str, Any]) -> None:
    step("Upstash — Redis")
    email = tokens.get("UPSTASH_EMAIL")
    api_key = tokens.get("UPSTASH_API_KEY")
    if not (email and api_key):
        blocked(
            "no UPSTASH_EMAIL / UPSTASH_API_KEY",
            "1. Sign in at https://console.upstash.com\n"
            "2. Account -> Management API -> Create API key\n"
            "3. Put your account email and the key in tokens.env",
        )
        return

    auth = (email, api_key)
    info(f"key {mask(api_key)}")

    with httpx.Client(timeout=60, auth=auth) as client:
        existing = client.get(f"{UPSTASH_API}/redis/databases")
        if existing.status_code == 401:
            fail("Upstash rejected the credentials (401)")
            return
        if existing.status_code != 200:
            fail(f"could not list databases: HTTP {existing.status_code}")
            return

        database = next(
            (d for d in existing.json() if d.get("database_name") == "amora"), None
        )

        if database is None:
            payload = {
                "database_name": "amora",
                "platform": "aws",
                # Keep this near the Render region to minimise latency.
                "primary_region": "eu-west-1",
                "tls": True,
                # Rate-limit counters are disposable; evicting under pressure
                # beats refusing writes.
                "eviction": True,
            }
            if DRY_RUN:
                info(f"[dry-run] POST {UPSTASH_API}/redis/database")
                return
            created = client.post(f"{UPSTASH_API}/redis/database", json=payload)
            if created.status_code not in (200, 201):
                fail(
                    f"creation failed: HTTP {created.status_code} "
                    f"{created.text[:200]}"
                )
                return
            database = created.json()
            ok("created Redis database 'amora'")
        else:
            ok(f"reusing existing database '{database.get('database_name')}'")

        # The password field is not in the published response schema, so find
        # it rather than assuming a name.
        password = next(
            (
                str(value)
                for key, value in database.items()
                if "password" in key.lower() and value
            ),
            None,
        )
        endpoint = database.get("endpoint")
        port = database.get("port", 6379)

        if not (password and endpoint):
            database_id = database.get("database_id")
            if database_id:
                detail = client.get(f"{UPSTASH_API}/redis/database/{database_id}")
                if detail.status_code == 200:
                    data = detail.json()
                    endpoint = endpoint or data.get("endpoint")
                    port = data.get("port", port)
                    password = password or next(
                        (
                            str(v)
                            for k, v in data.items()
                            if "password" in k.lower() and v
                        ),
                        None,
                    )

        if not (password and endpoint):
            blocked(
                "Upstash did not return a password in its response",
                "Copy the connection string from the Upstash console\n"
                "(your database -> Connect -> redis-cli / TLS) and put it in\n"
                "tokens.env as UPSTASH_REDIS_URL=",
            )
            return

        # rediss:// — TLS. Upstash requires it, and so does any sane deployment.
        url = f"rediss://default:{password}@{endpoint}:{port}"
        state["redis_url"] = url
        ok(f"connection string built for {endpoint}")


# --------------------------------------------------------------------------
# Render — the API service
# --------------------------------------------------------------------------
RENDER_API = "https://api.render.com/v1"


def build_render_env(tokens: dict[str, str], state: dict[str, Any]) -> list[dict]:
    """Environment variables for the Render service.

    Mirrors render.yaml. Values that only exist after provisioning (database,
    Redis) come from state; the rest come from tokens or are fixed.
    """
    supabase_ref = state.get("supabase_ref", "")
    s3_endpoint = (
        f"https://{supabase_ref}.storage.supabase.co/storage/v1/s3"
        if supabase_ref
        else ""
    )
    env = {
        "APP_ENV": "production",
        "DEBUG": "false",
        "WEB_CONCURRENCY": "1",
        "WS_FANOUT_ENABLED": "false",
        "ENABLE_DOCS": "false",
        "REQUIRE_PHOTO_MODERATION": "true",
        "BCRYPT_ROUNDS": "12",
        "EMAIL_TRANSPORT": "resend",
        "EMAIL_CONSOLE_FALLBACK": "false",
        "FRONTEND_BASE_URL": FRONTEND_URL,
        "CORS_ORIGINS": FRONTEND_ORIGIN,
        "S3_BUCKET": BUCKET,
        "S3_ENDPOINT_URL": s3_endpoint,
        "S3_PUBLIC_ENDPOINT_URL": s3_endpoint,
        "S3_REGION": state.get("supabase_region", "eu-west-1"),
        "DATABASE_URL_OVERRIDE": state.get("database_url", ""),
        "REDIS_URL": state.get("redis_url", ""),
        "RESEND_API_KEY": tokens.get("RESEND_API_KEY", ""),
        "SMTP_FROM": tokens.get("RESEND_FROM", "onboarding@resend.dev"),
        "S3_ACCESS_KEY": tokens.get("SUPABASE_S3_ACCESS_KEY", ""),
        "S3_SECRET_KEY": tokens.get("SUPABASE_S3_SECRET_KEY", ""),
    }
    pairs = [{"key": k, "value": v} for k, v in env.items() if v != ""]
    # Let Render mint the signing key; it never needs to exist on this machine.
    pairs.append({"key": "SECRET_KEY", "generateValue": True})
    return pairs


def provision_render(tokens: dict[str, str], state: dict[str, Any]) -> None:
    step("Render — API service")
    token = tokens.get("RENDER_API_KEY")
    if not token:
        blocked(
            "no RENDER_API_KEY",
            "1. Sign in at https://render.com (GitHub login works)\n"
            "2. Account Settings -> API Keys -> Create API key\n"
            "3. Put it in tokens.env as RENDER_API_KEY=",
        )
        return

    missing = [
        label
        for label, value in (
            ("database", state.get("database_url")),
            ("redis", state.get("redis_url")),
        )
        if not value
    ]
    if missing:
        warn(
            f"{', '.join(missing)} not provisioned yet — the service would "
            "start and fail its readiness check"
        )
        info("provision those first, then re-run")
        return

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    info(f"key {mask(token)}")

    with httpx.Client(timeout=90, headers=headers) as client:
        owners = client.get(f"{RENDER_API}/owners", params={"limit": 20})
        if owners.status_code == 401:
            fail("Render rejected the API key (401)")
            return
        if owners.status_code != 200 or not owners.json():
            fail(f"could not list workspaces: HTTP {owners.status_code}")
            return
        owner = owners.json()[0]["owner"]
        owner_id = owner["id"]
        ok(f"workspace: {owner.get('name', owner_id)}")

        services = client.get(
            f"{RENDER_API}/services", params={"name": SERVICE_NAME, "limit": 20}
        )
        existing = None
        if services.status_code == 200:
            for entry in services.json():
                if entry["service"]["name"] == SERVICE_NAME:
                    existing = entry["service"]
                    break

        env_vars = build_render_env(tokens, state)

        if existing:
            service_id = existing["id"]
            ok(f"reusing existing service ({service_id})")
            if DRY_RUN:
                info(f"[dry-run] PUT {RENDER_API}/services/{service_id}/env-vars")
                return
            updated = client.put(
                f"{RENDER_API}/services/{service_id}/env-vars", json=env_vars
            )
            if updated.status_code in (200, 201):
                ok(f"updated {len(env_vars)} environment variables")
            else:
                fail(f"env var update failed: HTTP {updated.status_code}")
            deploy = client.post(f"{RENDER_API}/services/{service_id}/deploys", json={})
            if deploy.status_code in (200, 201, 202):
                ok("triggered a redeploy")
            url = existing.get("serviceDetails", {}).get("url")
        else:
            payload = {
                "type": "web_service",
                "name": SERVICE_NAME,
                "ownerId": owner_id,
                "repo": f"https://github.com/{GITHUB_REPO}",
                "branch": "main",
                "autoDeployTrigger": "commit",
                "envVars": env_vars,
                "serviceDetails": {
                    "runtime": "docker",
                    "plan": "free",
                    "region": "frankfurt",
                    # Liveness, not readiness: a readiness failure from a bad
                    # storage key should not roll back an otherwise-good deploy.
                    "healthCheckPath": "/health",
                    "numInstances": 1,
                    "envSpecificDetails": {
                        "dockerfilePath": "./backend/Dockerfile",
                        "dockerContext": "./backend",
                    },
                },
            }
            if DRY_RUN:
                info(f"[dry-run] POST {RENDER_API}/services plan=free region=frankfurt")
                info(f"[dry-run] {len(env_vars)} environment variables")
                return

            print()
            warn("about to create a PUBLIC web service on Render (free plan)")
            info("this publishes the backend to the internet at a *.onrender.com URL")
            answer = input("  Proceed? [y/N] ").strip().lower()
            if answer != "y":
                blocked("not created", "Re-run and answer 'y' when ready.")
                return

            created = client.post(f"{RENDER_API}/services", json=payload)
            if created.status_code not in (200, 201):
                fail(
                    f"service creation failed: HTTP {created.status_code} "
                    f"{created.text[:300]}"
                )
                return
            body = created.json()
            service = body["service"]
            service_id = service["id"]
            url = service.get("serviceDetails", {}).get("url")
            ok(f"created service {service_id}")
            info("first build takes several minutes")

        if url:
            state["api_url"] = url
            state["render_service_id"] = service_id
            ok(f"API URL: {url}")


# --------------------------------------------------------------------------
# GitHub — point the published frontend at the backend
# --------------------------------------------------------------------------
def configure_frontend(tokens: dict[str, str], state: dict[str, Any]) -> None:
    step("GitHub Pages — frontend wiring")
    api_url = state.get("api_url")
    if not api_url:
        blocked(
            "the backend URL is not known yet",
            "Provision Render first; this step then runs automatically.",
        )
        return

    token = tokens.get("GITHUB_TOKEN") or git_token()
    if not token:
        blocked(
            "no GitHub token",
            "Put a token with the `repo` scope in tokens.env as GITHUB_TOKEN=,\n"
            "or sign in once with git so the credential helper has one.",
        )
        return

    value = f"{api_url.rstrip('/')}/api/v1"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    if DRY_RUN:
        info(f"[dry-run] set EXPO_PUBLIC_API_URL={value}")
        return

    with httpx.Client(timeout=60, headers=headers) as client:
        base = f"https://api.github.com/repos/{GITHUB_REPO}/actions/variables"
        existing = client.get(f"{base}/EXPO_PUBLIC_API_URL")
        if existing.status_code == 200:
            response = client.patch(
                f"{base}/EXPO_PUBLIC_API_URL",
                json={"name": "EXPO_PUBLIC_API_URL", "value": value},
            )
        else:
            response = client.post(
                base, json={"name": "EXPO_PUBLIC_API_URL", "value": value}
            )

        if response.status_code not in (200, 201, 204):
            fail(f"could not set the variable: HTTP {response.status_code}")
            return
        ok(f"EXPO_PUBLIC_API_URL = {value}")

        dispatch = client.post(
            f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/"
            "pages.yml/dispatches",
            json={"ref": "main"},
        )
        if dispatch.status_code == 204:
            ok("triggered a frontend rebuild")
            info(f"watch: https://github.com/{GITHUB_REPO}/actions")
        else:
            warn(
                "could not trigger the rebuild automatically "
                f"(HTTP {dispatch.status_code}); run it from the Actions tab"
            )


# --------------------------------------------------------------------------
# Status
# --------------------------------------------------------------------------
def print_status(tokens: dict[str, str], state: dict[str, Any]) -> None:
    step("Credentials")
    for key, label in (
        ("SUPABASE_ACCESS_TOKEN", "Supabase token"),
        ("SUPABASE_S3_ACCESS_KEY", "Supabase S3 key"),
        ("SUPABASE_S3_SECRET_KEY", "Supabase S3 secret"),
        ("UPSTASH_EMAIL", "Upstash email"),
        ("UPSTASH_API_KEY", "Upstash key"),
        ("RENDER_API_KEY", "Render key"),
        ("RESEND_API_KEY", "Resend key"),
    ):
        value = tokens.get(key)
        marker = f"{GREEN}set{RESET}" if value else f"{YELLOW}missing{RESET}"
        print(f"  {marker:<18} {label:<20} {mask(value) if value else ''}")

    step("Resources")
    for key, label in (
        ("supabase_ref", "Supabase project"),
        ("database_url", "Database URL"),
        ("redis_url", "Redis URL"),
        ("render_service_id", "Render service"),
        ("api_url", "API URL"),
    ):
        value = state.get(key)
        if not value:
            print(f"  {YELLOW}pending{RESET}            {label}")
        elif "url" in key and key != "api_url":
            print(f"  {GREEN}ready{RESET}              {label:<20} {mask(value)}")
        else:
            print(f"  {GREEN}ready{RESET}              {label:<20} {value}")


def main() -> int:
    global DRY_RUN

    # Windows consoles default to cp1252 and mangle the non-ASCII characters
    # in this script's output.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", action="store_true", help="show state and exit")
    parser.add_argument("--dry-run", action="store_true", help="make no changes")
    parser.add_argument("--check-db", action="store_true", help="test the DB connection")
    parser.add_argument(
        "--only",
        choices=["supabase", "upstash", "render", "frontend"],
        help="run a single step",
    )
    args = parser.parse_args()
    DRY_RUN = args.dry_run

    if not TOKENS_FILE.is_file():
        print(f"{YELLOW}No credential file yet.{RESET}\n")
        print("  cp deploy/tokens.env.example deploy/.secrets/tokens.env")
        print("  # fill in whichever tokens you have, then re-run\n")
        print("The script runs every step it has credentials for and reports")
        print("the rest as blocked, so partial progress is fine.")
        return 1

    tokens = load_tokens()
    state = load_state()

    if args.status:
        print_status(tokens, state)
        return 0

    if args.check_db:
        return 0 if check_database(state) else 1

    if DRY_RUN:
        print(f"{YELLOW}Dry run — no changes will be made.{RESET}")

    try:
        if args.only in (None, "supabase"):
            provision_supabase(tokens, state)
            save_state(state)
        if args.only in (None, "upstash"):
            provision_upstash(tokens, state)
            save_state(state)
        if args.only in (None, "render"):
            provision_render(tokens, state)
            save_state(state)
        if args.only in (None, "frontend"):
            configure_frontend(tokens, state)
            save_state(state)
    finally:
        save_state(state)

    print_status(tokens, state)

    if state.get("api_url"):
        print(f"\n{BOLD}Next:{RESET}")
        print(f"  python deploy/verify-deployment.py {state['api_url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
