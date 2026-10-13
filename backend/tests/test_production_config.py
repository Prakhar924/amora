"""Production configuration safety.

These tests encode the deployment mistakes that would be most damaging and
hardest to notice: shipping with a default signing key, a plain-HTTP origin,
a wildcard host allow-list, or an email fallback that swallows password-reset
links. Each one produces a working-looking deployment that is quietly unsafe,
so the guard is tested rather than trusted.
"""

from __future__ import annotations

import pytest

from app.core.config import DEV_SECRET, Settings

# A configuration that should pass every production check. Individual tests
# break exactly one field, so a failure names the rule that fired.
SAFE = {
    "app_env": "production",
    "debug": False,
    "secret_key": "x" * 48,
    "postgres_password": "a-real-generated-password-32-chars",
    "s3_access_key": "real-access-key",
    "s3_secret_key": "real-secret-key",
    "s3_public_endpoint_url": "https://abc.r2.cloudflarestorage.com",
    "cors_origins": "https://prakhar924.github.io",
    "allowed_hosts": "api.amora.app",
    "frontend_base_url": "https://prakhar924.github.io/amora",
    "email_console_fallback": False,
    "smtp_host": "smtp.resend.com",
    "bcrypt_rounds": 12,
}


def settings(**overrides) -> Settings:
    # `_env_file=None` keeps a developer's local .env from leaking in and
    # making these tests pass or fail for the wrong reason.
    return Settings(_env_file=None, **{**SAFE, **overrides})


def problems_for(**overrides) -> str:
    with pytest.raises(RuntimeError) as caught:
        settings(**overrides).assert_production_ready()
    return str(caught.value)


class TestGuardAcceptsASafeConfiguration:
    def test_a_fully_configured_production_setup_boots(self):
        # If this fails, the guard has become stricter than the documented
        # deployment process, and nobody will be able to deploy.
        settings().assert_production_ready()

    def test_development_is_never_blocked(self):
        # The guard must be inert outside production, or local work stops.
        Settings(_env_file=None, app_env="development").assert_production_ready()


class TestSecrets:
    def test_default_signing_key_is_rejected(self):
        assert "SECRET_KEY" in problems_for(secret_key=DEV_SECRET)

    def test_short_signing_key_is_rejected(self):
        assert "SECRET_KEY" in problems_for(secret_key="too-short")

    @pytest.mark.parametrize("password", ["amora", "postgres", "password", ""])
    def test_default_database_passwords_are_rejected(self, password: str):
        assert "POSTGRES_PASSWORD" in problems_for(postgres_password=password)

    @pytest.mark.parametrize("key", ["amoraminio", "amoralocal", "test", ""])
    def test_development_storage_credentials_are_rejected(self, key: str):
        assert "S3_SECRET_KEY" in problems_for(s3_secret_key=key)

    def test_debug_mode_is_rejected(self):
        assert "DEBUG" in problems_for(debug=True)

    def test_weak_password_hashing_is_rejected(self):
        assert "BCRYPT_ROUNDS" in problems_for(bcrypt_rounds=4)


class TestTransportSecurity:
    def test_plain_http_origin_is_rejected(self):
        """Tokens must never cross an unencrypted origin."""
        assert "https" in problems_for(cors_origins="http://amora.app")

    def test_localhost_origin_is_rejected(self):
        """A leftover localhost origin means the frontend was never configured."""
        assert "localhost" in problems_for(
            cors_origins="http://localhost:8081"
        )

    def test_empty_origin_list_is_rejected(self):
        assert "CORS_ORIGINS" in problems_for(cors_origins="")

    def test_plain_http_storage_endpoint_is_rejected(self):
        assert "S3_PUBLIC_ENDPOINT_URL" in problems_for(
            s3_public_endpoint_url="http://storage.amora.app"
        )

    def test_plain_http_frontend_url_is_rejected(self):
        assert "FRONTEND_BASE_URL" in problems_for(
            frontend_base_url="http://amora.app"
        )


class TestHostHeader:
    def test_wildcard_host_allowlist_is_rejected(self):
        """A wildcard permits Host-header poisoning of emailed reset links."""
        assert "ALLOWED_HOSTS" in problems_for(allowed_hosts="*")

    def test_a_named_host_is_accepted(self):
        settings(allowed_hosts="api.amora.app").assert_production_ready()

    def test_multiple_hosts_are_parsed(self):
        parsed = settings(allowed_hosts="api.amora.app, www.amora.app")
        assert parsed.allowed_host_list == ["api.amora.app", "www.amora.app"]


class TestEmailDelivery:
    def test_console_fallback_is_rejected(self):
        """Otherwise a failed reset email is logged and the user waits forever."""
        assert "EMAIL_CONSOLE_FALLBACK" in problems_for(email_console_fallback=True)

    @pytest.mark.parametrize("host", ["localhost", "mailpit", "mailhog", ""])
    def test_development_mail_hosts_are_rejected(self, host: str):
        assert "SMTP_HOST" in problems_for(smtp_host=host)


class TestGuardReporting:
    def test_every_problem_is_reported_at_once(self):
        """A deployer should see the full list, not fix one issue per attempt."""
        message = problems_for(
            secret_key=DEV_SECRET,
            postgres_password="amora",
            allowed_hosts="*",
            email_console_fallback=True,
        )
        for expected in (
            "SECRET_KEY",
            "POSTGRES_PASSWORD",
            "ALLOWED_HOSTS",
            "EMAIL_CONSOLE_FALLBACK",
        ):
            assert expected in message

    def test_the_message_explains_how_to_fix_the_key(self):
        assert "secrets.token_urlsafe" in problems_for(secret_key=DEV_SECRET)


class TestSeedRefusesInProduction:
    async def test_seeding_production_exits_rather_than_writing(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Synthetic profiles must never reach a real user database."""
        from app import seed

        monkeypatch.setattr(seed.settings, "app_env", "production")

        with pytest.raises(SystemExit) as caught:
            await seed.main()
        assert caught.value.code == 1
