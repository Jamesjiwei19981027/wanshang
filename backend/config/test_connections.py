"""Connection configuration tests; constructing pools never opens a socket."""

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase
from redis import ConnectionPool

from .connections import (
    postgres_database_from_components,
    redis_url_from_components,
)


class SqliteConnectionURLTests(SimpleTestCase):
    def test_absolute_posix_path_retains_its_root(self):
        from .settings import database_from_url

        self.assertEqual(
            database_from_url("sqlite:////tmp/review.sqlite3")["NAME"],
            "/tmp/review.sqlite3",
        )

    def test_relative_memory_and_windows_paths_keep_their_contract(self):
        from .settings import database_from_url

        for url, expected in (
            ("sqlite:///review.sqlite3", "review.sqlite3"),
            ("sqlite:///:memory:", ":memory:"),
            ("sqlite:///C:/review.sqlite3", "C:/review.sqlite3"),
        ):
            with self.subTest(url=url):
                self.assertEqual(database_from_url(url)["NAME"], expected)


class ContainerConnectionSettingsTests(SimpleTestCase):
    def test_postgres_preserves_credentials_and_database_as_opaque_values(self):
        credentials = "  # @ / : % +$ 密码  "
        database = postgres_database_from_components(
            {
                "DJANGO_DB_HOST": "postgres",
                "DJANGO_DB_PORT": "5433",
                "DJANGO_DB_NAME": "catalog /:@#%+$",
                "DJANGO_DB_USER": "buyer /:@#%+$",
                "DJANGO_DB_PASSWORD": credentials,
            }
        )
        self.assertEqual(database["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual(database["HOST"], "postgres")
        self.assertEqual(database["PORT"], "5433")
        self.assertEqual(database["NAME"], "catalog /:@#%+$")
        self.assertEqual(database["USER"], "buyer /:@#%+$")
        self.assertEqual(database["PASSWORD"], credentials)
        self.assertGreater(database["OPTIONS"]["connect_timeout"], 0)

    def test_redis_client_decodes_special_passwords_exactly_once(self):
        passwords = ("#", "@", "/", ":", "%", " ", "+", "$", "  # @ / : % +$ 密码  ")
        for password in passwords:
            with self.subTest(password=password):
                url = redis_url_from_components(
                    {
                        "DJANGO_REDIS_HOST": "redis",
                        "DJANGO_REDIS_PASSWORD": password,
                    },
                    database=1,
                )
                options = ConnectionPool.from_url(url).connection_kwargs
                self.assertEqual(options["host"], "redis")
                self.assertEqual(options["port"], 6379)
                self.assertEqual(options["db"], 1)
                self.assertEqual(options["password"], password)

    def test_redis_client_preserves_acl_username_and_literal_percent_sequences(self):
        username = "worker /:@#%+$"
        password = "%23%40%2F%3A%25%20%2B%24"
        url = redis_url_from_components(
            {
                "DJANGO_REDIS_HOST": "redis",
                "DJANGO_REDIS_USER": username,
                "DJANGO_REDIS_PASSWORD": password,
                "DJANGO_REDIS_PORT": "6380",
            },
            database=3,
        )
        options = ConnectionPool.from_url(url).connection_kwargs
        self.assertEqual(options["username"], username)
        self.assertEqual(options["password"], password)
        self.assertEqual(options["port"], 6380)
        self.assertEqual(options["db"], 3)

    def test_ipv6_hosts_are_usable_by_the_connection_clients(self):
        address = "2001:db8::5"
        database = postgres_database_from_components({"DJANGO_DB_HOST": address})
        self.assertEqual(database["HOST"], address)
        self.assertEqual(database["PORT"], "5432")

        for host in (address, f"[{address}]"):
            with self.subTest(redis_host=host):
                url = redis_url_from_components(
                    {"DJANGO_REDIS_HOST": host}, database=0
                )
                options = ConnectionPool.from_url(url).connection_kwargs
                self.assertEqual(options["host"], address)
                self.assertEqual(options["port"], 6379)
                self.assertEqual(options["db"], 0)
                self.assertIsNone(options.get("password"))

    def test_missing_component_hosts_leave_legacy_url_fallback_available(self):
        for environment in (
            {},
            {"DJANGO_DB_HOST": " ", "DJANGO_REDIS_HOST": " "},
            {"DJANGO_DB_PASSWORD": "opaque", "DJANGO_REDIS_PASSWORD": "opaque"},
        ):
            with self.subTest(environment=environment):
                self.assertIsNone(postgres_database_from_components(environment))
                self.assertEqual(redis_url_from_components(environment, database=0), "")

    def test_invalid_ports_fail_closed_in_both_component_paths(self):
        for port in ("", "0", "65536", "-1", "+6379", "6.5", "port", "６３７９"):
            for helper, host_key, port_key in (
                (postgres_database_from_components, "DJANGO_DB_HOST", "DJANGO_DB_PORT"),
                (redis_url_from_components, "DJANGO_REDIS_HOST", "DJANGO_REDIS_PORT"),
            ):
                with self.subTest(port=port, component=port_key):
                    environment = {host_key: "service", port_key: port}
                    with self.assertRaisesMessage(ImproperlyConfigured, port_key):
                        if helper is redis_url_from_components:
                            helper(environment, database=0)
                        else:
                            helper(environment)

    def test_valid_port_range_boundaries_are_preserved(self):
        for port in ("1", "65535"):
            with self.subTest(port=port):
                database = postgres_database_from_components(
                    {"DJANGO_DB_HOST": "postgres", "DJANGO_DB_PORT": port}
                )
                self.assertEqual(database["PORT"], port)
                url = redis_url_from_components(
                    {"DJANGO_REDIS_HOST": "redis", "DJANGO_REDIS_PORT": port},
                    database=0,
                )
                self.assertEqual(ConnectionPool.from_url(url).connection_kwargs["port"], int(port))
