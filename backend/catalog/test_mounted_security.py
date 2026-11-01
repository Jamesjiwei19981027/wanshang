from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import SESSION_KEY, get_user_model
from django.core.cache import cache
from django.test import Client, RequestFactory, TestCase, override_settings

from .middleware import AdminLoginRateLimitMiddleware


PUBLIC_FORM_PATHS = (
    "/api/inquiries/",
    "/api/v1/inquiries/",
    "/api/contact/",
    "/api/contact-messages/",
    "/api/v1/contact/",
    "/api/v1/contact-messages/",
)


@override_settings(
    ADMIN_LOGIN_MAX_FAILURES=2,
    ADMIN_LOGIN_FAILURE_WINDOW=60,
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "mounted-security-tests",
        }
    },
    CSRF_TRUSTED_ORIGINS=["https://frontend.example.com"],
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    SECURE_SSL_REDIRECT=False,
    TURNSTILE_REQUIRED=True,
    TURNSTILE_SECRET_KEY="isolated-mounted-security-tests",
    REST_FRAMEWORK={**settings.REST_FRAMEWORK, "NUM_PROXIES": 0},
)
class MountedSecurityTests(TestCase):
    remote_addr = "198.51.100.91"
    password = "isolated-mounted-security-test-password"

    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser(
            username="isolated-mounted-admin",
            email="mounted-admin@example.com",
            password=cls.password,
        )

    def setUp(self):
        cache.clear()
        self.verify = self.start_patch("catalog.serializers.verify_turnstile_token")
        self.rfq_queue = self.start_patch("catalog.views.enqueue_inquiry_notification")
        self.contact_queue = self.start_patch("catalog.views.enqueue_contact_notification")
        self.start_patch(
            "catalog.tasks.send_mail", side_effect=AssertionError("No mail in tests")
        )
        self.start_patch(
            "socket.create_connection",
            side_effect=AssertionError("No external network in tests"),
        )
        self.start_patch(
            "socket.socket.connect",
            side_effect=AssertionError("No external network in tests"),
        )

    def start_patch(self, target, **kwargs):
        patcher = patch(target, **kwargs)
        mocked = patcher.start()
        self.addCleanup(patcher.stop)
        return mocked

    def csrf_client(self, script_name):
        client = Client(enforce_csrf_checks=True)
        response = client.get("/admin/login/", SCRIPT_NAME=script_name)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.wsgi_request.path, f"{script_name}/admin/login/")
        self.assertEqual(response.wsgi_request.path_info, "/admin/login/")
        self.assertIn(settings.CSRF_COOKIE_NAME, client.cookies)
        return client

    def login_attempt(self, client, script_name, *, password="incorrect-password"):
        return client.post(
            "/admin/login/",
            {"username": self.admin.username, "password": password},
            HTTP_X_CSRFTOKEN=client.cookies[settings.CSRF_COOKIE_NAME].value,
            REMOTE_ADDR=self.remote_addr,
            SCRIPT_NAME=script_name,
        )

    def cache_keys(self):
        request = RequestFactory().post(
            "/admin/login/",
            {"username": self.admin.username},
            REMOTE_ADDR=self.remote_addr,
        )
        return (
            AdminLoginRateLimitMiddleware._cache_key(request),
            AdminLoginRateLimitMiddleware._username_cache_key(request),
        )

    def assert_throttled(self, response):
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Retry-After"], "60")
        self.assertContains(
            response, "Too many unsuccessful login attempts.", status_code=429
        )

    def test_failed_admin_logins_are_limited_at_root_and_under_script_name(self):
        for script_name in ("", "/mounted"):
            with self.subTest(script_name=script_name):
                cache.clear()
                client = self.csrf_client(script_name)
                for _ in range(2):
                    self.assertEqual(
                        self.login_attempt(client, script_name).status_code, 200
                    )
                self.assert_throttled(self.login_attempt(client, script_name))
                for key in self.cache_keys():
                    self.assertEqual(cache.get(key), 2)
                self.assertNotIn(SESSION_KEY, client.session)

    def test_root_and_mounted_admin_logins_share_failure_buckets(self):
        for first_mount, second_mount in (("", "/mounted"), ("/mounted", "")):
            with self.subTest(first_mount=first_mount):
                cache.clear()
                first_client = self.csrf_client(first_mount)
                second_client = self.csrf_client(second_mount)
                self.assertEqual(
                    self.login_attempt(first_client, first_mount).status_code, 200
                )
                self.assertEqual(
                    self.login_attempt(second_client, second_mount).status_code, 200
                )
                self.assert_throttled(self.login_attempt(second_client, second_mount))
                for key in self.cache_keys():
                    self.assertEqual(cache.get(key), 2)

    def test_successful_admin_login_clears_buckets_at_root_and_under_script_name(self):
        for script_name in ("", "/mounted"):
            with self.subTest(script_name=script_name):
                cache.clear()
                client = self.csrf_client(script_name)
                self.assertEqual(self.login_attempt(client, script_name).status_code, 200)
                for key in self.cache_keys():
                    self.assertEqual(cache.get(key), 1)

                response = self.login_attempt(client, script_name, password=self.password)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(client.session[SESSION_KEY], str(self.admin.pk))
                for key in self.cache_keys():
                    self.assertIsNone(cache.get(key))

                # Successful authentication rotates CSRF. The helper reads the
                # current cookie so this next failure also has a valid token.
                self.assertEqual(self.login_attempt(client, script_name).status_code, 200)
                for key in self.cache_keys():
                    self.assertEqual(cache.get(key), 1)

    def test_public_form_cross_site_posts_are_rejected_under_each_mount(self):
        client = Client(enforce_csrf_checks=True)
        for script_name in ("", "/mounted"):
            for path in PUBLIC_FORM_PATHS:
                with self.subTest(script_name=script_name, path=path):
                    response = client.post(
                        path,
                        {},
                        content_type="application/json",
                        HTTP_ORIGIN="https://attacker.example.com",
                        HTTP_USER_AGENT="Mozilla/5.0",
                        SCRIPT_NAME=script_name,
                    )
                    self.assertEqual(response.wsgi_request.path, f"{script_name}{path}")
                    self.assertEqual(response.wsgi_request.path_info, path)
                    self.assertContains(
                        response,
                        "Cross-site form submissions are not allowed.",
                        status_code=403,
                    )
        self.verify.assert_not_called()
        self.rfq_queue.assert_not_called()
        self.contact_queue.assert_not_called()

    def test_public_form_trusted_and_same_site_posts_reach_validation_under_each_mount(self):
        client = Client(enforce_csrf_checks=True)
        for script_name in ("", "/mounted"):
            for path in PUBLIC_FORM_PATHS:
                for origin in ("https://frontend.example.com", "http://testserver"):
                    with self.subTest(script_name=script_name, path=path, origin=origin):
                        cache.clear()
                        response = client.post(
                            path,
                            {},
                            content_type="application/json",
                            HTTP_ORIGIN=origin,
                            HTTP_USER_AGENT="Mozilla/5.0",
                            SCRIPT_NAME=script_name,
                        )
                        self.assertEqual(response.status_code, 400, response.content[:500])
                        self.assertIn("name", response.json().get("fields", response.json()))
        self.verify.assert_not_called()
        self.rfq_queue.assert_not_called()
        self.contact_queue.assert_not_called()
