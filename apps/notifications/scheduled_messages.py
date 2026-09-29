import logging
import smtplib
import ssl

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.utils import timezone

from .models import ScheduledMessageCampaign
from .sms_utils import send_bulk_sms, _normalize_msisdn, resolve_recipient_phone

logger = logging.getLogger(__name__)

# Exceptions that mean the SMTP *connection* itself died (dropped, timed out,
# rejected by the server mid-session) rather than anything specific to the
# message/recipient being sent. Worth a reconnect-and-retry, unlike e.g.
# SMTPRecipientsRefused, which means the connection is fine but that address
# was rejected — retrying that on a new connection would just fail again.
CONNECTION_ERRORS = (
    smtplib.SMTPServerDisconnected,
    smtplib.SMTPConnectError,
    ConnectionError,
    OSError,
    ssl.SSLError,
)


def _append_error(recipient, note):
    recipient.error_detail = (recipient.error_detail + f' | {note}').strip(' |')


def _reconnect(old_connection, message_id):
    """Best-effort: close whatever's left of a broken connection, then open a fresh one."""
    try:
        if old_connection is not None:
            old_connection.close()
    except Exception:  # pragma: no cover - closing an already-dead connection must never raise
        pass
    logger.warning('Scheduled message %s: mail connection dropped mid-batch — reconnecting.', message_id)
    new_connection = get_connection()
    new_connection.open()
    return new_connection


def _send_email(recipient, subject, body, connection, message_id):
    """
    Sends one recipient's email over the given (shared, whole-batch) connection.
    Returns the connection the caller should use for the *next* recipient —
    normally the same one passed in, but if this send failed because the
    connection itself died (not because of anything specific to this recipient),
    a fresh connection is opened, this recipient is retried once on it, and that
    new connection is returned so the rest of the batch keeps going on a working
    connection instead of every remaining recipient failing one by one. This is
    what used to turn a single dropped connection partway through a large batch
    into a "partially_failed" result for everyone sent afterwards.
    """
    email = recipient.client_profile.user.email
    if not email:
        recipient.email_status = 'skipped'
        recipient.save(update_fields=['email_status'])
        return connection

    def _attempt(conn):
        EmailMultiAlternatives(
            subject=subject,
            body=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[email],
            connection=conn,
        ).send(fail_silently=False)

    try:
        _attempt(connection)
        recipient.email_status = 'sent'
    except CONNECTION_ERRORS as exc:
        try:
            connection = _reconnect(connection, message_id)
            _attempt(connection)
            recipient.email_status = 'sent'
        except Exception as retry_exc:
            recipient.email_status = 'failed'
            _append_error(recipient, f'email: {retry_exc}')
            logger.warning('Scheduled message email failed for %s after reconnect attempt: %s', email, retry_exc)
    except Exception as exc:
        recipient.email_status = 'failed'
        _append_error(recipient, f'email: {exc}')
        logger.warning('Scheduled message email failed for %s: %s', email, exc)

    recipient.save(update_fields=['email_status', 'error_detail'])
    return connection


def _send_sms_bulk(message, recipients):
    """
    Sends the SMS to every reachable recipient as a single eSMS campaign (one API
    call), instead of one API call per recipient. Looping per recipient was the
    root cause of the bulk-sending issue — it multiplies API calls with recipient
    count and can exceed the account's 20 requests/second send-SMS limit,
    triggering errCode 117 ("too many requests") for the overflow. Recipients
    without a usable phone number are marked skipped up front and left out of
    the campaign entirely.
    """
    reachable = []  # [(recipient, normalized_phone), ...]
    for recipient in recipients:
        phone = resolve_recipient_phone(recipient.client_profile.user)
        normalized = _normalize_msisdn(phone) if phone else None
        if not normalized:
            recipient.sms_status = 'skipped'
            recipient.save(update_fields=['sms_status'])
        else:
            reachable.append((recipient, normalized))

    if not reachable:
        logger.warning(
            'Scheduled message %s: 0 of %s recipients had a usable phone number — nothing sent.',
            message.id, len(recipients),
        )
        return

    push_url = None
    if settings.PUBLIC_BASE_URL:
        push_url = f'{settings.PUBLIC_BASE_URL}/api/notifications/sms-delivery-webhook/'

    result = send_bulk_sms(
        [normalized for _, normalized in reachable],
        message.sms_body,
        push_notification_url=push_url,
    )

    for campaign_id in result['campaign_ids']:
        ScheduledMessageCampaign.objects.get_or_create(message=message, campaign_id=campaign_id)

    # 'sent' here means "accepted into the campaign by eSMS", not "confirmed
    # delivered" — if push_url was set, eSMS calls SmsDeliveryWebhookView back
    # with the real per-number outcome, which flips this to 'failed' if that
    # specific number didn't actually deliver. Without PUBLIC_BASE_URL configured
    # (e.g. local dev), no callback arrives and 'sent' here is the final status.
    sent_set = set(result['sent_numbers'])
    for recipient, normalized in reachable:
        if normalized in sent_set:
            recipient.sms_status = 'sent'
        else:
            recipient.sms_status = 'failed'
            _append_error(recipient, f"sms: {result['comment'] or 'delivery failed'}")
        recipient.save(update_fields=['sms_status', 'error_detail'])


def _open_email_connection(message):
    """
    One SMTP connection reused for the whole batch instead of one per email.
    Returns None if the connection cannot be opened (or e-mail is not selected),
    in which case each EmailMultiAlternatives falls back to its own connection
    and records its own per-recipient failure.
    """
    if not message.send_email:
        return None
    try:
        connection = get_connection()
        connection.open()
        return connection
    except Exception as exc:
        logger.warning(
            'Scheduled message %s: could not open a shared mail connection (%s); '
            'falling back to per-email connections.', message.id, exc,
        )
        return None


def deliver(message):
    """
    Never raises — a failure for one recipient/channel is recorded on that
    recipient and must not block the rest of the batch or leave the message
    stuck in 'sending'.
    """
    message.status = 'sending'
    message.save(update_fields=['status'])

    try:
        recipients = list(message.recipients.select_related('client_profile__user'))

        connection = _open_email_connection(message)
        try:
            if message.send_email:
                for recipient in recipients:
                    connection = _send_email(recipient, message.email_subject, message.email_body, connection, message.id)
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:  # pragma: no cover - closing must never mask a result
                    logger.warning('Scheduled message %s: error closing mail connection.', message.id)

        if message.send_sms:
            _send_sms_bulk(message, recipients)

        statuses = []
        for recipient in recipients:
            if message.send_email:
                statuses.append(recipient.email_status)
            if message.send_sms:
                statuses.append(recipient.sms_status)

        # Nothing actually delivered (no recipients, all skipped, or all failed)
        # is a failure from the consultant's point of view — never report 'sent'.
        if 'sent' not in statuses:
            message.status = 'failed'
        elif 'failed' in statuses:
            message.status = 'partially_failed'
        else:
            message.status = 'sent'
    except Exception:
        # deliver() promises never to raise and never to leave the message
        # stranded in 'sending' — always land on a terminal status.
        logger.exception('Scheduled message %s: unexpected error during delivery.', message.id)
        message.status = 'failed'

    message.sent_at = timezone.now()
    message.save(update_fields=['status', 'sent_at'])
    return message
