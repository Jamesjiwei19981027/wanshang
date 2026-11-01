import logging
import json
from datetime import datetime, timedelta
from string import Formatter
from typing import Callable

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.db.models import Model, Q
from django.urls import reverse
from django.utils import timezone

logger = logging.getLogger(__name__)
QUEUE_ERROR_PREFIX = "Queue publish failed:"
DEFAULT_NOTIFICATION_CLAIM_LEASE_SECONDS = 600


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
    ignore_result=True,
)
def scan_inquiry_attachment_task(self, attachment_id: int) -> None:
    from .file_security import scan_inquiry_attachment
    from .models import InquiryAttachment

    attachment = InquiryAttachment.objects.filter(pk=attachment_id).first()
    if attachment is None:
        return
    scan_inquiry_attachment(attachment)


def safe_subject_component(value: str, fallback: str) -> str:
    """Keep user-supplied labels readable without allowing mail header breaks."""
    cleaned = " ".join((value or "").split()).strip()
    return (cleaned or fallback)[:160]


def render_email_template(
    configured_template: str,
    fallback_template: str,
    context: dict[str, str],
    template_name: str,
) -> str:
    """Render a deliberately small, admin-trusted placeholder language."""
    template = (configured_template or "").strip() or fallback_template
    try:
        for _, field_name, format_spec, conversion in Formatter().parse(template):
            if field_name and field_name not in context:
                raise ValueError(f"Unknown placeholder: {field_name}")
            if format_spec or conversion:
                raise ValueError("Format specifications and conversions are not supported.")
        return template.format_map(context)
    except (AttributeError, IndexError, KeyError, ValueError):
        logger.warning(
            "Invalid email template; using the built-in fallback",
            extra={"template_name": template_name},
        )
        return fallback_template.format_map(context)


def sales_notification_email(site_setting=None) -> str:
    from .models import SiteSetting

    if site_setting is None:
        configured_email = (
            SiteSetting.objects.order_by("pk")
            .values_list("sales_email", flat=True)
            .first()
        )
    else:
        configured_email = site_setting.sales_email
    return configured_email or settings.SALES_NOTIFICATION_EMAIL


def _claim_delivery(
    model: type[Model],
    object_id: int,
    *,
    sent_field: str,
    claim_field: str,
) -> datetime | None:
    """Atomically claim one delivery leg, taking over only an expired lease."""
    lease_seconds = max(
        int(
            getattr(
                settings,
                "NOTIFICATION_CLAIM_LEASE_SECONDS",
                DEFAULT_NOTIFICATION_CLAIM_LEASE_SECONDS,
            )
        ),
        60,
    )
    claimed_at = timezone.now()
    stale_before = claimed_at - timedelta(seconds=lease_seconds)
    pending = model.objects.filter(
        pk=object_id,
        **{f"{sent_field}__isnull": True},
    ).filter(
        Q(**{f"{claim_field}__isnull": True})
        | Q(**{f"{claim_field}__lte": stale_before})
    )
    if pending.update(**{claim_field: claimed_at, "updated_at": claimed_at}) == 1:
        return claimed_at
    return None


def _release_delivery(
    model: type[Model],
    object_id: int,
    *,
    claim_field: str,
    claimed_at: datetime,
) -> bool:
    """Release only the lease owned by this worker."""
    return bool(
        model.objects.filter(
            pk=object_id,
            **{claim_field: claimed_at},
        ).update(**{claim_field: None, "updated_at": timezone.now()})
    )


def _complete_delivery(
    model: type[Model],
    object_id: int,
    *,
    sent_field: str,
    claim_field: str,
    claimed_at: datetime,
) -> bool:
    """Record success only while this worker still owns the delivery lease."""
    completed_at = timezone.now()
    return bool(
        model.objects.filter(
            pk=object_id,
            **{
                f"{sent_field}__isnull": True,
                claim_field: claimed_at,
            },
        ).update(
            **{
                sent_field: completed_at,
                claim_field: None,
                "updated_at": completed_at,
            }
        )
    )


def _deliver_once(
    model: type[Model],
    object_id: int,
    *,
    sent_field: str,
    claim_field: str,
    send: Callable[[], None],
) -> bool:
    claimed_at = _claim_delivery(
        model,
        object_id,
        sent_field=sent_field,
        claim_field=claim_field,
    )
    if claimed_at is None:
        return False
    try:
        send()
        if not _complete_delivery(
            model,
            object_id,
            sent_field=sent_field,
            claim_field=claim_field,
            claimed_at=claimed_at,
        ):
            raise RuntimeError("Email delivery lease expired before completion.")
    except Exception:
        _release_delivery(
            model,
            object_id,
            claim_field=claim_field,
            claimed_at=claimed_at,
        )
        raise
    return True


