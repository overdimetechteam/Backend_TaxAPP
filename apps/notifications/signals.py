from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Notification
from .email_utils import send_notification_email
from .sms_utils import send_sms, resolve_recipient_phone


@receiver(post_save, sender=Notification)
def on_notification_created(sender, instance, created, **kwargs):
    if created:
        send_notification_email(instance)
        _send_notification_sms(instance)


def _send_notification_sms(notification):
    """
    Sends the SMS and stashes the outcome on the (in-memory) notification
    instance as `_sms_result`: True (sent), False (attempted, failed), or
    None (no phone number on file — nothing attempted). Since post_save fires
    synchronously on the same instance the caller holds, code that just called
    Notification.objects.create(...) can read this back immediately via
    getattr(notification, '_sms_result', None) — e.g. to report the outcome
    in an API response.
    """
    recipient = notification.recipient
    phone = resolve_recipient_phone(recipient)
    if not phone:
        notification._sms_result = None
        return None
    # message is self-contained by convention (some are written as full letters,
    # e.g. "Dear Valued Client, ..."), so send it as-is rather than prefixing the
    # title — that reads awkwardly ("Title: Dear Valued Client, ...") and eats
    # into the SMS length budget for no benefit.
    text = notification.message.replace('\n', ' ').strip()
    if len(text) > 600:
        text = text[:597] + '...'
    result = send_sms([phone], text)
    notification._sms_result = result
    return result
