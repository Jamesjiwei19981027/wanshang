from unittest.mock import patch

from django.conf import settings
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .models import ContactMessage, Inquiry, InquiryAttachment


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    SECURE_SSL_REDIRECT=False,
    CACHES={"default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "admin-action-permission-tests",
    }},
)
class AdminActionPermissionTests(TestCase):
    inquiry_actions = {
        "mark_qualified": Inquiry.Status.QUALIFIED,
        "mark_contacted": Inquiry.Status.CONTACTED,
        "mark_spam": Inquiry.Status.SPAM,
        "retry_notifications": Inquiry.Status.NEW,
        "rescan_attachments": Inquiry.Status.NEW,
    }
    contact_actions = {
        "mark_resolved": ContactMessage.Status.RESOLVED,
        "mark_spam": ContactMessage.Status.SPAM,
        "retry_notifications": ContactMessage.Status.NEW,
    }

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.viewer = User.objects.create_user(
            username="action-view-only", is_staff=True,
        )
        cls.editor = User.objects.create_user(
            username="action-editor", is_staff=True,
        )
        for model in (Inquiry, ContactMessage):
            content_type = ContentType.objects.get_for_model(model)
            view = Permission.objects.get(
                content_type=content_type,
                codename=f"view_{model._meta.model_name}",
            )
            change = Permission.objects.get(
                content_type=content_type,
                codename=f"change_{model._meta.model_name}",
            )
            cls.viewer.user_permissions.add(view)
            cls.editor.user_permissions.add(view, change)

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        # Use a server-issued token so forged action POSTs reach authorization.
        self.assertEqual(self.client.get(reverse("admin:login")).status_code, 200)
        self.csrf_token = self.client.cookies[settings.CSRF_COOKIE_NAME].value
        self.inquiry = Inquiry.objects.create(
            name="Inquiry action buyer", email="inquiry-action@example.com",
            privacy_consent=True,
        )
        self.attachment = InquiryAttachment.objects.create(
            inquiry=self.inquiry,
            file="quarantine/action-permission.pdf",
            original_name="drawing.pdf", size=16,
        )
        self.contact = ContactMessage.objects.create(
            name="Contact action buyer", email="contact-action@example.com",
            message="Please reply.", privacy_consent=True,
        )
        self.inquiry_notification = self.enterContext(patch(
            "catalog.admin.enqueue_inquiry_notification", return_value=True,
        ))
        self.contact_notification = self.enterContext(patch(
            "catalog.admin.enqueue_contact_notification", return_value=True,
        ))
        self.scan = self.enterContext(patch(
            "catalog.admin.schedule_inquiry_attachment_scan",
            return_value=InquiryAttachment.ScanStatus.PENDING,
        ))

    def action_url(self, obj):
        return reverse(f"admin:catalog_{obj._meta.model_name}_changelist")

    def post_action(self, obj, action, *, with_csrf=True):
        headers = {"HTTP_X_CSRFTOKEN": self.csrf_token} if with_csrf else {}
        return self.client.post(
            self.action_url(obj),
            {"action": action, "_selected_action": str(obj.pk), "index": "0"},
            **headers,
        )

    def assert_no_external_work(self):
        self.inquiry_notification.assert_not_called()
        self.contact_notification.assert_not_called()
        self.scan.assert_not_called()
        self.assertEqual(mail.outbox, [])

    def assert_viewer_cannot_execute_actions(self, obj, actions):
        self.client.force_login(self.viewer)
        self.assertTrue(self.viewer.has_perm(f"catalog.view_{obj._meta.model_name}"))
        self.assertFalse(self.viewer.has_perm(f"catalog.change_{obj._meta.model_name}"))
        original = type(obj).objects.filter(pk=obj.pk).values().get()
        response = self.client.get(self.action_url(obj))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["action_form"])

        for action in actions:
            with self.subTest(action=action):
                response = self.post_action(obj, action)
                # Django renders the read-only list when no actions are allowed.
                # A 200 also establishes this was not rejected for invalid CSRF.
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    type(obj).objects.filter(pk=obj.pk).values().get(), original,
                )
                self.attachment.refresh_from_db()
                self.assertEqual(
                    self.attachment.scan_status, InquiryAttachment.ScanStatus.PENDING,
                )
                self.assertFalse(LogEntry.objects.filter(user=self.viewer).exists())
                self.assert_no_external_work()

    def test_view_only_staff_cannot_forge_any_inquiry_action_with_valid_csrf(self):
        self.assert_viewer_cannot_execute_actions(self.inquiry, self.inquiry_actions)

    def test_view_only_staff_cannot_forge_any_contact_action_with_valid_csrf(self):
        self.assert_viewer_cannot_execute_actions(self.contact, self.contact_actions)

    def assert_editor_can_execute_actions(self, obj, actions):
        self.client.force_login(self.editor)
        response = self.client.get(self.action_url(obj))
        self.assertEqual(response.status_code, 200)
        choices = dict(response.context["action_form"].fields["action"].choices)
        self.assertTrue(set(actions).issubset(choices))

        for action, expected_status in actions.items():
            with self.subTest(action=action):
                type(obj).objects.filter(pk=obj.pk).update(status=type(obj).Status.NEW)
                if isinstance(obj, ContactMessage):
                    # Verify marking spam also clears an existing resolved flag.
                    ContactMessage.objects.filter(pk=obj.pk).update(
                        is_resolved=action == "mark_spam",
                    )
                previous_logs = LogEntry.objects.filter(user=self.editor).count()
                self.inquiry_notification.reset_mock()
                self.contact_notification.reset_mock()
                self.scan.reset_mock()

                response = self.post_action(obj, action)

                self.assertEqual(response.status_code, 302)
                obj.refresh_from_db()
                self.assertEqual(obj.status, expected_status)
                if isinstance(obj, ContactMessage):
                    self.assertEqual(obj.is_resolved, action == "mark_resolved")
                logs = LogEntry.objects.filter(user=self.editor)
                self.assertEqual(logs.count(), previous_logs + 1)
                entry = logs.latest("pk")
                self.assertEqual(entry.action_flag, CHANGE)
                self.assertEqual(entry.object_id, str(obj.pk))
                self.assertEqual(
                    entry.content_type, ContentType.objects.get_for_model(obj),
                )
                if action == "retry_notifications":
                    notification = (
                        self.inquiry_notification if isinstance(obj, Inquiry)
                        else self.contact_notification
                    )
                    notification.assert_called_once_with(obj.pk)
                elif action == "rescan_attachments":
                    self.scan.assert_called_once_with(self.attachment)
                else:
                    self.assert_no_external_work()
                self.assertEqual(mail.outbox, [])

    def test_change_staff_can_execute_every_inquiry_action(self):
        self.assert_editor_can_execute_actions(self.inquiry, self.inquiry_actions)

    def test_change_staff_can_execute_every_contact_action(self):
        self.assert_editor_can_execute_actions(self.contact, self.contact_actions)

    def test_admin_action_post_requires_csrf_even_for_change_staff(self):
        self.client.force_login(self.editor)

        response = self.post_action(self.inquiry, "mark_spam", with_csrf=False)

        self.assertEqual(response.status_code, 403)
        self.inquiry.refresh_from_db()
        self.assertEqual(self.inquiry.status, Inquiry.Status.NEW)
        self.assertFalse(LogEntry.objects.filter(user=self.editor).exists())
        self.assert_no_external_work()