def _email_context(instance, site_setting) -> dict[str, str]:
    company = getattr(instance, "company", "") or ""
    return {
        "reference": instance.reference,
        "name": instance.name,
        "email": instance.email,
        "company": company,
        "company_label": company or instance.name or "Website enquiry",
        "country": getattr(instance, "country", "") or "",
        "application": getattr(instance, "application", "") or "",
        "subject": getattr(instance, "subject", "") or "",
        "site_name": getattr(site_setting, "site_name", "") or "Our team",
    }


def _configured_template(site_setting, field_name: str) -> str:
    return getattr(site_setting, field_name, "") if site_setting else ""


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
    ignore_result=True,
)
def send_inquiry_notification(self, inquiry_id: int) -> None:
    from .models import Inquiry, SiteSetting

    site_setting = SiteSetting.objects.order_by("pk").first()

    def send_sales_notification() -> None:
        inquiry = Inquiry.objects.prefetch_related("items", "attachments").get(
            pk=inquiry_id
        )
        context = _email_context(inquiry, site_setting)
        subject = render_email_template(
            _configured_template(site_setting, "inquiry_sales_subject_template"),
            "New RFQ {reference} — {company_label}",
            context,
            "inquiry_sales_subject_template",
        )
        intro = render_email_template(
            _configured_template(site_setting, "inquiry_sales_intro_template"),
            "A new RFQ has been submitted.",
            context,
            "inquiry_sales_intro_template",
        )
        item_lines = [
            f"- {item.product_snapshot.get('model', 'Custom item')} × {item.quantity}"
            for item in inquiry.items.all()
        ]
        attachment_lines = [
            (
                f"- {attachment.original_name} "
                f"({attachment.get_scan_status_display()}): "
                f"{settings.DJANGO_PUBLIC_BASE_URL}"
                f"{reverse('inquiry-attachment-download', kwargs={'pk': attachment.pk})}"
            )
            for attachment in inquiry.attachments.all()
        ]
        body = "\n".join([
            intro,
            "",
            f"Reference: {inquiry.reference}",
            f"Contact: {inquiry.name} <{inquiry.email}>",
            f"Company: {inquiry.company}",
            f"Country: {inquiry.country}",
            f"Quantity: {inquiry.quantity or 'Not specified'}",
            f"Application: {inquiry.application or 'Not specified'}",
            f"Destination port: {inquiry.destination_port or 'Not specified'}",
            f"Target delivery date: {inquiry.target_delivery_date or 'Not specified'}",
            "",
            "Products:",
            *(item_lines or ["- No catalogue product selected"]),
            "",
            f"Specifications: {json.dumps(inquiry.specifications, ensure_ascii=False)}",
            f"Attachments ({len(attachment_lines)}):",
            *(attachment_lines or ["- None"]),
            "",
            f"Message:\n{inquiry.message}",
            "",
            f"Review in Admin: {settings.DJANGO_PUBLIC_BASE_URL}/admin/catalog/inquiry/{inquiry.pk}/change/",
        ])
        send_mail(
            safe_subject_component(subject, f"New RFQ {inquiry.reference}"),
            body,
            settings.DEFAULT_FROM_EMAIL,
            [sales_notification_email(site_setting)],
            fail_silently=False,
        )

    def send_customer_confirmation() -> None:
        inquiry = Inquiry.objects.get(pk=inquiry_id)
        context = _email_context(inquiry, site_setting)
        subject = render_email_template(
            _configured_template(
                site_setting,
                "inquiry_confirmation_subject_template",
            ),
            "We received your RFQ {reference}",
            context,
            "inquiry_confirmation_subject_template",
        )
        intro = render_email_template(
            _configured_template(
                site_setting,
                "inquiry_confirmation_intro_template",
            ),
            "We received your request. Your reference is {reference}.",
            context,
            "inquiry_confirmation_intro_template",
        )
        send_mail(
            safe_subject_component(subject, f"We received your RFQ {inquiry.reference}"),
            (
                f"Hello {inquiry.name},\n\n"
                f"{intro} "
                "Our sales or engineering team will review the supplied configuration "
                "and respond using your contact details.\n\n"
                "Please retain this reference for follow-up."
            ),
            settings.DEFAULT_FROM_EMAIL,
            [inquiry.email],
            fail_silently=False,
        )

    try:
        _deliver_once(
            Inquiry,
            inquiry_id,
            sent_field="notification_sent_at",
            claim_field="notification_claimed_at",
            send=send_sales_notification,
        )
        _deliver_once(
            Inquiry,
            inquiry_id,
            sent_field="confirmation_sent_at",
            claim_field="confirmation_claimed_at",
            send=send_customer_confirmation,
        )
        Inquiry.objects.filter(
            pk=inquiry_id,
            notification_sent_at__isnull=False,
            confirmation_sent_at__isnull=False,
        ).update(notification_error="")
    except Exception as exc:
        Inquiry.objects.filter(pk=inquiry_id).update(notification_error=str(exc)[:2000])
        raise


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
    ignore_result=True,
)
def send_contact_notification(self, message_id: int) -> None:
    from .models import ContactMessage, SiteSetting

    site_setting = SiteSetting.objects.order_by("pk").first()

    def send_sales_notification() -> None:
        message = ContactMessage.objects.get(pk=message_id)
        context = _email_context(message, site_setting)
        subject = render_email_template(
            _configured_template(site_setting, "contact_sales_subject_template"),
            "Website contact {reference} — {company_label}",
            context,
            "contact_sales_subject_template",
        )
        intro = render_email_template(
            _configured_template(site_setting, "contact_sales_intro_template"),
            "A new website contact message has been submitted.",
            context,
            "contact_sales_intro_template",
        )
        send_mail(
            safe_subject_component(subject, f"Website contact {message.reference}"),
            (
                f"{intro}\n\n"
                f"Reference: {message.reference}\n"
                f"Subject: {message.subject or 'Website contact'}\n"
                f"{message.name} <{message.email}> wrote:\n\n{message.message}\n\n"
                f"Review in Admin: {settings.DJANGO_PUBLIC_BASE_URL}/admin/catalog/contactmessage/{message.pk}/change/"
            ),
            settings.DEFAULT_FROM_EMAIL,
            [sales_notification_email(site_setting)],
            fail_silently=False,
        )

    def send_customer_confirmation() -> None:
        message = ContactMessage.objects.get(pk=message_id)
        context = _email_context(message, site_setting)
        subject = render_email_template(
            _configured_template(
                site_setting,
                "contact_confirmation_subject_template",
            ),
            "We received your message {reference}",
            context,
            "contact_confirmation_subject_template",
        )
        intro = render_email_template(
            _configured_template(
                site_setting,
                "contact_confirmation_intro_template",
            ),
            "Thank you for contacting us.",
            context,
            "contact_confirmation_intro_template",
        )
        send_mail(
            safe_subject_component(
                subject,
                f"We received your message {message.reference}",
            ),
            (
                f"Hello {message.name},\n\n"
                f"{intro} Our team will review your message "
                "and respond using the contact details you supplied.\n\n"
                f"Your reference is {message.reference}."
            ),
            settings.DEFAULT_FROM_EMAIL,
            [message.email],
            fail_silently=False,
        )

    try:
        _deliver_once(
            ContactMessage,
            message_id,
            sent_field="notification_sent_at",
            claim_field="notification_claimed_at",
            send=send_sales_notification,
        )
        _deliver_once(
            ContactMessage,
            message_id,
            sent_field="confirmation_sent_at",
            claim_field="confirmation_claimed_at",
            send=send_customer_confirmation,
        )
        ContactMessage.objects.filter(
            pk=message_id,
            notification_sent_at__isnull=False,
            confirmation_sent_at__isnull=False,
        ).update(notification_error="")
    except Exception as exc:
        ContactMessage.objects.filter(pk=message_id).update(notification_error=str(exc)[:2000])
        raise


