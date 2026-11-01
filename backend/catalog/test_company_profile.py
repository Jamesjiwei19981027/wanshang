from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from .models import CompanyPhoto, CompanyProfile, SiteSetting


class CompanyProfileTests(TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="company-profile-test-")
        self.addCleanup(temporary.cleanup)
        storage_settings = override_settings(MEDIA_ROOT=Path(temporary.name), MEDIA_URL="/media/")
        storage_settings.enable()
        self.addCleanup(storage_settings.disable)

    def test_missing_or_draft_profile_never_exposes_unpublished_content(self):
        self.assertEqual(self.client.get("/api/company-profile/").json(), {})
        CompanyProfile.objects.create(introduction="Unpublished company information.")
        self.assertEqual(self.client.get("/api/company-profile/").json(), {})

    def test_published_profile_and_ordered_photos_are_returned(self):
        profile = CompanyProfile.objects.create(headline="Verified company profile", introduction="Verified introduction.", business_role="trading", is_published=True)
        for order in (2, 1):
            image = BytesIO()
            Image.new("RGB", (2, 2), "blue").save(image, "PNG")
            CompanyPhoto.objects.create(profile=profile, image=SimpleUploadedFile(f"company-{order}.png", image.getvalue(), content_type="image/png"), alt_text=f"Company photo {order}", sort_order=order)
        response = self.client.get("/api/company-profile/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["business_role_label"], "Trading company")
        self.assertEqual([photo["alt_text"] for photo in payload["photos"]], ["Company photo 1", "Company photo 2"])
        self.assertTrue(payload["photos"][0]["image_url"].startswith("http://testserver/media/company/"))
        profile.is_published = False
        profile.save()
        self.assertEqual(self.client.get("/api/company-profile/").json(), {})

    def test_publishing_requires_an_introduction(self):
        profile = CompanyProfile(is_published=True, introduction="  ")
        with self.assertRaises(ValidationError) as error:
            profile.full_clean()
        self.assertIn("introduction", error.exception.message_dict)

    def test_public_api_is_read_only_and_versioned(self):
        self.assertEqual(self.client.post("/api/company-profile/", {"introduction": "Untrusted"}).status_code, 405)
        response = self.client.get("/api/v1/company-profile/")
        self.assertTrue(response.json()["success"])
        self.assertEqual(response.json()["data"], {})
        self.assertFalse(CompanyProfile.objects.exists())

    def test_new_site_fields_are_public_but_notification_email_remains_private(self):
        SiteSetting.objects.create(site_name="Verified site", tagline="Public brand line", map_url="https://maps.example.com/company", linkedin_url="https://www.linkedin.com/company/verified", sales_email="private-sales@example.com")
        payload = self.client.get("/api/site-settings/").json()
        self.assertEqual(payload["tagline"], "Public brand line")
        self.assertEqual(payload["map_url"], "https://maps.example.com/company")
        self.assertEqual(payload["linkedin_url"], "https://www.linkedin.com/company/verified")
        self.assertNotIn("sales_email", payload)

    def test_new_contact_urls_reject_unsafe_values(self):
        for name in ("map_url", "linkedin_url"):
            with self.subTest(field=name):
                profile = SiteSetting(site_name="Verified site", **{name: "http://127.0.0.1/private"})
                with self.assertRaises(ValidationError):
                    profile.full_clean()

    def test_unsafe_legacy_contact_urls_are_not_returned(self):
        SiteSetting.objects.create(site_name="Verified site", map_url="http://127.0.0.1/private", linkedin_url="javascript:alert(1)")
        payload = self.client.get("/api/site-settings/").json()
        self.assertEqual(payload["map_url"], "")
        self.assertEqual(payload["linkedin_url"], "")

    def test_admin_requires_permission_to_add_a_profile(self):
        user = get_user_model().objects.create_user(username="profile-staff", is_staff=True)
        self.client.force_login(user)
        url = reverse("admin:catalog_companyprofile_add")
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {"introduction": "Untrusted profile"}).status_code, 403)
        self.assertFalse(CompanyProfile.objects.exists())

    def test_authorized_editor_can_save_and_publish_profile(self):
        user = get_user_model().objects.create_user(username="profile-editor", is_staff=True)
        user.user_permissions.add(*Permission.objects.filter(content_type__app_label="catalog", content_type__model="companyprofile", codename__in=["add_companyprofile", "view_companyprofile", "change_companyprofile"]))
        self.client.force_login(user)
        payload = {"introduction": "Verified company introduction.", "business_role": "trading", "is_published": "on", "photos-TOTAL_FORMS": "0", "photos-INITIAL_FORMS": "0", "photos-MIN_NUM_FORMS": "0", "photos-MAX_NUM_FORMS": "1000", "_save": "Save"}
        response = self.client.post(reverse("admin:catalog_companyprofile_add"), payload)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(CompanyProfile.objects.get().is_published)
        self.assertEqual(self.client.get("/api/company-profile/").json()["introduction"], payload["introduction"])
        self.assertEqual(self.client.get(reverse("admin:catalog_companyprofile_add")).status_code, 403)
