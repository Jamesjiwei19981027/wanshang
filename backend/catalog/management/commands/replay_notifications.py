from django.core.management.base import BaseCommand
from django.db.models import Q

from catalog.models import ContactMessage, Inquiry
from catalog.tasks import (
    QUEUE_ERROR_PREFIX,
    enqueue_contact_notification,
    enqueue_inquiry_notification,
)


class Command(BaseCommand):
    help = "Replay persisted RFQ and Contact notification queue failures."

    def add_arguments(self, parser):
        parser.add_argument(
            "--kind",
            choices=("all", "inquiry", "contact"),
            default="all",
        )
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument(
            "--all-pending",
            action="store_true",
            help="Queue every incomplete notification, not only broker failures.",
        )

    def handle(self, *args, **options):
        limit = options["limit"]
        if limit < 1 or limit > 10_000:
            self.stderr.write(self.style.ERROR("--limit must be between 1 and 10,000."))
            return

        queued = 0
        failed = 0
        if options["kind"] in {"all", "inquiry"}:
            queryset = Inquiry.objects.filter(
                Q(notification_sent_at__isnull=True)
                | Q(confirmation_sent_at__isnull=True)
            )
            if not options["all_pending"]:
                queryset = queryset.filter(notification_error__startswith=QUEUE_ERROR_PREFIX)
            for inquiry_id in queryset.order_by("pk").values_list("pk", flat=True)[:limit]:
                if enqueue_inquiry_notification(inquiry_id):
                    queued += 1
                else:
                    failed += 1

        if options["kind"] in {"all", "contact"}:
            queryset = ContactMessage.objects.filter(
                Q(notification_sent_at__isnull=True)
                | Q(confirmation_sent_at__isnull=True)
            )
            if not options["all_pending"]:
                queryset = queryset.filter(notification_error__startswith=QUEUE_ERROR_PREFIX)
            remaining = max(limit - queued - failed, 0)
            for message_id in queryset.order_by("pk").values_list("pk", flat=True)[:remaining]:
                if enqueue_contact_notification(message_id):
                    queued += 1
                else:
                    failed += 1

        self.stdout.write(
            self.style.SUCCESS(f"Notification replay complete: queued={queued}, failed={failed}.")
        )
