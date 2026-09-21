from django.contrib import admin
from .models import Notification, ScheduledMessage, ScheduledMessageRecipient


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ['title', 'recipient', 'notification_type', 'is_read', 'created_at']
    list_filter = ['notification_type', 'is_read']
    search_fields = ['title', 'recipient__email']


class ScheduledMessageRecipientInline(admin.TabularInline):
    model = ScheduledMessageRecipient
    extra = 0
    readonly_fields = ['client_profile', 'email_status', 'sms_status', 'error_detail']


@admin.register(ScheduledMessage)
class ScheduledMessageAdmin(admin.ModelAdmin):
    list_display = ['id', 'created_by', 'send_email', 'send_sms', 'status', 'scheduled_for', 'created_at']
    list_filter = ['status', 'send_email', 'send_sms']
    # Delivery state is owned by deliver()/the cron command. Editing status back to
    # 'pending' in admin would make a past-due message get delivered a second time.
    readonly_fields = ['status', 'sent_at', 'created_at', 'created_by']
    inlines = [ScheduledMessageRecipientInline]