def enqueue_inquiry_notification(inquiry_id: int) -> bool:
    from .models import Inquiry

    try:
        send_inquiry_notification.delay(inquiry_id)
    except Exception as exc:
        Inquiry.objects.filter(pk=inquiry_id).update(
            notification_error=f"{QUEUE_ERROR_PREFIX} {exc}"[:2000],
        )
        logger.exception(
            "Unable to enqueue inquiry notification",
            extra={"inquiry_id": inquiry_id},
        )
        return False
    Inquiry.objects.filter(pk=inquiry_id).update(notification_queued_at=timezone.now())
    Inquiry.objects.filter(
        pk=inquiry_id,
        notification_error__startswith=QUEUE_ERROR_PREFIX,
    ).update(notification_error="")
    return True


def enqueue_contact_notification(message_id: int) -> bool:
    from .models import ContactMessage

    try:
        send_contact_notification.delay(message_id)
    except Exception as exc:
        ContactMessage.objects.filter(pk=message_id).update(
            notification_error=f"{QUEUE_ERROR_PREFIX} {exc}"[:2000],
        )
        logger.exception(
            "Unable to enqueue contact notification",
            extra={"message_id": message_id},
        )
        return False
    ContactMessage.objects.filter(pk=message_id).update(notification_queued_at=timezone.now())
    ContactMessage.objects.filter(
        pk=message_id,
        notification_error__startswith=QUEUE_ERROR_PREFIX,
    ).update(notification_error="")
    return True
