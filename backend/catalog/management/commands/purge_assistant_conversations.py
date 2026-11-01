from django.core.management.base import BaseCommand

from assistant.leads import purge_expired_conversations


class Command(BaseCommand):
    help = "Delete assistant conversations past their retention period (also runs automatically once a day)."

    def handle(self, *args, **options):
        deleted = purge_expired_conversations()
        self.stdout.write(self.style.SUCCESS(f"Deleted {deleted} expired record(s)."))
