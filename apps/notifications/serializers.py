from rest_framework import serializers
from django.utils import timezone

from apps.clients.models import ClientProfile
from .models import Notification, ScheduledMessage, ScheduledMessageRecipient
from .scheduled_messages import deliver


class NotificationSerializer(serializers.ModelSerializer):
    notification_type_display = serializers.CharField(source='get_notification_type_display', read_only=True)

    class Meta:
        model = Notification
        fields = [
            'id', 'title', 'message', 'notification_type', 'notification_type_display',
            'is_read', 'related_submission_id', 'related_client_id', 'related_year_id',
            'created_at', 'read_at',
        ]
        read_only_fields = ['id', 'created_at']


class ScheduledMessageRecipientSerializer(serializers.ModelSerializer):
    client_name = serializers.CharField(source='client_profile.full_name', read_only=True)
    client_email = serializers.EmailField(source='client_profile.user.email', read_only=True)

    class Meta:
        model = ScheduledMessageRecipient
        fields = ['id', 'client_profile', 'client_name', 'client_email', 'email_status', 'sms_status', 'error_detail']
        read_only_fields = fields


class ScheduledMessageSerializer(serializers.ModelSerializer):
    recipients = ScheduledMessageRecipientSerializer(many=True, read_only=True)
    recipient_count = serializers.IntegerField(source='recipients.count', read_only=True)

    class Meta:
        model = ScheduledMessage
        fields = [
            'id', 'send_email', 'send_sms', 'email_subject', 'email_body', 'sms_body',
            'scheduled_for', 'status', 'created_at', 'sent_at', 'recipients', 'recipient_count',
        ]
        read_only_fields = fields


class ScheduledMessageCreateSerializer(serializers.Serializer):
    # Overall cap on a single bulk send.
    MAX_RECIPIENTS = 300

    # A "Send Now" batch above this size is routed through the scheduled-message
    # cron pipeline (send_due_scheduled_messages, runs every 2 min) instead of
    # being sent inline: sending is synchronous inside the HTTP request, so a
    # large batch risks exceeding the gateway timeout. At or below this size, the
    # batch still comfortably finishes inside one request/response cycle, so it
    # keeps sending inline and the admin gets the final result immediately.
    SYNC_SEND_THRESHOLD = 50

    client_ids = serializers.ListField(
        child=serializers.IntegerField(),
        allow_empty=False,
        max_length=MAX_RECIPIENTS,
        error_messages={
            'max_length': f'You can send to at most {MAX_RECIPIENTS} clients at a time.',
        },
    )
    send_email = serializers.BooleanField(default=False)
    send_sms = serializers.BooleanField(default=False)
    email_subject = serializers.CharField(max_length=200, required=False, allow_blank=True, default='')
    email_body = serializers.CharField(required=False, allow_blank=True, default='')
    sms_body = serializers.CharField(max_length=600, required=False, allow_blank=True, default='')
    scheduled_for = serializers.DateTimeField(required=False, allow_null=True, default=None)

    def validate(self, data):
        if not data.get('send_email') and not data.get('send_sms'):
            raise serializers.ValidationError('Select at least one channel: email or SMS.')
        if data.get('send_email') and not (data.get('email_subject') and data.get('email_body')):
            raise serializers.ValidationError('Email subject and body are required when Email is selected.')
        if data.get('send_sms') and not data.get('sms_body'):
            raise serializers.ValidationError('SMS body is required when SMS is selected.')

        valid_count = ClientProfile.objects.filter(id__in=data.get('client_ids', [])).count()
        if valid_count == 0:
            raise serializers.ValidationError({'client_ids': 'No valid clients found.'})
        return data

    def create(self, validated_data):
        request = self.context['request']
        client_ids = validated_data.pop('client_ids')
        profiles = ClientProfile.objects.filter(id__in=client_ids)

        # Admin explicitly picked a future time via the scheduler UI.
        explicitly_scheduled = bool(validated_data.get('scheduled_for'))

        # A large "Send Now" batch (no explicit scheduled_for) is auto-queued for the
        # cron pipeline instead of sending inline — see SYNC_SEND_THRESHOLD above.
        auto_scheduled = not explicitly_scheduled and profiles.count() > self.SYNC_SEND_THRESHOLD
        if auto_scheduled:
            validated_data['scheduled_for'] = timezone.now()

        message = ScheduledMessage.objects.create(created_by=request.user, **validated_data)
        # A selected channel starts as 'pending' so a not-yet-due scheduled message
        # displays correctly; deliver() overwrites these with the real outcome.
        email_status = 'pending' if message.send_email else 'not_applicable'
        sms_status = 'pending' if message.send_sms else 'not_applicable'
        ScheduledMessageRecipient.objects.bulk_create([
            ScheduledMessageRecipient(
                message=message, client_profile=p,
                email_status=email_status, sms_status=sms_status,
            )
            for p in profiles
        ])

        if not auto_scheduled and (not message.scheduled_for or message.scheduled_for <= timezone.now()):
            deliver(message)
            message.refresh_from_db()
        return message
