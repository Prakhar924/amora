#!/usr/bin/env python3
"""End-to-end verification against a deployed Amora backend.

    python deploy/verify-deployment.py https://amora-api.onrender.com

Exercises the real product over HTTPS and WSS: registration, the 18+ gate,
authentication, presigned photo upload, discovery, mutual matching, real-time
chat, and the authorization boundaries. It creates its own throwaway accounts
and cleans them up by deleting them, so it is safe to re-run against a live
deployment.

Exit code is non-zero if any check fails, so CI can gate on it.

Why this exists separately from the test suite: the suite runs against a local
database with stubbed storage and email. This talks to the actual deployment
and so catches the things only a real environment can break — TLS, CORS, a
sleeping host, a wrong bucket credential, a WebSocket blocked by a proxy.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
import uuid
from datetime import date, timedelta
from typing import Any

try:
    import httpx
except ImportError:  # pragma: no cover
    sys.exit("This script needs httpx:  pip install httpx")

try:
    from websockets.sync.client import connect as ws_connect
except ImportError:  # pragma: no cover
    ws_connect = None


PASSWORD = "VerifyPass123!"


class RateLimited(Exception):
    """Raised when the API's abuse protection blocks this script."""

GREEN, RED, YELLOW, DIM, RESET = (
    "\033[32m",
    "\033[31m",
    "\033[33m",
    "\033[2m",
    "\033[0m",
)

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    mark = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  {mark}  {name}" + (f"  {DIM}{detail}{RESET}" if detail else ""))
    return ok


def section(title: str) -> None:
    print(f"\n{title}")


def dob(age: int) -> str:
    return (date.today() - timedelta(days=age * 365 + 40)).isoformat()


