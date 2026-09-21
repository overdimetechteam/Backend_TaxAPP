"""
Management command: send_due_scheduled_messages

Sends any consultant-scheduled messages (ScheduledMessage) whose
scheduled_for time has passed. Schedule via cron every few minutes:
  */5 * * * * /path/to/python /path/to/manage.py send_due_scheduled_messages
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.notifications.models import ScheduledMessage
from apps.notifications.scheduled_messages import deliver


class Command(BaseCommand):
    help = 'Send scheduled messages whose scheduled_for time has passed'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='List due messages without sending them')

    def handle(self, *args, **options):
        due = ScheduledMessage.objects.filter(status='pending', scheduled_for__lte=timezone.now())

        if options['dry_run']:
            for message in due:
                self.stdout.write(f'[DRY RUN] Would send message {message.id} (created_by={message.created_by.email})')
            self.stdout.write(self.style.SUCCESS(f'{due.count()} message(s) due.'))
            return

        # Claim the batch atomically before delivering. A single UPDATE ... WHERE
        # status='pending' means an overlapping cron run (a batch that takes longer
        # than the cron interval) cannot pick up the same messages and double-send.
        # update() is used rather than select_for_update() because SQLite provides
        # no row locking; the claim works identically on SQLite and Postgres.
        due_ids = list(due.values_list('id', flat=True))
        ScheduledMessage.objects.filter(id__in=due_ids, status='pending').update(status='sending')
        claimed = list(ScheduledMessage.objects.filter(id__in=due_ids, status='sending'))

        count = 0
        for message in claimed:
            deliver(message)
            count += 1
            self.stdout.write(f'  Sent scheduled message {message.id} — status={message.status}')

        self.stdout.write(self.style.SUCCESS(f'Done. {count} scheduled message(s) processed.'))
