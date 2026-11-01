"""Production startup must reject unsafe CAPTCHA and mail configurations."""

import os
from pathlib import Path
import subprocess
import sys

from django.test import SimpleTestCase


class ProductionCaptchaSettingsTests(SimpleTestCase):
    def import_settings(self, secret, *, environment="production", **overrides):
        env = {
            **os.environ,
            "DJANGO_ENV": environment,
            "DJANGO_DEBUG": "False",
            "DJANGO_BEHIND_PROXY": "False",
            "DJANGO_NUM_PROXIES": "0",
            "DJANGO_GEOIP_COUNTRY_HEADER": "",
            "DJANGO_GEOIP_CITY_HEADER": "",
            "DJANGO_SECRET_KEY": "IsolatedProductionConfigTest_3mB8qR2pT9vX1hG6sY5jL4eU7kD0nWz",
            "DJANGO_ALLOWED_HOSTS": "api.example.com",
            "DJANGO_CORS_ORIGINS": "https://www.example.com",
            "DJANGO_SECURE_SSL_REDIRECT": "True",
            "DJANGO_PUBLIC_MEDIA_URL": "https://cdn.example.com/media/",
            "DJANGO_PUBLIC_BASE_URL": "https://api.example.com",
            "TURNSTILE_SECRET_KEY": secret,
            "DEFAULT_FROM_EMAIL": "website@example.com",
            "SALES_NOTIFICATION_EMAIL": "sales@example.com",
            "EMAIL_BACKEND": "django.core.mail.backends.smtp.EmailBackend",
            "EMAIL_HOST": "smtp.example.com",
            "EMAIL_PORT": "587",
            "EMAIL_USE_TLS": "True",
            "EMAIL_USE_SSL": "False",
            "EMAIL_TIMEOUT": "15",
            "DATABASE_URL": "sqlite:///:memory:",
        }
        env.update(overrides)
        return subprocess.run(
            [sys.executable, "-c", "import config.settings"],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def test_all_public_cloudflare_test_keys_fail_production_startup(self):
        for key in (
            "1x0000000000000000000000000000000AA",
            "2x0000000000000000000000000000000AA",
            "3x0000000000000000000000000000000AA",
        ):
            with self.subTest(key=key):
                result = self.import_settings(key)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(
                    "must not use a public testing key in production",
                    result.stdout + result.stderr,
                )

    def test_real_key_configuration_and_development_test_keys_remain_supported(self):
        for key, environment in (
            ("isolated-non-public-key", "production"),
            ("1x0000000000000000000000000000000AA", "development"),
        ):
            with self.subTest(environment=environment):
                result = self.import_settings(key, environment=environment)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_production_rejects_plaintext_smtp(self):
        result = self.import_settings(
            "isolated-non-public-key", EMAIL_USE_TLS="False", EMAIL_USE_SSL="False",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "Enable EMAIL_USE_TLS or EMAIL_USE_SSL for encrypted SMTP delivery",
            result.stdout + result.stderr,
        )

    def test_encrypted_smtp_and_development_plaintext_remain_supported(self):
        for environment, tls, ssl in (
            ("production", "True", "False"),
            ("production", "False", "True"),
            ("development", "False", "False"),
        ):
            with self.subTest(environment=environment, tls=tls, ssl=ssl):
                result = self.import_settings(
                    "isolated-non-public-key", environment=environment,
                    EMAIL_USE_TLS=tls, EMAIL_USE_SSL=ssl,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
