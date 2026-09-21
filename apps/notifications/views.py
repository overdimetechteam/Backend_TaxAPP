from rest_framework import generics, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone

from .models import Notification, ScheduledMessage
from .serializers import NotificationSerializer, ScheduledMessageSerializer, ScheduledMessageCreateSerializer
from apps.clients.models import ClientProfile

CONSULTANT_ROLES = ('consultant', 'handling_person', 'admin', 'super_admin')


def _check_year_start_notifications(consultant):
    """
    Lazy check: for each client assessment year whose start date has passed
    and no notification sent yet, create a consultant notification.
    Called on every notification list fetch — idempotent via notification_sent flag.
    """
    from apps.clients.models import ClientAssessmentYear
    today = timezone.now().date()

    pending = ClientAssessmentYear.objects.filter(
        assigned_by=consultant,
        notification_sent=False,
        form_sent=False,
        tax_year__assessment_year_start__lte=today,
    ).select_related('client__client_profile', 'tax_year')

    for assignment in pending:
        try:
            profile = assignment.client.client_profile
            client_name = profile.full_name
            client_profile_id = profile.id
        except Exception:
            client_name = assignment.client.get_full_name() or assignment.client.email
            client_profile_id = None

        Notification.objects.create(
            recipient=consultant,
            title=f'Assessment Year Started — {client_name}',
            message=(
                f'The {assignment.tax_year.label} assessment year has started for '
                f'{client_name}. Send the tax return form to begin the filing process.'
            ),
            notification_type='action_required',
            related_client_id=client_profile_id,
            related_year_id=assignment.tax_year.id,
        )
        assignment.notification_sent = True
        assignment.save(update_fields=['notification_sent'])


class NotificationListView(generics.ListAPIView):
    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        if self.request.user.role in CONSULTANT_ROLES:
            _check_year_start_notifications(self.request.user)

        qs = Notification.objects.filter(recipient=self.request.user)
        unread_only = self.request.query_params.get('unread', None)
        if unread_only == 'true':
            qs = qs.filter(is_read=False)
        return qs


class MarkReadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk=None):
        if pk:
            notifications = Notification.objects.filter(id=pk, recipient=request.user)
        else:
            notifications = Notification.objects.filter(recipient=request.user, is_read=False)

        notifications.update(is_read=True, read_at=timezone.now())
        return Response({'message': 'Notifications marked as read.'})


class UnreadCountView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        count = Notification.objects.filter(recipient=request.user, is_read=False).count()
        return Response({'count': count})


class SendReminderView(APIView):
    """Consultant sends manual reminder to a client."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if request.user.role != 'consultant':
            return Response({'error': 'Permission denied.'}, status=status.HTTP_403_FORBIDDEN)

        client_profile_id = request.data.get('client_id')
        message = request.data.get('message', '')

        try:
            profile = ClientProfile.objects.get(id=client_profile_id, assigned_consultant=request.user)
        except ClientProfile.DoesNotExist:
            return Response({'error': 'Client not found.'}, status=status.HTTP_404_NOT_FOUND)

        Notification.objects.create(
            recipient=profile.user,
            title='Reminder from Your Tax Consultant',
            message=message or 'Please complete and submit your tax form at your earliest convenience.',
            notification_type='reminder',
        )

        return Response({'message': f'Reminder sent to {profile.full_name}.'})


class IsSuperAdminRole(IsAuthenticated):
    def has_permission(self, request, view):
        return super().has_permission(request, view) and request.user.role == 'super_admin'


class ScheduledMessageListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsSuperAdminRole]

    def get_queryset(self):
        return ScheduledMessage.objects.filter(created_by=self.request.user).prefetch_related(
            'recipients__client_profile__user'
        )

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return ScheduledMessageCreateSerializer
        return ScheduledMessageSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        message = serializer.save()
        return Response(ScheduledMessageSerializer(message).data, status=status.HTTP_201_CREATED)


class ScheduledMessageCancelView(APIView):
    permission_classes = [IsSuperAdminRole]

    def post(self, request, pk=None):
        try:
            message = ScheduledMessage.objects.get(pk=pk, created_by=request.user)
        except ScheduledMessage.DoesNotExist:
            return Response({'error': 'Message not found.'}, status=status.HTTP_404_NOT_FOUND)

        if message.status != 'pending':
            return Response({'error': 'Only pending messages can be cancelled.'}, status=status.HTTP_400_BAD_REQUEST)

        message.status = 'cancelled'
        message.save(update_fields=['status'])
        return Response(ScheduledMessageSerializer(message).data)
