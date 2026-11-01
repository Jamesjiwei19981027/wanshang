from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import SiteSetting
from .tasks import sales_notification_email


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    SALES_NOTIFICATION_EMAIL="trusted-sales@example.com",
    CACHES={"default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "site-setting-permission-tests",
    }},
)
class SiteSettingAdminPermissionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.restricted_staff = User.objects.create_user(
            username="restricted-settings-staff", is_staff=True,
        )
        cls.authorized_staff = User.objects.create_user(
            username="authorized-settings-staff", is_staff=True,
        )
        cls.authorized_staff.user_permissions.add(Permission.objects.get(
            content_type__app_label="catalog",
            content_type__model="sitesetting",
            codename="add_sitesetting",
        ))
        cls.superuser = User.objects.create_superuser(
            username="settings-superuser", email="admin@example.com", password=None,
        )

    def setUp(self):
        temporary_storage = TemporaryDirectory(prefix="site-setting-permission-test-")
        self.addCleanup(temporary_storage.cleanup)
        storage_settings = override_settings(
            MEDIA_ROOT=Path(temporary_storage.name) / "public",
            PRIVATE_MEDIA_ROOT=Path(temporary_storage.name) / "private",
        )
        storage_settings.enable()
        self.addCleanup(storage_settings.disable)
        self.add_url = reverse("admin:catalog_sitesetting_add")
        self.payload = {
            "site_name": "Permission test site",
            "sales_email": "new-sales@example.com",
            "default_unit_system": "imperial",
            "_save": "Save",
        }

    def assert_no_configuration_change(self):
        self.assertFalse(SiteSetting.objects.exists())
        self.assertEqual(sales_notification_email(), "trusted-sales@example.com")
        self.assertEqual(mail.outbox, [])

    def test_staff_without_permission_cannot_get_add_form(self):
        self.assertFalse(self.restricted_staff.has_perm("catalog.add_sitesetting"))
        self.client.force_login(self.restricted_staff)

        response = self.client.get(self.add_url)

        self.assertEqual(response.status_code, 403)
        self.assert_no_configuration_change()

    def test_staff_without_permission_cannot_create_or_redirect_notifications(self):
        self.client.force_login(self.restricted_staff)

        response = self.client.post(self.add_url, self.payload)

        self.assertEqual(response.status_code, 403)
        self.assert_no_configuration_change()

    def assert_user_can_create(self, user):
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.add_url).status_code, 200)

        response = self.client.post(self.add_url, self.payload)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(SiteSetting.objects.count(), 1)
        setting = SiteSetting.objects.get()
        self.assertEqual(setting.site_name, self.payload["site_name"])
        self.assertEqual(setting.sales_email, self.payload["sales_email"])
        self.assertEqual(sales_notification_email(), self.payload["sales_email"])
        self.assertEqual(mail.outbox, [])

    def test_staff_with_add_permission_can_create_singleton(self):
        self.assertTrue(self.authorized_staff.has_perm("catalog.add_sitesetting"))
        self.assertFalse(self.authorized_staff.is_superuser)
        self.assert_user_can_create(self.authorized_staff)

    def test_superuser_can_create_singleton(self):
        self.assert_user_can_create(self.superuser)

    def assert_existing_singleton_blocks_add(self, user):
        original = SiteSetting.objects.create(
            site_name="Existing site", sales_email="existing-sales@example.com",
        )
        self.client.force_login(user)

        get_response = self.client.get(self.add_url)
        post_response = self.client.post(self.add_url, self.payload)

        self.assertEqual(get_response.status_code, 403)
        self.assertEqual(post_response.status_code, 403)
        self.assertEqual(SiteSetting.objects.count(), 1)
        original.refresh_from_db()
        self.assertEqual(original.site_name, "Existing site")
        self.assertEqual(original.sales_email, "existing-sales@example.com")
        self.assertEqual(sales_notification_email(), "existing-sales@example.com")
        self.assertEqual(mail.outbox, [])

    def test_existing_singleton_blocks_add_for_authorized_staff(self):
        self.assert_existing_singleton_blocks_add(self.authorized_staff)

    def test_existing_singleton_blocks_add_even_for_superuser(self):
        self.assert_existing_singleton_blocks_add(self.superuser)
