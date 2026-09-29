import logging
import re
import time

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

_LOGIN_CACHE_KEY = 'esms_login_token'


def resolve_recipient_phone(user):
    """
    User.phone is a separate, optional field that's rarely filled in — the
    number a client actually gives us usually lives on their ClientProfile
    (mobile / telephone) instead. Prefer User.phone (covers staff created with
    a phone number), then fall back to the client's profile.

    Every SMS send path (single notifications, bulk broadcasts) must resolve a
    recipient's phone via this one function — a client whose number is only on
    User.phone would otherwise be silently skipped by any path that checks
    ClientProfile.mobile/telephone alone.
    """
    if user.phone:
        return user.phone
    profile = getattr(user, 'client_profile', None)
    if profile:
        return profile.mobile or profile.telephone or None
    return None


def _normalize_msisdn(raw):
    """
    Normalize a Sri Lankan mobile number to the 9-digit format eSMS expects
    (e.g. "714551682"). Accepts local (0771234567), international
    (+94771234567 / 94771234567) or bare 9-digit input. Returns None if the
    number can't be normalized to a valid-looking mobile number.
    """
    if not raw:
        return None
    digits = re.sub(r'\D', '', str(raw))
    if digits.startswith('94') and len(digits) == 11:
        digits = digits[2:]
    elif digits.startswith('0') and len(digits) == 10:
        digits = digits[1:]
    if len(digits) == 9 and digits[0] == '7':
        return digits
    return None


def _login_for_token():
    """Username/password login fallback — only used if ESMS_API_KEY is not set."""
    cached = cache.get(_LOGIN_CACHE_KEY)
    if cached:
        return cached

    if not (settings.ESMS_USERNAME and settings.ESMS_PASSWORD):
        logger.warning('eSMS: no ESMS_API_KEY or ESMS_USERNAME/ESMS_PASSWORD configured — cannot send SMS.')
        return None

    try:
        resp = requests.post(
            settings.ESMS_LOGIN_URL,
            json={'username': settings.ESMS_USERNAME, 'password': settings.ESMS_PASSWORD},
            headers={'Content-Type': 'application/json'},
            timeout=10,
        )
        data = resp.json()
    except Exception as exc:
        logger.warning('eSMS: login request failed: %s', exc)
        return None

    if data.get('status') != 'success' or not data.get('token'):
        logger.warning('eSMS: login failed — %s', data.get('comment'))
        return None

    token = data['token']
    # Cache for slightly less than the reported expiration (default 12h) so we
    # refresh proactively instead of hitting a 100 (expired token) error.
    ttl = max(60, int(data.get('expiration') or 43200) - 300)
    cache.set(_LOGIN_CACHE_KEY, token, timeout=ttl)
    return token


def _get_token(force_refresh=False):
    if settings.ESMS_API_KEY:
        return settings.ESMS_API_KEY
    if force_refresh:
        cache.delete(_LOGIN_CACHE_KEY)
    return _login_for_token()


# eSMS's tested-reliable ceiling for recipients in a single campaign (API doc §3.1.2).
MAX_RECIPIENTS_PER_CAMPAIGN = 1000


def _post_campaign(normalized, message, push_notification_url=None):
    """
    Shared POST-to-eSMS logic used by both send_sms() and send_bulk_sms(): builds
    the payload (one msisdn array, one campaign), handles the token-expiry retry,
    and returns the parsed response dict, or None if the request itself failed
    (network error, non-JSON response, etc). Never raises.
    """
    token = _get_token()
    if not token:
        return None

    payload = {
        'msisdn': [{'mobile': m} for m in normalized],
        'message': message[:600],  # keep well within multi-part limits
        'transaction_id': int(time.time() * 1_000_000),
        'payment_method': 0,
    }
    if settings.ESMS_SENDER_ADDRESS:
        payload['sourceAddress'] = settings.ESMS_SENDER_ADDRESS
    if push_notification_url:
        payload['push_notification_url'] = push_notification_url

    def _post(bearer_token):
        return requests.post(
            settings.ESMS_SEND_URL,
            json=payload,
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {bearer_token}',
            },
            timeout=10,
        )

    try:
        resp = _post(token)
        data = resp.json()
    except Exception as exc:
        logger.warning('eSMS: send request failed for %s recipients: %s', len(normalized), exc)
        return None

    # Token expired (errCode 100) — refresh once and retry, but only when we're
    # managing our own login token (a static ESMS_API_KEY can't be refreshed here).
    if data.get('errCode') == 100 and not settings.ESMS_API_KEY:
        token = _get_token(force_refresh=True)
        if not token:
            return None
        try:
            resp = _post(token)
            data = resp.json()
        except Exception as exc:
            logger.warning('eSMS: retry send request failed for %s recipients: %s', len(normalized), exc)
            return None

    return data


