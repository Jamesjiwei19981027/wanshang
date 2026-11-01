"""Configuration security contracts; no live mail, database or account access."""
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("production_configure", Path(__file__).with_name("configure.py"))
configure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(configure)


class ProductionConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.path = self.root / ".env.production"
        configure.init_config(self.path, "axle.wan-shang.com")
        self.values = configure.read_env(self.path)
        self.values.update({
            "NEXT_PUBLIC_TURNSTILE_SITE_KEY": "deployment-public-key",
            "TURNSTILE_SECRET_KEY": "deployment-private-key",
            "EMAIL_HOST_USER": "sender@example.com",
            "EMAIL_HOST_PASSWORD": "unit-test-app-password",
            "DEFAULT_FROM_EMAIL": "sender@example.com",
            "SALES_NOTIFICATION_EMAIL": "sales@example.com",
        })

    def tearDown(self):
        self.temporary.cleanup()

    def test_prepared_configuration_is_valid_offline(self):
        self.assertEqual(configure.validate_config(self.values, check_tls=False), [])

    def test_generated_credentials_are_independent_and_private(self):
        names = ("DJANGO_SECRET_KEY", "POSTGRES_PASSWORD", "APP_DB_PASSWORD", "REDIS_PASSWORD")
        self.assertEqual(len({self.values[name] for name in names}), 4)
        self.assertGreaterEqual(len(self.values["DJANGO_SECRET_KEY"]), 50)
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_initialization_never_overwrites_existing_configuration(self):
        original = self.path.read_bytes()
        with self.assertRaises(ValueError):
            configure.init_config(self.path, "axle.wan-shang.com")
        self.assertEqual(self.path.read_bytes(), original)

    def test_invalid_hostnames_are_rejected_before_file_creation(self):
        for value in ("https://axle.wan-shang.com", "*.wan-shang.com", "127.0.0.1", "127.1", "0x7f000001", "example.com:443", "localhost", "example.internal", "example.com/path"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                configure.init_config(self.root / "never-created.env", value)
        self.assertFalse((self.root / "never-created.env").exists())

    def test_import_is_allowlisted_and_does_not_reuse_development_secrets(self):
        source = self.root / "source"
        (source / "frontend").mkdir(parents=True)
        (source / ".env").write_text("DJANGO_SECRET_KEY=development-secret\nPOSTGRES_PASSWORD=development-db\nEMAIL_HOST=smtp.example.com\nEMAIL_HOST_PASSWORD='has $ and apostrophe\\'s'\n", encoding="utf-8")
        (source / ".env.production").write_text("TURNSTILE_SECRET_KEY=private-widget-key\n", encoding="utf-8")
        (source / "frontend/.env.production.local").write_text("NEXT_PUBLIC_TURNSTILE_SITE_KEY=public-widget-key\n", encoding="utf-8")
        destination = self.root / "imported.env"
        with patch.object(configure, "ROOT", source):
            result = configure.init_config(destination, "axle.wan-shang.com", import_existing=True)
        values = configure.read_env(destination)
        self.assertEqual(values["EMAIL_HOST_PASSWORD"], "has $ and apostrophe's")
        self.assertEqual(values["TURNSTILE_SECRET_KEY"], "private-widget-key")
        self.assertNotEqual(values["DJANGO_SECRET_KEY"], "development-secret")
        self.assertNotEqual(values["POSTGRES_PASSWORD"], "development-db")
        self.assertFalse(result["secret_values_printed"])

    def test_duplicate_entries_are_rejected(self):
        self.path.write_text("EMAIL_HOST=a\nEMAIL_HOST=b\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            configure.read_env(self.path)

    def test_smtp_requires_one_encrypted_transport(self):
        for ssl, tls in (("True", "True"), ("False", "False"), ("yes", "False")):
            values = self.values | {"EMAIL_USE_SSL": ssl, "EMAIL_USE_TLS": tls}
            self.assertTrue(any("SMTP requires" in error for error in configure.validate_config(values, False)))

    def test_superuser_role_and_reused_credentials_are_rejected(self):
        values = self.values | {"APP_DB_USER": self.values["POSTGRES_USER"], "APP_DB_PASSWORD": self.values["POSTGRES_PASSWORD"]}
        errors = configure.validate_config(values, False)
        self.assertTrue(any("roles must differ" in error for error in errors))
        self.assertTrue(any("credentials must be distinct" in error for error in errors))
        for role in ("POSTGRES_USER", "APP_DB_USER"):
            self.assertTrue(any("reserved pg_" in error for error in configure.validate_config(self.values | {role: "pg_reserved"}, False)))

    def test_examples_short_secrets_public_widget_keys_and_unpinned_images_fail(self):
        cases = (("DJANGO_SECRET_KEY", "replace-this-in-production"), ("REDIS_PASSWORD", "short"), ("TURNSTILE_SECRET_KEY", "1x0000000000000000000000000000000AA"), ("CLAMAV_IMAGE", "clamav/clamav:latest"), ("NGINX_BASE_IMAGE", "nginx:stable"), ("BACKEND_IMAGE", "axle-api"), ("FRONTEND_IMAGE", "registry.example.com:5000/axle-web"), ("NGINX_IMAGE", "axle-nginx:latest"))
        for name, value in cases:
            with self.subTest(name=name):
                self.assertTrue(configure.validate_config(self.values | {name: value}, False))

    def test_invalid_numeric_bounds_fail(self):
        for name, value in (("EMAIL_PORT", "0"), ("HTTPS_PORT", "70000"), ("EMAIL_TIMEOUT", "nan"), ("API_WORKERS", "0"), ("CELERY_CONCURRENCY", "1000")):
            with self.subTest(name=name):
                self.assertTrue(configure.validate_config(self.values | {name: value}, False))

    def test_missing_certificates_prevent_deployment(self):
        errors = configure.validate_config(self.values | {"TLS_CERT_DIR": str(self.root / "missing-tls")}, True)
        self.assertTrue(any("fullchain.pem" in error for error in errors))

    def test_certificate_hostname_matching_is_single_label_only(self):
        self.assertTrue(configure.certificate_covers("axle.wan-shang.com", ["*.wan-shang.com"]))
        self.assertFalse(configure.certificate_covers("deep.axle.wan-shang.com", ["*.wan-shang.com"]))
        self.assertFalse(configure.certificate_covers("wan-shang.com", ["*.wan-shang.com"]))

    def test_proxy_refresh_is_atomic_and_rejects_broad_or_private_ranges(self):
        target = self.root / "nginx/cloudflare-realip.conf"
        target.parent.mkdir()
        target.write_text("existing verified config\n", encoding="utf-8")
        bad = {"success": True, "result": {"ipv4_cidrs": ["0.0.0.0/0", "10.0.0.0/8"], "ipv6_cidrs": ["2606:4700::/32", "2001:4860::/32"]}}
        with patch.object(configure, "HERE", self.root), patch.object(configure, "urlopen", return_value=io.BytesIO(json.dumps(bad).encode())):
            with self.assertRaises(ValueError):
                configure.refresh_proxies()
        self.assertEqual(target.read_text(), "existing verified config\n")


if __name__ == "__main__":
    unittest.main()
