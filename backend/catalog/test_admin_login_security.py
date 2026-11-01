from types import SimpleNamespace
from unittest.mock import PropertyMock, patch

from django.conf import settings
from django.contrib.auth import SESSION_KEY, get_user_model
from django.core.cache import cache
from django.http import HttpResponseRedirect
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings

from .middleware import AdminLoginRateLimitMiddleware


TEST_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "admin-login-security-tests",
    }
}


@override_settings(
    ADMIN_LOGIN_MAX_FAILURES=2,
    ADMIN_LOGIN_FAILURE_WINDOW=60,
    CACHES=TEST_CACHE,
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    SECURE_SSL_REDIRECT=False,
    REST_FRAMEWORK={**settings.REST_FRAMEWORK, "NUM_PROXIES": 0},
)
class AdminLoginSecurityTests(TestCase):
    remote_addr = "198.51.100.61"
    password = "isolated-admin-security-test-password"

    @classmethod
    def setUpTestData(cls):
        cls.first_admin = get_user_model().objects.create_superuser(
            username="isolated-admin-one",
            email="admin-one@example.com",
            password=cls.password,
        )
        cls.second_admin = get_user_model().objects.create_superuser(
            username="isolated-admin-two",
            email="admin-two@example.com",
            password=cls.password,
        )

    def setUp(self):
        cache.clear()
        self.client = Client()
        request = RequestFactory().post("/admin/login/", REMOTE_ADDR=self.remote_addr)
        self.cache_key = AdminLoginRateLimitMiddleware._cache_key(request)

    def username_cache_key(self, username, remote_addr=None):
        request = RequestFactory().post(
            "/admin/login/",
            {"username": username},
            REMOTE_ADDR=remote_addr or self.remote_addr,
        )
        return AdminLoginRateLimitMiddleware._username_cache_key(request)

    def login_attempt(
        self,
        user=None,
        password="incorrect-password",
        username=None,
        remote_addr=None,
        **headers,
    ):
        return self.client.post(
            "/admin/login/",
            {
                "username": (
                    username
                    if username is not None
                    else (user or self.second_admin).username
                ),
                "password": password,
            },
            REMOTE_ADDR=remote_addr or self.remote_addr,
            **headers,
        )

    def assert_blocked(self, response):
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Retry-After"], "60")
        self.assertContains(
            response, "Too many unsuccessful login attempts.", status_code=429
        )

    def test_anonymous_failures_reach_limit_and_block_even_valid_credentials(self):
        self.assertEqual(self.login_attempt().status_code, 200)
        self.assertEqual(self.login_attempt().status_code, 200)
        self.assertEqual(cache.get(self.cache_key), 2)

        self.assert_blocked(self.login_attempt(password=self.password))
        self.assertEqual(cache.get(self.cache_key), 2)
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_existing_staff_session_cannot_bypass_failures_for_another_account(self):
        self.client.force_login(self.first_admin)
        for forwarded_for in ("203.0.113.1", "203.0.113.2"):
            response = self.login_attempt(HTTP_X_FORWARDED_FOR=forwarded_for)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                self.client.session[SESSION_KEY], str(self.first_admin.pk)
            )

        self.assert_blocked(self.login_attempt(HTTP_X_FORWARDED_FOR="203.0.113.3"))
        self.assertEqual(cache.get(self.cache_key), 2)

    def test_browser_login_then_repeated_wrong_passwords_are_limited_with_valid_csrf(self):
        self.client = Client(enforce_csrf_checks=True)
        self.assertEqual(self.client.get("/admin/login/").status_code, 200)
        csrf_token = self.client.cookies[settings.CSRF_COOKIE_NAME].value
        succeeded = self.login_attempt(
            user=self.first_admin,
            password=self.password,
            HTTP_X_CSRFTOKEN=csrf_token,
        )
        self.assertEqual(succeeded.status_code, 302)

        # A real login rotates CSRF; use the new token with the existing session.
        csrf_token = self.client.cookies[settings.CSRF_COOKIE_NAME].value
        for _ in range(2):
            self.assertEqual(
                self.login_attempt(HTTP_X_CSRFTOKEN=csrf_token).status_code, 200
            )
        self.assert_blocked(self.login_attempt(HTTP_X_CSRFTOKEN=csrf_token))
        self.assertEqual(self.client.session[SESSION_KEY], str(self.first_admin.pk))

    def test_real_anonymous_login_clears_previous_failures(self):
        self.assertEqual(self.login_attempt().status_code, 200)
        self.assertEqual(cache.get(self.cache_key), 1)

        succeeded = self.login_attempt(password=self.password)
        self.assertEqual(succeeded.status_code, 302)
        self.assertEqual(
            self.client.session[SESSION_KEY], str(self.second_admin.pk)
        )
        self.assertIsNone(cache.get(self.cache_key))

        self.client.logout()
        self.assertEqual(self.login_attempt().status_code, 200)
        self.assertEqual(cache.get(self.cache_key), 1)

    def test_real_account_switch_clears_failures_only_for_that_request(self):
        self.client.force_login(self.first_admin)
        self.assertEqual(self.login_attempt().status_code, 200)
        self.assertEqual(cache.get(self.cache_key), 1)

        succeeded = self.login_attempt(password=self.password)
        self.assertEqual(succeeded.status_code, 302)
        self.assertEqual(
            self.client.session[SESSION_KEY], str(self.second_admin.pk)
        )
        self.assertIsNone(cache.get(self.cache_key))

        # The successful request's marker must not persist on the next request.
        self.assertEqual(self.login_attempt(user=self.first_admin).status_code, 200)
        self.assertEqual(cache.get(self.cache_key), 1)

    def test_successful_reauthentication_of_same_staff_account_clears_failures(self):
        self.client.force_login(self.first_admin)
        self.assertEqual(self.login_attempt().status_code, 200)
        self.assertEqual(cache.get(self.cache_key), 1)

        succeeded = self.login_attempt(user=self.first_admin, password=self.password)
        self.assertEqual(succeeded.status_code, 302)
        self.assertIsNone(cache.get(self.cache_key))

    def test_own_success_between_target_guesses_cannot_reset_target_limit(self):
        self.client.force_login(self.first_admin)
        target_key = self.username_cache_key(self.second_admin.username)

        for expected_failures in (1, 2):
            self.assertEqual(self.login_attempt().status_code, 200)
            self.assertEqual(cache.get(target_key), expected_failures)
            self.assertEqual(
                self.login_attempt(user=self.first_admin, password=self.password).status_code,
                302,
            )
            self.assertIsNone(cache.get(self.cache_key))
            self.assertEqual(cache.get(target_key), expected_failures)

        self.assert_blocked(self.login_attempt())
        self.assertIsNone(cache.get(self.cache_key))
        self.assertEqual(cache.get(target_key), 2)
        self.assertEqual(self.client.session[SESSION_KEY], str(self.first_admin.pk))

    def test_target_limit_does_not_lock_the_same_account_at_another_ip(self):
        for _ in range(2):
            self.assertEqual(self.login_attempt().status_code, 200)
        target_key = self.username_cache_key(self.second_admin.username)
        self.assert_blocked(self.login_attempt())

        self.assertEqual(
            self.login_attempt(password=self.password, remote_addr="198.51.100.63").status_code,
            302,
        )
        self.assertEqual(self.client.session[SESSION_KEY], str(self.second_admin.pk))
        self.assertEqual(cache.get(target_key), 2)
        self.assertEqual(cache.get(self.cache_key), 2)

    @override_settings(ADMIN_LOGIN_MAX_FAILURES=3)
    def test_success_clears_own_username_and_ip_buckets_but_keeps_other_target(self):
        own_key = self.username_cache_key(self.first_admin.username)
        other_key = self.username_cache_key(self.second_admin.username)
        self.assertEqual(self.login_attempt(user=self.first_admin).status_code, 200)
        self.assertEqual(self.login_attempt().status_code, 200)
        self.assertEqual(cache.get(self.cache_key), 2)
        self.assertEqual(cache.get(own_key), 1)
        self.assertEqual(cache.get(other_key), 1)

        self.assertEqual(
            self.login_attempt(user=self.first_admin, password=self.password).status_code,
            302,
        )
        self.assertIsNone(cache.get(self.cache_key))
        self.assertIsNone(cache.get(own_key))
        self.assertEqual(cache.get(other_key), 1)

    def test_whitespace_and_nfkc_username_variants_share_the_target_limit(self):
        self.client.force_login(self.first_admin)
        target_key = self.username_cache_key(self.second_admin.username)
        variants = (
            f"  {self.second_admin.username}\t",
            "ｉｓｏｌａｔｅｄ-ａｄｍｉｎ-ｔｗｏ",
        )
        for expected_failures, username in enumerate(variants, start=1):
            self.assertEqual(self.username_cache_key(username), target_key)
            self.assertEqual(self.login_attempt(username=username).status_code, 200)
            self.assertEqual(
                self.login_attempt(user=self.first_admin, password=self.password).status_code,
                302,
            )
            self.assertEqual(cache.get(target_key), expected_failures)

        self.assert_blocked(self.login_attempt())

    def test_success_with_normalized_username_clears_its_target_bucket(self):
        target_key = self.username_cache_key(self.second_admin.username)
        self.assertEqual(self.login_attempt().status_code, 200)
        self.assertEqual(cache.get(target_key), 1)

        self.assertEqual(
            self.login_attempt(
                username="  ｉｓｏｌａｔｅｄ-ａｄｍｉｎ-ｔｗｏ\t",
                password=self.password,
            ).status_code,
            302,
        )
        self.assertIsNone(cache.get(self.cache_key))
        self.assertIsNone(cache.get(target_key))
        self.assertEqual(self.client.session[SESSION_KEY], str(self.second_admin.pk))

    def test_login_get_and_other_requests_do_not_increment_or_clear_failures(self):
        self.assertEqual(self.login_attempt().status_code, 200)
        self.assertEqual(self.client.get("/admin/login/").status_code, 200)
        self.assertEqual(self.client.post("/favicon.ico", {}).status_code, 302)
        self.assertEqual(cache.get(self.cache_key), 1)

        # An existing session makes GET /admin/login/ redirect, not log in again.
        self.client.force_login(self.first_admin)
        self.assertEqual(self.client.get("/admin/login/").status_code, 302)
        self.assertEqual(self.client.get("/favicon.ico").status_code, 302)
        self.assertEqual(cache.get(self.cache_key), 1)

    @override_settings(REST_FRAMEWORK={**settings.REST_FRAMEWORK, "NUM_PROXIES": 1})
    def test_configured_trusted_proxy_uses_client_ip_for_failure_limit(self):
        client_ip = "203.0.113.61"
        for _ in range(2):
            self.assertEqual(
                self.login_attempt(HTTP_X_FORWARDED_FOR=client_ip).status_code, 200
            )
        self.assert_blocked(self.login_attempt(HTTP_X_FORWARDED_FOR=client_ip))
        self.assertEqual(
            self.login_attempt(HTTP_X_FORWARDED_FOR="203.0.113.62").status_code, 200
        )


