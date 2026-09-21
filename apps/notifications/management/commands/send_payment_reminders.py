"""
Management command: send_payment_reminders

Reminds clients to pay when they've had a processed return awaiting
payment for 3, 5, or 10 days. Each threshold fires at most once per
submission (latched via payment_reminder_<N>d_sent) and is skipped
entirely if payment has already been recorded by then.

Schedule via cron (e.g. daily at 9 AM):
  0 9 * * * /path/to/python /path/to/manage.py send_payment_reminders
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta

from apps.tax_forms.models import TaxSubmission
from apps.notifications.models import Notification

AWAITING_PAYMENT_STATUSES = ('awaiting_confirmation', 'confirmed')

THRESHOLDS = [
    (3, 'payment_reminder_3d_sent'),
    (5, 'payment_reminder_5d_sent'),
    (10, 'payment_reminder_10d_sent'),
]


class Command(BaseCommand):
    help = 'Send payment reminders to clients whose return has been awaiting payment for 3/5/10 days'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Print what would be sent without actually sending reminders',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        now = timezone.now()
        total_sent = 0

        for days, flag_field in THRESHOLDS:
            cutoff = now - timedelta(days=days)
            submissions = TaxSubmission.objects.filter(
                status__in=AWAITING_PAYMENT_STATUSES,
                payment_status='pending',
                reviewed_at__lte=cutoff,
                **{flag_field: False},
            ).select_related('client', 'tax_year')

            for submission in submissions:
                if dry_run:
                    self.stdout.write(
                        f'[DRY RUN] Would remind {submission.client.email} '
                        f'({days}-day threshold, submission {submission.id})'
                    )
                    continue

                Notification.objects.create(
                    recipient=submission.client,
                    title='Payment Reminder — Action Required',
                    message=(
                        f'Your tax return for {submission.tax_year.label} has been processed '
                        f'and payment is still pending after {days} days. Please contact the '
                        f'office to complete your payment and avoid delays in filing your return.'
                    ),
                    notification_type='action_required',
                    related_submission_id=submission.id,
                )
                setattr(submission, flag_field, True)
                submission.save(update_fields=[flag_field])
                total_sent += 1
                self.stdout.write(f'  Reminded {submission.client.email} — {days}-day threshold')

        if not dry_run:
            self.stdout.write(self.style.SUCCESS(f'Done. {total_sent} payment reminder(s) sent.'))