def send_sms(mobile_numbers, message):
    """
    Send an SMS to one or more Sri Lankan mobile numbers via the Dialog eSMS API,
    as a single campaign (one API call regardless of recipient count).
    Returns True if the campaign was accepted by eSMS, False otherwise.
    Never raises — failures are logged so callers (e.g. notification creation)
    are never blocked by an SMS delivery problem.
    """
    if not getattr(settings, 'ESMS_ENABLED', True):
        return False

    normalized = [n for n in (_normalize_msisdn(m) for m in mobile_numbers) if n]
    if not normalized:
        logger.info('eSMS: no valid mobile numbers to send to (raw=%s)', mobile_numbers)
        return False

    data = _post_campaign(normalized, message)
    if data is None:
        return False

    if data.get('status') == 'success':
        logger.info('eSMS: sent to %s — %s', normalized, data.get('comment'))
        return True

    logger.warning('eSMS: send failed for %s — %s (errCode=%s)', normalized, data.get('comment'), data.get('errCode'))
    return False


def send_bulk_sms(mobile_numbers, message, push_notification_url=None):
    """
    Send one SMS campaign to many recipients in a single eSMS API call — the
    correct way to send bulk SMS (per the eSMS API doc's `msisdn` array), instead
    of looping one API call per recipient. Looping per recipient is what caused
    the original bulk-sending issue: it multiplies API calls 1-for-1 with the
    recipient count, which can exceed the account's 20 requests/second send-SMS
    limit and trigger errCode 117 ("too many requests") for the overflow.

    Chunks recipients into groups of MAX_RECIPIENTS_PER_CAMPAIGN if needed — this
    is a safety cap, not the normal case; almost every real send fits in one chunk
    and therefore one API call.

    Returns a dict:
        {
            'success': bool,               # every chunk's campaign was accepted
            'sent_numbers': [str, ...],     # normalized numbers actually included
            'skipped_numbers': [str, ...],  # raw inputs that failed normalization
            'campaign_ids': [str, ...],     # one per chunk, for delivery-report matching
            'comment': str,                 # last comment/error seen, for logging
        }
    Never raises.
    """
    result = {'success': False, 'sent_numbers': [], 'skipped_numbers': [], 'campaign_ids': [], 'comment': ''}

    if not getattr(settings, 'ESMS_ENABLED', True):
        result['comment'] = 'eSMS disabled'
        return result

    seen = set()
    normalized = []
    for raw in mobile_numbers:
        n = _normalize_msisdn(raw)
        if not n:
            result['skipped_numbers'].append(raw)
        elif n not in seen:
            seen.add(n)
            normalized.append(n)

    if not normalized:
        result['comment'] = 'No valid mobile numbers'
        logger.info('eSMS: bulk send — no valid mobile numbers (raw count=%s)', len(mobile_numbers))
        return result

    all_ok = True
    for i in range(0, len(normalized), MAX_RECIPIENTS_PER_CAMPAIGN):
        chunk = normalized[i:i + MAX_RECIPIENTS_PER_CAMPAIGN]
        data = _post_campaign(chunk, message, push_notification_url=push_notification_url)

        if data is None:
            all_ok = False
            result['comment'] = 'Send request failed'
            continue

        result['comment'] = data.get('comment') or result['comment']
        if data.get('status') == 'success':
            result['sent_numbers'].extend(chunk)
            campaign_id = (data.get('data') or {}).get('campaignId')
            if campaign_id is not None:
                result['campaign_ids'].append(str(campaign_id))
            logger.info('eSMS: bulk campaign sent to %s recipients — %s', len(chunk), data.get('comment'))
        else:
            all_ok = False
            logger.warning(
                'eSMS: bulk send failed for %s recipients — %s (errCode=%s)',
                len(chunk), data.get('comment'), data.get('errCode'),
            )

    result['success'] = all_ok and bool(result['sent_numbers'])
    return result
