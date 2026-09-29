from django.urls import path
from .views import (
    NotificationListView, MarkReadView, UnreadCountView, SendReminderView,
    ScheduledMessageListCreateView, ScheduledMessageCancelView, SmsDeliveryWebhookView,
)

urlpatterns = [
    path('', NotificationListView.as_view(), name='notifications'),
    path('unread-count/', UnreadCountView.as_view(), name='unread_count'),
    path('mark-read/', MarkReadView.as_view(), name='mark_all_read'),
    path('<int:pk>/mark-read/', MarkReadView.as_view(), name='mark_read'),
    path('send-reminder/', SendReminderView.as_view(), name='send_reminder'),
    path('scheduled-messages/', ScheduledMessageListCreateView.as_view(), name='scheduled_messages'),
    path('scheduled-messages/<int:pk>/cancel/', ScheduledMessageCancelView.as_view(), name='scheduled_message_cancel'),
    path('sms-delivery-webhook/', SmsDeliveryWebhookView.as_view(), name='sms_delivery_webhook'),
]
