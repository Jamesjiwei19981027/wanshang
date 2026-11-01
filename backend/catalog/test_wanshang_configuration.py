from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from .models import Category, CompanyProfile, Product, SiteSetting


class WanshangConfigurationTests(TestCase):
    def configure(self, **options):
        call_command("configure_wanshang", stdout=StringIO(), **options)

    def test_dry_run_leaves_the_database_unchanged(self):
        self.configure(dry_run=True)
        self.assertFalse(SiteSetting.objects.exists())
        self.assertFalse(CompanyProfile.objects.exists())
        self.assertFalse(Category.objects.exists())

    def test_configuration_is_repeatable_and_does_not_invent_products(self):
        self.configure()
        self.configure()
        self.assertEqual(SiteSetting.objects.count(), 1)
        self.assertEqual(CompanyProfile.objects.count(), 1)
        self.assertEqual(Category.objects.count(), 6)
        self.assertFalse(Product.objects.exists())
        profile = CompanyProfile.objects.get()
        self.assertEqual(profile.business_role, CompanyProfile.BusinessRole.MANUFACTURING)
        self.assertTrue(profile.is_published)
        for label in ("American-Type Drum Brake Axles", "Spoke-Wheel Axles", "Single-Point Suspension", "Trailer Landing Gear"):
            self.assertIn(label, profile.main_products)
        self.assertIn("target markets", profile.markets)

    def test_configuration_preserves_unrelated_editor_settings_and_categories(self):
        SiteSetting.objects.create(site_name="Old brand", business_hours="By appointment", linkedin_url="https://www.linkedin.com/company/example")
        Category.objects.create(name="Existing family", slug="existing-family")
        self.configure()
        site = SiteSetting.objects.get()
        self.assertEqual(site.business_hours, "By appointment")
        self.assertEqual(site.linkedin_url, "https://www.linkedin.com/company/example")
        self.assertTrue(Category.objects.filter(slug="existing-family").exists())

    def test_supplied_identity_is_available_on_both_public_api_versions(self):
        self.configure()
        payload = self.client.get("/api/site-settings/").json()
        self.assertEqual(payload["legal_name"], "Shandong Wanshang International Trading Co., Ltd.")
        self.assertEqual(payload["chinese_name"], "山东万商国际商贸有限公司")
        self.assertEqual(payload["contact_person"], "Si Mingzhu (司明珠)")
        self.assertEqual(payload["contact_email"], "simingzhu727@gmail.com")
        self.assertEqual(payload["phone_number"], "+86 15253754552")
        self.assertEqual(payload["website_url"], "https://www.wan-shang.com")
        self.assertEqual(payload["whatsapp_link"], "")
        self.assertNotIn("sales_email", payload)
        self.assertEqual(self.client.get("/api/v1/site-settings/").json()["data"], payload)

    def test_public_website_url_uses_existing_url_security_rules(self):
        from django.core.exceptions import ValidationError

        for value in ("http://127.0.0.1/private", "javascript:alert(1)", "https://user:password@example.com"):
            with self.subTest(value=value):
                site = SiteSetting(site_name="WANSHANG", website_url=value)
                with self.assertRaises(ValidationError):
                    site.full_clean()
        SiteSetting.objects.create(site_name="WANSHANG", website_url="http://127.0.0.1/private")
        self.assertEqual(self.client.get("/api/site-settings/").json()["website_url"], "")
