import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.utils import timezone

from .sms_utils import send_sms

logger = logging.getLogger(__name__)


def _append_error(recipient, note):
    recipient.error_detail = (recipient.error_detail + f' | {note}').strip(' |')


def _send_email(recipient, subject, body, connection=None):
    email = recipient.client_profile.user.email
    if not email:
        recipient.email_status = 'skipped'
        recipient.save(update_fields=['email_status'])
        return

    try:
        EmailMultiAlternatives(
            subject=subject,
            body=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[email],
            connection=connection,
        ).send(fail_silently=False)
        recipient.email_status = 'sent'
    except Exception as exc:
        recipient.email_status = 'failed'
        _append_error(recipient, f'email: {exc}')
        logger.warning('Scheduled message email failed for %s: %s', email, exc)
    recipient.save(update_fields=['email_status', 'error_detail'])


def _send_sms(recipient, body):
    profile = recipient.client_profile
    phone = profile.mobile or profile.telephone
    if not phone:
        recipient.sms_status = 'skipped'
        recipient.save(update_fields=['sms_status'])
        return

    ok = send_sms([phone], body)
    recipient.sms_status = 'sent' if ok else 'failed'
    if not ok:
        _append_error(recipient, 'sms: delivery failed')
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
            for recipient in recipients:
                if message.send_email:
                    _send_email(recipient, message.email_subject, message.email_body, connection)
                if message.send_sms:
                    _send_sms(recipient, message.sms_body)
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:  # pragma: no cover - closing must never mask a result
                    logger.warning('Scheduled message %s: error closing mail connection.', message.id)

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
