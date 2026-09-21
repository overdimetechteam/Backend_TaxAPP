from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.authentication.models import CustomUser
from apps.clients.models import ClientProfile
from apps.notifications.serializers import ScheduledMessageCreateSerializer


class _FakeRequest:
    def __init__(self, user):
        self.user = user


class ScheduledMessageCreateSerializerTests(TestCase):
    def setUp(self):
        self.consultant = CustomUser.objects.create_user(
            email='consultant@test.lk', username='consultant1', password='x', role='consultant'
        )
        self.other_consultant = CustomUser.objects.create_user(
            email='other@test.lk', username='other1', password='x', role='consultant'
        )
        client_user = CustomUser.objects.create_user(email='cl@test.lk', username='cl1', password='x', role='client')
        self.profile = ClientProfile.objects.create(
            user=client_user, assigned_consultant=self.consultant, full_name='Test Client', mobile='711234567'
        )

    def _serializer(self, data, user=None):
        return ScheduledMessageCreateSerializer(data=data, context={'request': _FakeRequest(user or self.consultant)})

    def test_requires_at_least_one_channel(self):
        s = self._serializer({'client_ids': [self.profile.id], 'send_email': False, 'send_sms': False})
        self.assertFalse(s.is_valid())
        self.assertIn('non_field_errors', s.errors)

    def test_email_channel_requires_subject_and_body(self):
        s = self._serializer({'client_ids': [self.profile.id], 'send_email': True})
        self.assertFalse(s.is_valid())
        self.assertIn('non_field_errors', s.errors)

    def test_sms_channel_requires_body(self):
        s = self._serializer({'client_ids': [self.profile.id], 'send_sms': True})
        self.assertFalse(s.is_valid())
        self.assertIn('non_field_errors', s.errors)

    def test_rejects_client_ids_not_owned_by_consultant(self):
        s = self._serializer(
            {'client_ids': [self.profile.id], 'send_sms': True, 'sms_body': 'Hi'},
            user=self.other_consultant,
        )
        self.assertFalse(s.is_valid())
        self.assertIn('client_ids', s.errors)

    @patch('apps.notifications.scheduled_messages.send_sms')
    def test_valid_data_with_no_schedule_sends_immediately(self, mock_send_sms):
        mock_send_sms.return_value = True
        s = self._serializer({'client_ids': [self.profile.id], 'send_sms': True, 'sms_body': 'Hi'})
        self.assertTrue(s.is_valid(), s.errors)
        message = s.save()
        self.assertEqual(message.status, 'sent')
        mock_send_sms.assert_called_once()

    @patch('apps.notifications.scheduled_messages.send_sms')
    def test_valid_data_with_future_schedule_stays_pending(self, mock_send_sms):
        from django.utils import timezone
        from datetime import timedelta

        future = timezone.now() + timedelta(days=1)
        s = self._serializer({
            'client_ids': [self.profile.id], 'send_sms': True, 'sms_body': 'Hi', 'scheduled_for': future,
        })
        self.assertTrue(s.is_valid(), s.errors)
        message = s.save()
        self.assertEqual(message.status, 'pending')
        mock_send_sms.assert_not_called()

    def test_rejects_more_than_max_recipients(self):
        s = self._serializer({
            'client_ids': list(range(1, 302)), 'send_sms': True, 'sms_body': 'Hi',
        })
        self.assertFalse(s.is_valid())
        self.assertIn('client_ids', s.errors)
        self.assertIn('at most 300 clients', str(s.errors['client_ids'][0]))

    def test_accepts_exactly_max_recipients(self):
        # The cap itself must not be off by one; only the ids we own become recipients.
        s = self._serializer({
            'client_ids': [self.profile.id] + list(range(10000, 10299)),
            'send_sms': True, 'sms_body': 'Hi',
            'scheduled_for': timezone.now() + timedelta(days=1),
        })
        self.assertTrue(s.is_valid(), s.errors)

    def test_mixed_ownership_client_ids_only_creates_owned_recipients(self):
        """One owned client + one owned by another consultant: valid, but only ours is a recipient."""
        foreign_user = CustomUser.objects.create_user(
            email='foreign@test.lk', username='foreign1', password='x', role='client'
        )
        foreign_profile = ClientProfile.objects.create(
            user=foreign_user, assigned_consultant=self.other_consultant,
            full_name='Other Consultants Client', mobile='799999999',
        )

        s = self._serializer({
            'client_ids': [self.profile.id, foreign_profile.id],
            'send_sms': True, 'sms_body': 'Hi',
            'scheduled_for': timezone.now() + timedelta(days=1),
        })
        self.assertTrue(s.is_valid(), s.errors)
        message = s.save()

        recipients = list(message.recipients.all())
        self.assertEqual(len(recipients), 1)
        self.assertEqual(recipients[0].client_profile_id, self.profile.id)
        self.assertNotIn(
            foreign_profile.id,
            message.recipients.values_list('client_profile_id', flat=True),
        )

    def test_pending_channel_statuses_set_on_scheduled_recipients(self):
        s = self._serializer({
            'client_ids': [self.profile.id],
            'send_email': True, 'email_subject': 'S', 'email_body': 'B',
            'send_sms': True, 'sms_body': 'Hi',
            'scheduled_for': timezone.now() + timedelta(days=1),
        })
        self.assertTrue(s.is_valid(), s.errors)
        message = s.save()

        recipient = message.recipients.get()
        self.assertEqual(recipient.email_status, 'pending')
        self.assertEqual(recipient.sms_status, 'pending')

    def _create_client_profiles(self, count):
        profiles = []
        for i in range(count):
            user = CustomUser.objects.create_user(
                email=f'bulk{i}@test.lk', username=f'bulk{i}', password='x', role='client'
            )
            profiles.append(ClientProfile.objects.create(
                user=user, assigned_consultant=self.consultant, full_name=f'Bulk Client {i}', mobile='711234567'
            ))
        return profiles

    @patch('apps.notifications.scheduled_messages.send_sms')
    def test_send_now_above_sync_threshold_is_auto_queued_not_sent_inline(self, mock_send_sms):
        profiles = self._create_client_profiles(
            ScheduledMessageCreateSerializer.SYNC_SEND_THRESHOLD + 1
        )
        s = self._serializer({
            'client_ids': [p.id for p in profiles], 'send_sms': True, 'sms_body': 'Hi',
        })
        self.assertTrue(s.is_valid(), s.errors)
        message = s.save()
        self.assertEqual(message.status, 'pending')
        self.assertIsNotNone(message.scheduled_for)
        mock_send_sms.assert_not_called()

    @patch('apps.notifications.scheduled_messages.send_sms')
    def test_send_now_at_sync_threshold_still_sends_immediately(self, mock_send_sms):
        mock_send_sms.return_value = True
        profiles = self._create_client_profiles(ScheduledMessageCreateSerializer.SYNC_SEND_THRESHOLD)
        s = self._serializer({
            'client_ids': [p.id for p in profiles], 'send_sms': True, 'sms_body': 'Hi',
        })
        self.assertTrue(s.is_valid(), s.errors)
        message = s.save()
        self.assertEqual(message.status, 'sent')
        self.assertEqual(mock_send_sms.call_count, len(profiles))

    def test_unselected_channel_status_is_not_applicable(self):
        s = self._serializer({
            'client_ids': [self.profile.id], 'send_sms': True, 'sms_body': 'Hi',
            'scheduled_for': timezone.now() + timedelta(days=1),
        })
        self.assertTrue(s.is_valid(), s.errors)
        message = s.save()

        recipient = message.recipients.get()
        self.assertEqual(recipient.sms_status, 'pending')
        self.assertEqual(recipient.email_status, 'not_applicable')