class Verifier:
    def __init__(self, base: str, timeout: float) -> None:
        self.base = base.rstrip("/")
        self.api = f"{self.base}/api/v1"
        self.client = httpx.Client(timeout=timeout, follow_redirects=True)
        self.created_tokens: list[str] = []

    # -- helpers ---------------------------------------------------------
    def h(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def register(self, label: str, gender: str = "woman") -> tuple[str, str]:
        """Register a throwaway user and return (email, access_token)."""
        email = f"verify-{label}-{uuid.uuid4().hex[:8]}@amora.dev"
        response = self.client.post(
            f"{self.api}/auth/register",
            json={
                "email": email,
                "password": PASSWORD,
                "display_name": f"Verify {label.title()}",
                "date_of_birth": dob(28),
                "gender": gender,
                "accept_terms": True,
                "accept_privacy": True,
            },
        )
        if response.status_code == 429:
            # The registration limiter is per-IP and deliberately tight
            # (5/hour). Hitting it proves the protection works; it just means
            # this script cannot run the journey again right now.
            raise RateLimited(
                "the registration rate limit is active for this IP"
            )
        if response.status_code != 202:
            raise RuntimeError(
                f"registration failed ({response.status_code}): {response.text[:300]}"
            )

        body = response.json()
        if body.get("verification_required", True):
            # Email verification is on, so this account cannot sign in without
            # clicking a link. The caller reports this as a skip, not a failure.
            raise PermissionError("verification_required")

        login = self.client.post(
            f"{self.api}/auth/login", json={"email": email, "password": PASSWORD}
        )
        login.raise_for_status()
        token = login.json()["access_token"]
        self.created_tokens.append(token)
        return email, token

    def cleanup(self) -> None:
        for token in self.created_tokens:
            try:
                self.client.request(
                    "DELETE",
                    f"{self.api}/auth/me",
                    headers=self.h(token),
                    json={"password": PASSWORD, "confirmation": "DELETE"},
                )
            except Exception:  # noqa: BLE001 - best effort
                pass
        self.client.close()

    # -- checks ----------------------------------------------------------
    def check_reachable(self) -> bool:
        section("Connectivity")
        # Loopback is exempt: the script doubles as a local smoke test, and a
        # development stack has no certificate. Anything else must be HTTPS,
        # because tokens and photos cross it.
        is_local = any(
            host in self.base for host in ("localhost", "127.0.0.1", "[::1]")
        )
        if is_local:
            print(
                f"  {YELLOW}note{RESET}  local target — skipping the HTTPS "
                "requirement"
            )
        else:
            if not self.base.startswith("https://"):
                check("API served over HTTPS", False, self.base)
                return False
            check("API served over HTTPS", True)

        started = time.monotonic()
        try:
            response = self.client.get(f"{self.base}/health")
        except Exception as exc:  # noqa: BLE001
            return check("GET /health", False, str(exc)[:120])

        elapsed = time.monotonic() - started
        ok = check("GET /health", response.status_code == 200, f"{elapsed:.1f}s")
        if elapsed > 10:
            print(
                f"  {YELLOW}note{RESET}  the host was asleep and took "
                f"{elapsed:.0f}s to wake — expected on a free tier"
            )
        return ok

    def check_dependencies(self) -> bool:
        response = self.client.get(f"{self.base}/health/ready")
        try:
            checks = response.json().get("checks", {})
        except json.JSONDecodeError:
            return check("GET /health/ready", False, "non-JSON response")

        all_ok = True
        for dependency in ("database", "redis", "object_storage"):
            state = checks.get(dependency, "missing")
            all_ok &= check(f"dependency: {dependency}", state == "ok", state)
        return all_ok

    def check_security_headers(self) -> None:
        section("Security")
        response = self.client.get(f"{self.base}/health")
        headers = {k.lower(): v for k, v in response.headers.items()}
        # HSTS is only emitted in production; absent locally by design.
        if self.base.startswith("https://"):
            check("HSTS header present", "strict-transport-security" in headers)
        check(
            "X-Content-Type-Options: nosniff",
            headers.get("x-content-type-options") == "nosniff",
        )
        check("X-Frame-Options: DENY", headers.get("x-frame-options") == "DENY")
        # Publishing the schema is expected in development and a finding only
        # against a real deployment, so do not fail a local run over it.
        docs = self.client.get(f"{self.base}/docs")
        if self.base.startswith("https://"):
            check(
                "OpenAPI docs not public",
                docs.status_code == 404,
                ""
                if docs.status_code == 404
                else "reachable — set ENABLE_DOCS=false",
            )
        elif docs.status_code == 200:
            print(f"  {DIM}note  OpenAPI docs are public (expected locally){RESET}")

    def check_cors(self, origin: str) -> None:
        response = self.client.options(
            f"{self.api}/auth/login",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        allowed = response.headers.get("access-control-allow-origin", "")
        check(
            f"CORS allows {origin}",
            allowed in (origin, "*"),
            allowed or "no allow-origin header",
        )

    def check_age_gate(self) -> None:
        section("Registration rules")
        response = self.client.post(
            f"{self.api}/auth/register",
            json={
                "email": f"minor-{uuid.uuid4().hex[:6]}@amora.dev",
                "password": PASSWORD,
                "display_name": "Too Young",
                "date_of_birth": dob(15),
                "gender": "woman",
                "accept_terms": True,
                "accept_privacy": True,
            },
        )
        check("under-18 registration rejected", response.status_code == 422)

        response = self.client.post(
            f"{self.api}/auth/register",
            json={
                "email": f"noconsent-{uuid.uuid4().hex[:6]}@amora.dev",
                "password": PASSWORD,
                "display_name": "No Consent",
                "date_of_birth": dob(30),
                "gender": "man",
                "accept_terms": False,
                "accept_privacy": True,
            },
        )
        check("registration without consent rejected", response.status_code == 422)

    def check_unauthenticated(self) -> None:
        response = self.client.get(f"{self.api}/discovery")
        check("unauthenticated request rejected", response.status_code == 401)

    def run_journey(self) -> dict[str, Any] | None:
        """Register two users, match them, and chat. Returns context or None."""
        section("User journey")
        try:
            _, a_token = self.register("a", "woman")
            _, b_token = self.register("b", "man")
        except PermissionError:
            print(
                f"  {YELLOW}SKIP{RESET}  email verification is enabled, so this "
                "script cannot complete signup.\n"
                f"        {DIM}That is the safe setting. To verify the full "
                f"journey, either click the link in the\n        email or set "
                f"REQUIRE_EMAIL_VERIFICATION=false temporarily.{RESET}"
            )
            return None
        except Exception as exc:  # noqa: BLE001
            check("register two users", False, str(exc)[:160])
            return None

        check("register two users", True)

        me = self.client.get(f"{self.api}/auth/me", headers=self.h(a_token))
        check("authenticated /auth/me", me.status_code == 200)
        a_id = me.json()["id"]
        b_id = self.client.get(f"{self.api}/auth/me", headers=self.h(b_token)).json()["id"]

        # --- profile + photo upload (the object-storage path) -------------
        for token in (a_token, b_token):
            self.client.patch(
                f"{self.api}/profiles/me",
                headers=self.h(token),
                json={"bio": "Deployment verification account."},
            )
            self.client.put(
                f"{self.api}/profiles/me/location",
                headers=self.h(token),
                json={
                    "latitude": 12.9716,
                    "longitude": 77.5946,
                    "city": "Bengaluru",
                    "country": "IN",
                },
            )

        photo_ok = self.check_photo_upload(a_token)
        self.check_photo_upload(b_token, quiet=True)

        # --- discovery + matching ------------------------------------------
        feed = self.client.get(f"{self.api}/discovery", headers=self.h(a_token))
        if feed.status_code == 403:
            print(
                f"  {YELLOW}note{RESET}  discovery gated: profiles need an "
                "approved photo. Approve them in the admin queue."
            )
        check(
            "discovery feed reachable",
            feed.status_code in (200, 403),
            f"HTTP {feed.status_code}",
        )
        if feed.status_code == 200:
            raw = feed.text
            check(
                "feed leaks no coordinates",
                "latitude" not in raw and "longitude" not in raw,
            )

        self.client.post(
            f"{self.api}/discovery/swipe",
            headers=self.h(a_token),
            json={"target_user_id": b_id, "action": "like"},
        )
        swipe = self.client.post(
            f"{self.api}/discovery/swipe",
            headers=self.h(b_token),
            json={"target_user_id": a_id, "action": "like"},
        )
        matched = swipe.status_code == 200 and swipe.json().get("is_match") is True
        check("mutual like creates a match", matched, f"HTTP {swipe.status_code}")
        if not matched:
            return None

        conversation_id = swipe.json()["match"]["conversation_id"]

        # --- messaging over REST -------------------------------------------
        sent = self.client.post(
            f"{self.api}/conversations/{conversation_id}/messages",
            headers=self.h(a_token),
            json={"body": "Verification message", "client_nonce": "verify-1"},
        )
        check("send message (REST)", sent.status_code == 201)

        history = self.client.get(
            f"{self.api}/conversations/{conversation_id}/messages",
            headers=self.h(b_token),
        )
        check(
            "recipient reads history",
            history.status_code == 200
            and any(
                m["body"] == "Verification message"
                for m in history.json().get("messages", [])
            ),
        )

        # --- authorization boundary ----------------------------------------
        # The outsider is given a photo so their profile is complete. Without
        # that they are stopped by the onboarding gate and the request never
        # reaches the chat authorization — which would make this test pass for
        # the wrong reason.
        try:
            _, c_token = self.register("c", "nonbinary")
            self.check_photo_upload(c_token, quiet=True)
            outsider = self.client.get(
                f"{self.api}/conversations/{conversation_id}/messages",
                headers=self.h(c_token),
            )
            denied = outsider.status_code in (403, 404)
            leaked = "Verification message" in outsider.text
            check(
                "outsider cannot read the chat",
                denied and not leaked,
                f"HTTP {outsider.status_code}"
                + (" — MESSAGE CONTENT LEAKED" if leaked else ""),
            )
        except RateLimited:
            print(
                f"  {DIM}note  skipped the outsider check: registration rate "
                f"limit{RESET}"
            )
        except Exception as exc:  # noqa: BLE001
            check("outsider cannot read the chat", False, str(exc)[:140])

        return {
            "a_token": a_token,
            "b_token": b_token,
            "conversation_id": conversation_id,
            "photo_ok": photo_ok,
        }

    def check_photo_upload(self, token: str, quiet: bool = False) -> bool:
        """Exercise the full presigned-upload round trip against real storage."""
        # A 1x1 PNG: the smallest payload that is genuinely an image.
        png = bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
            "890000000a49444154789c6360000002000100ffff03000006000557bfabd400"
            "00000049454e44ae426082"
        )
        ticket = self.client.post(
            f"{self.api}/profiles/me/photos",
            headers=self.h(token),
            json={"content_type": "image/png", "byte_size": len(png)},
        )
        if ticket.status_code != 201:
            if not quiet:
                check("request presigned upload", False, f"HTTP {ticket.status_code}")
            return False
        if not quiet:
            check("request presigned upload", True)

        body = ticket.json()
        try:
            # Straight to object storage, not through the API.
            upload = httpx.put(
                body["upload_url"], content=png, headers=body["headers"], timeout=60
            )
        except Exception as exc:  # noqa: BLE001
            if not quiet:
                check("PUT to object storage", False, str(exc)[:140])
            return False

        ok = upload.status_code in (200, 201, 204)
        if not quiet:
            check("PUT to object storage", ok, f"HTTP {upload.status_code}")
        if not ok:
            return False

        confirmed = self.client.post(
            f"{self.api}/profiles/me/photos/{body['photo_id']}/confirm",
            headers=self.h(token),
        )
        if not quiet:
            check("confirm upload (storage read-back)", confirmed.status_code == 200)
        return confirmed.status_code == 200

    def check_websocket(self, token: str, conversation_id: str) -> None:
        section("Real-time chat (WSS)")
        if ws_connect is None:
            print(
                f"  {YELLOW}SKIP{RESET}  install websockets to test WSS:  "
                "pip install websockets"
            )
            return

        ws_url = (
            self.base.replace("https://", "wss://").replace("http://", "ws://")
            + f"/ws/chat?token={token}"
        )
        if self.base.startswith("https://"):
            check("WebSocket URL is wss://", ws_url.startswith("wss://"))
        else:
            print(f"  {DIM}note  local target — ws:// is expected{RESET}")

        try:
            with ws_connect(ws_url, open_timeout=60, close_timeout=5) as socket:
                ready = json.loads(socket.recv(timeout=30))
                check("socket accepted and ready", ready.get("type") == "ready")

                nonce = secrets.token_hex(6)
                socket.send(
                    json.dumps(
                        {
                            "type": "message.send",
                            "conversation_id": conversation_id,
                            "body": "Sent over the socket",
                            "client_nonce": nonce,
                        }
                    )
                )

                # The server echoes the message back to the sender.
                deadline = time.monotonic() + 30
                echoed = False
                while time.monotonic() < deadline:
                    frame = json.loads(socket.recv(timeout=30))
                    if frame.get("type") == "message.created":
                        echoed = frame["message"].get("client_nonce") == nonce
                        break
                    if frame.get("type") == "error":
                        check("send over socket", False, frame.get("detail", "")[:120])
                        return
                check("message delivered over the socket", echoed)
        except Exception as exc:  # noqa: BLE001
            check("WebSocket connection", False, str(exc)[:160])


def main() -> int:
    # Windows consoles default to cp1252 and mangle non-ASCII output.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url", help="e.g. https://amora-api.onrender.com")
    parser.add_argument(
        "--origin",
        default="https://prakhar924.github.io",
        help="Frontend origin to test CORS against",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=90.0,
        help="Per-request timeout. Generous by default: a sleeping free-tier "
        "host can take ~60s to wake.",
    )
    args = parser.parse_args()

    print(f"Verifying {args.base_url}")
    verifier = Verifier(args.base_url, args.timeout)

    try:
        if not verifier.check_reachable():
            print(f"\n{RED}Backend unreachable — stopping.{RESET}")
            return 1

        verifier.check_dependencies()
        verifier.check_security_headers()
        verifier.check_cors(args.origin)
        verifier.check_age_gate()
        verifier.check_unauthenticated()

        context = verifier.run_journey()
        if context:
            verifier.check_websocket(context["a_token"], context["conversation_id"])
    finally:
        verifier.cleanup()

    passed = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - passed
    print(f"\n{'-' * 58}")
    print(f"{passed} passed, {failed} failed")
    if failed:
        print(f"\n{RED}Failures:{RESET}")
        for name, ok, detail in results:
            if not ok:
                print(f"  - {name}" + (f": {detail}" if detail else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