@override_settings(
    ADMIN_LOGIN_MAX_FAILURES=2,
    ADMIN_LOGIN_FAILURE_WINDOW=60,
    CACHES=TEST_CACHE,
    REST_FRAMEWORK={**settings.REST_FRAMEWORK, "NUM_PROXIES": 0},
)
class AdminLoginSuccessEvidenceTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_authenticated_user_and_redirect_without_login_signal_count_as_failure(self):
        request = RequestFactory().post(
            "/admin/login/", REMOTE_ADDR="198.51.100.62"
        )
        request.user = SimpleNamespace(is_authenticated=True)
        middleware = AdminLoginRateLimitMiddleware(
            lambda current_request: HttpResponseRedirect("/admin/")
        )

        response = middleware(request)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(cache.get(middleware._cache_key(request)), 1)

    def test_blank_username_uses_only_ip_bucket(self):
        request = RequestFactory().post(
            "/admin/login/", {"username": " \t "}, REMOTE_ADDR="198.51.100.62"
        )
        middleware = AdminLoginRateLimitMiddleware(
            lambda current_request: HttpResponseRedirect("/admin/")
        )

        self.assertEqual(middleware(request).status_code, 302)
        self.assertIsNone(middleware._username_cache_key(request))
        self.assertEqual(cache.get(middleware._cache_key(request)), 1)

    def test_ip_limit_is_checked_before_parsing_post_data(self):
        request = RequestFactory().post(
            "/admin/login/", REMOTE_ADDR="198.51.100.62"
        )
        middleware = AdminLoginRateLimitMiddleware(
            lambda current_request: self.fail(
                "Blocked requests must not reach the login view"
            )
        )
        cache.set(middleware._cache_key(request), 2, timeout=60)

        with patch.object(type(request), "POST", new_callable=PropertyMock) as post:
            post.side_effect = AssertionError("Blocked requests must not parse POST data")
            self.assertEqual(middleware(request).status_code, 429)
