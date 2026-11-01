from django.core.management.base import BaseCommand

from catalog.file_security import scan_inquiry_attachment
from catalog.models import InquiryAttachment


class Command(BaseCommand):
    help = "Scan quarantined RFQ attachments and persist clean/infected/error status."

    def add_arguments(self, parser):
        parser.add_argument("attachment_ids", nargs="*", type=int)
        parser.add_argument(
            "--all",
            action="store_true",
            help="Rescan clean and infected files as well as pending/error files.",
        )

    def handle(self, *args, **options):
        queryset = InquiryAttachment.objects.select_related("inquiry").order_by("pk")
        if options["attachment_ids"]:
            queryset = queryset.filter(pk__in=options["attachment_ids"])
        elif not options["all"]:
            queryset = queryset.filter(
                scan_status__in=[
                    InquiryAttachment.ScanStatus.PENDING,
                    InquiryAttachment.ScanStatus.ERROR,
                ]
            )

        totals = {choice: 0 for choice, _ in InquiryAttachment.ScanStatus.choices}
        for attachment in queryset.iterator():
            totals[scan_inquiry_attachment(attachment)] += 1

        self.stdout.write(
            self.style.SUCCESS(
                "Attachment scan complete: "
                + ", ".join(f"{status}={count}" for status, count in totals.items())
            )
        )
