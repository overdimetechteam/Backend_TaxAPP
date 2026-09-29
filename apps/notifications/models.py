from django.db import models
from django.conf import settings


class Notification(models.Model):
    TYPE_CHOICES = [
        ('info', 'Information'),
        ('action_required', 'Action Required'),
        ('reminder', 'Reminder'),
        ('warning', 'Warning'),
    ]

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notifications'
    )
    title = models.CharField(max_length=200)
    message = models.TextField()
    notification_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default='info')
    is_read = models.BooleanField(default=False)
    related_submission_id = models.IntegerField(null=True, blank=True)
    related_client_id = models.IntegerField(null=True, blank=True)   # ClientProfile.id
    related_year_id = models.IntegerField(null=True, blank=True)     # TaxYear.id
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'notifications'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.title} -> {self.recipient.email}"


class ScheduledMessage(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('sending', 'Sending'),
        ('sent', 'Sent'),
        ('partially_failed', 'Partially Failed'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
    ]

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='scheduled_messages'
    )
    send_email = models.BooleanField(default=False)
    send_sms = models.BooleanField(default=False)
    email_subject = models.CharField(max_length=200, blank=True)
    email_body = models.TextField(blank=True)
    sms_body = models.CharField(max_length=600, blank=True)
    scheduled_for = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'scheduled_messages'
        ordering = ['-created_at']

    def __str__(self):
        return f"Message #{self.id} by {self.created_by.email} ({self.status})"


class ScheduledMessageCampaign(models.Model):
    """
    One row per eSMS campaign created for this message's bulk SMS send (normally
    just one — chunking only happens if the recipient list exceeds eSMS's
    per-campaign limit). Lets the delivery-report webhook look up which message
    a given campaignId belongs to, so it can update the matching recipient's
    sms_status when eSMS calls back with real per-number delivery results.
    """
    message = models.ForeignKey(ScheduledMessage, on_delete=models.CASCADE, related_name='campaigns')
    campaign_id = models.CharField(max_length=32, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'scheduled_message_campaigns'

    def __str__(self):
        return f"Campaign {self.campaign_id} for message #{self.message_id}"


class ScheduledMessageRecipient(models.Model):
    CHANNEL_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('sent', 'Sent'),
        ('failed', 'Failed'),
        ('skipped', 'Skipped'),
        ('not_applicable', 'N/A'),
    ]

    message = models.ForeignKey(ScheduledMessage, on_delete=models.CASCADE, related_name='recipients')
    client_profile = models.ForeignKey('clients.ClientProfile', on_delete=models.CASCADE)
    email_status = models.CharField(max_length=20, choices=CHANNEL_STATUS_CHOICES, default='not_applicable')
    sms_status = models.CharField(max_length=20, choices=CHANNEL_STATUS_CHOICES, default='not_applicable')
    error_detail = models.TextField(blank=True)

    class Meta:
        db_table = 'scheduled_message_recipients'
        unique_together = [('message', 'client_profile')]
        ordering = ['id']

    def __str__(self):
        return f"{self.client_profile.full_name} — message #{self.message_id}"
