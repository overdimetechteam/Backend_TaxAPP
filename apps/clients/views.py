from rest_framework import generics, status, filters
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django_filters.rest_framework import DjangoFilterBackend
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.conf import settings

from .models import ClientProfile, ClientAssessmentYear
from .serializers import ClientProfileSerializer, RegisterClientSerializer, ClientListSerializer
from apps.notifications.models import Notification

User = get_user_model()

CONSULTANT_ROLES = ('consultant', 'handling_person')
ADMIN_ROLES = (*CONSULTANT_ROLES, 'admin', 'super_admin')


class IsConsultant(IsAuthenticated):
    def has_permission(self, request, view):
        return super().has_permission(request, view) and request.user.role in CONSULTANT_ROLES


class IsSuperAdmin(IsAuthenticated):
    def has_permission(self, request, view):
        return super().has_permission(request, view) and request.user.role == 'super_admin'


class IsConsultantOrSuperAdmin(IsAuthenticated):
    def has_permission(self, request, view):
        return super().has_permission(request, view) and request.user.role in ADMIN_ROLES


class RegisterClientView(APIView):
    permission_classes = [IsConsultantOrSuperAdmin]

    def post(self, request):
        serializer = RegisterClientSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            user, profile = serializer.save()
            portal_url = settings.FRONTEND_URL
            notification = Notification.objects.create(
                recipient=user,
                title='Welcome to Tax Automation Portal',
                message=(
                    f'Your account has been created. Username: {user.username}. '
                    f'Log in at {portal_url} and change your password on first login.'
                ),
                notification_type='info',
            )
            return Response({
                'message': 'Client registered successfully.',
                'client_id': profile.id,
                'email': user.email,
                'username': user.username,
                # True = SMS sent, False = attempted but failed, None = no phone number on file
                'sms_sent': getattr(notification, '_sms_result', None),
            }, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ClientListView(generics.ListAPIView):
    serializer_class = ClientListSerializer
    permission_classes = [IsConsultantOrSuperAdmin]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['status']
    search_fields = ['full_name', 'tin', 'user__email']
    ordering_fields = ['full_name', 'created_at', 'status']

    def get_queryset(self):
        user = self.request.user
        if user.role in ('super_admin', 'admin'):
            consultant_id = self.request.query_params.get('consultant_id')
            qs = ClientProfile.objects.all().select_related('user', 'assigned_consultant')
            if consultant_id:
                qs = qs.filter(assigned_consultant_id=consultant_id)
        else:
            qs = ClientProfile.objects.filter(
                assigned_consultant=user
            ).select_related('user')

        is_active = self.request.query_params.get('is_active')
        if is_active is not None and is_active != '':
            qs = qs.filter(user__is_active=is_active.lower() in ('true', '1'))
        return qs


class ClientDetailView(generics.RetrieveUpdateAPIView):
    serializer_class = ClientProfileSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        client_id = self.kwargs.get('pk')
        user = self.request.user
        if user.role in ('super_admin', 'admin'):
            return ClientProfile.objects.get(id=client_id)
        if user.role in CONSULTANT_ROLES:
            return ClientProfile.objects.get(id=client_id, assigned_consultant=user)
        return user.client_profile

    def patch(self, request, *args, **kwargs):
        return self.partial_update(request, *args, **kwargs)

    def perform_update(self, serializer):
        # Only consultants/admins may flip a client's active status.
        if self.request.user.role not in ADMIN_ROLES:
            serializer.validated_data.get('user', {}).pop('is_active', None)
        serializer.save()


class ClientCredentialsView(APIView):
    """
    Consultant/admin view & change a client's login username and/or password.
    Passwords are hashed and can never be read back — only newly-set ones are
    returned once in the response, for the consultant to relay to the client.
    """
    permission_classes = [IsConsultantOrSuperAdmin]

    def _get_profile(self, pk, user):
        qs = ClientProfile.objects.select_related('user')
        if user.role in ('super_admin', 'admin'):
            return qs.get(id=pk)
        return qs.get(id=pk, assigned_consultant=user)

    def get(self, request, pk):
        try:
            profile = self._get_profile(pk, request.user)
        except ClientProfile.DoesNotExist:
            return Response({'error': 'Client not found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response({'username': profile.user.username})

    def patch(self, request, pk):
        try:
            profile = self._get_profile(pk, request.user)
        except ClientProfile.DoesNotExist:
            return Response({'error': 'Client not found.'}, status=status.HTTP_404_NOT_FOUND)

        user = profile.user
        new_username = (request.data.get('username') or '').strip()
        new_password = request.data.get('password') or ''

        if not new_username and not new_password:
            return Response({'error': 'Provide a username and/or password to update.'}, status=status.HTTP_400_BAD_REQUEST)

        errors = {}
        if new_username and new_username != user.username:
            if User.objects.filter(username=new_username).exclude(id=user.id).exists():
                errors['username'] = ['A user with this username already exists.']
        if new_password:
            try:
                validate_password(new_password, user=user)
            except ValidationError as exc:
                errors['password'] = list(exc.messages)
        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)

        response_data = {'message': 'Client credentials updated successfully.'}
        if new_username and new_username != user.username:
            user.username = new_username
            response_data['username'] = new_username
        if new_password:
            user.set_password(new_password)
            user.must_change_password = True
            response_data['password'] = new_password
        user.save()

        return Response(response_data)


class MyProfileView(generics.RetrieveUpdateAPIView):
    """Client's own profile edit. Clients may not change their own is_active flag."""
    serializer_class = ClientProfileSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return self.request.user.client_profile

    def perform_update(self, serializer):
        serializer.validated_data.get('user', {}).pop('is_active', None)
        serializer.save()


class ConsultantDashboardStatsView(APIView):
    permission_classes = [IsConsultant]

    def get(self, request):
        clients = ClientProfile.objects.filter(assigned_consultant=request.user)
        stats = {
            'total_clients': clients.count(),
            'not_started': clients.filter(status='not_started').count(),
            'in_progress': clients.filter(status='in_progress').count(),
            'pending_review': clients.filter(status='pending_review').count(),
            'awaiting_confirmation': clients.filter(status='awaiting_confirmation').count(),
            'archived': clients.filter(status='archived').count(),
        }
        return Response(stats)


class SuperAdminDashboardView(APIView):
    permission_classes = [IsSuperAdmin]

    def get(self, request):
        consultants = User.objects.filter(role__in=CONSULTANT_ROLES, is_active=True)
        all_clients = ClientProfile.objects.all()

        overall = {
            'total_consultants': consultants.count(),
            'total_clients': all_clients.count(),
            'not_started': all_clients.filter(status='not_started').count(),
            'in_progress': all_clients.filter(status='in_progress').count(),
            'pending_review': all_clients.filter(status='pending_review').count(),
            'awaiting_confirmation': all_clients.filter(status='awaiting_confirmation').count(),
            'archived': all_clients.filter(status='archived').count(),
        }

        consultant_stats = []
        for consultant in consultants:
            clients = ClientProfile.objects.filter(assigned_consultant=consultant)
            consultant_stats.append({
                'id': consultant.id,
                'name': consultant.get_full_name() or consultant.email,
                'email': consultant.email,
                'total_clients': clients.count(),
                'not_started': clients.filter(status='not_started').count(),
                'in_progress': clients.filter(status='in_progress').count(),
                'pending_review': clients.filter(status='pending_review').count(),
                'awaiting_confirmation': clients.filter(status='awaiting_confirmation').count(),
                'archived': clients.filter(status='archived').count(),
            })

        return Response({'overall': overall, 'consultants': consultant_stats})


class ConsultantListView(APIView):
    permission_classes = [IsConsultantOrSuperAdmin]

    def get(self, request):
        consultants = User.objects.filter(role__in=CONSULTANT_ROLES, is_active=True)
        data = []
        for c in consultants:
            client_count = ClientProfile.objects.filter(assigned_consultant=c).count()
            data.append({
                'id': c.id,
                'name': c.get_full_name() or c.email,
                'email': c.email,
                'phone': c.phone,
                'client_count': client_count,
            })
        return Response(data)


class CreateConsultantView(APIView):
    """Super admin creates a new consultant account."""
    permission_classes = [IsSuperAdmin]

    def post(self, request):
        first_name = request.data.get('first_name', '').strip()
        last_name  = request.data.get('last_name', '').strip()
        email      = request.data.get('email', '').strip()
        username   = request.data.get('username', '').strip()
        password   = request.data.get('password', '')
        phone      = request.data.get('phone', '').strip()

        errors = {}
        if not first_name: errors['first_name'] = ['First name is required.']
        if not last_name:  errors['last_name']  = ['Last name is required.']
        if not email:      errors['email']       = ['Email is required.']
        if not username:   errors['username']    = ['Username is required.']
        if not password:   errors['password']    = ['Password is required.']

        if not errors:
            if User.objects.filter(email=email).exists():
                errors['email'] = ['A user with this email already exists.']
            if User.objects.filter(username=username).exists():
                errors['username'] = ['A user with this username already exists.']

        if not errors:
            try:
                validate_password(password)
            except ValidationError as e:
                errors['password'] = list(e.messages)

        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)

        consultant = User.objects.create_user(
            email=email,
            username=username,
            first_name=first_name,
            last_name=last_name,
            password=password,
            role='consultant',
            phone=phone or None,
            must_change_password=True,
        )
        return Response({
            'id': consultant.id,
            'name': consultant.get_full_name(),
            'email': consultant.email,
            'username': consultant.username,
            'client_count': 0,
        }, status=status.HTTP_201_CREATED)


class AccountsDivisionListView(APIView):
    """Super admin: list all accounts division users."""
    permission_classes = [IsSuperAdmin]

    def get(self, request):
        users = User.objects.filter(role='accounts_division').order_by('first_name', 'last_name')
        return Response([
            {
                'id': u.id,
                'name': u.get_full_name() or u.email,
                'email': u.email,
                'username': u.username,
                'phone': u.phone,
                'is_active': u.is_active,
            }
            for u in users
        ])


class CreateAccountsDivisionView(APIView):
    """Super admin creates a new accounts division user account."""
    permission_classes = [IsSuperAdmin]

    def post(self, request):
        first_name = request.data.get('first_name', '').strip()
        last_name  = request.data.get('last_name', '').strip()
        email      = request.data.get('email', '').strip()
        username   = request.data.get('username', '').strip()
        password   = request.data.get('password', '')
        phone      = request.data.get('phone', '').strip()

        errors = {}
        if not first_name: errors['first_name'] = ['First name is required.']
        if not last_name:  errors['last_name']  = ['Last name is required.']
        if not email:      errors['email']       = ['Email is required.']
        if not username:   errors['username']    = ['Username is required.']
        if not password:   errors['password']    = ['Password is required.']

        if not errors:
            if User.objects.filter(email=email).exists():
                errors['email'] = ['A user with this email already exists.']
            if User.objects.filter(username=username).exists():
                errors['username'] = ['A user with this username already exists.']

        if not errors:
            try:
                validate_password(password)
            except ValidationError as e:
                errors['password'] = list(e.messages)

        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)

        acc_user = User.objects.create_user(
            email=email,
            username=username,
            first_name=first_name,
            last_name=last_name,
            password=password,
            role='accounts_division',
            phone=phone or None,
            must_change_password=True,
        )
        return Response({
            'id': acc_user.id,
            'name': acc_user.get_full_name(),
            'email': acc_user.email,
            'username': acc_user.username,
        }, status=status.HTTP_201_CREATED)


class AccountsDivisionDetailView(APIView):
    """Super admin: get details or deactivate an accounts division user."""
    permission_classes = [IsSuperAdmin]

    def _get_user(self, pk):
        try:
            return User.objects.get(pk=pk, role='accounts_division')
        except User.DoesNotExist:
            return None

    def get(self, request, pk):
        u = self._get_user(pk)
        if not u:
            return Response({'error': 'Accounts division user not found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response({
            'id': u.id,
            'name': u.get_full_name() or u.email,
            'email': u.email,
            'username': u.username,
            'phone': u.phone,
            'is_active': u.is_active,
        })

    def delete(self, request, pk):
        u = self._get_user(pk)
        if not u:
            return Response({'error': 'Accounts division user not found.'}, status=status.HTTP_404_NOT_FOUND)
        u.is_active = False
        u.save(update_fields=['is_active'])
        return Response({'message': 'Accounts division user deactivated successfully.'})


class ConsultantDetailView(APIView):
    """Super admin: get details or delete a consultant."""
    permission_classes = [IsSuperAdmin]

    def _get_consultant(self, pk):
        try:
            return User.objects.get(pk=pk, role__in=CONSULTANT_ROLES)
        except User.DoesNotExist:
            return None

    def get(self, request, pk):
        consultant = self._get_consultant(pk)
        if not consultant:
            return Response({'error': 'Consultant not found.'}, status=status.HTTP_404_NOT_FOUND)
        clients = ClientProfile.objects.filter(assigned_consultant=consultant).select_related('user')
        return Response({
            'id': consultant.id,
            'name': consultant.get_full_name() or consultant.email,
            'email': consultant.email,
            'username': consultant.username,
            'phone': consultant.phone,
            'is_active': consultant.is_active,
            'client_count': clients.count(),
            'clients': [
                {'id': cp.id, 'full_name': cp.full_name, 'email': cp.user.email, 'status': cp.status}
                for cp in clients
            ],
        })

    def patch(self, request, pk):
        consultant = self._get_consultant(pk)
        if not consultant:
            return Response({'error': 'Consultant not found.'}, status=status.HTTP_404_NOT_FOUND)

        if 'phone' not in request.data:
            return Response({'error': 'phone is required.'}, status=status.HTTP_400_BAD_REQUEST)

        consultant.phone = request.data.get('phone', '').strip() or None
        consultant.save(update_fields=['phone'])
        return Response({
            'id': consultant.id,
            'name': consultant.get_full_name() or consultant.email,
            'email': consultant.email,
            'username': consultant.username,
            'phone': consultant.phone,
            'is_active': consultant.is_active,
        })

    def delete(self, request, pk):
        consultant = self._get_consultant(pk)
        if not consultant:
            return Response({'error': 'Consultant not found.'}, status=status.HTTP_404_NOT_FOUND)

        client_count = ClientProfile.objects.filter(assigned_consultant=consultant).count()
        if client_count > 0:
            return Response(
                {
                    'error': f'Cannot remove this consultant — they have {client_count} client(s) assigned. '
                             f'Please transfer all clients to another consultant first.',
                    'client_count': client_count,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        consultant.is_active = False
        consultant.save(update_fields=['is_active'])
        return Response({'message': 'Consultant deactivated successfully.'})


class TransferClientsView(APIView):
    """Super admin: transfer one or more clients between consultants."""
    permission_classes = [IsSuperAdmin]

    def post(self, request):
        from_id    = request.data.get('from_consultant_id')
        to_id      = request.data.get('to_consultant_id')
        client_ids = request.data.get('client_ids', [])   # list of ClientProfile IDs; empty = transfer all
        transfer_all = request.data.get('transfer_all', False)

        if not from_id or not to_id:
            return Response({'error': 'from_consultant_id and to_consultant_id are required.'}, status=status.HTTP_400_BAD_REQUEST)
        if from_id == to_id:
            return Response({'error': 'Source and destination consultant must be different.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            from_consultant = User.objects.get(pk=from_id, role__in=CONSULTANT_ROLES)
        except User.DoesNotExist:
            return Response({'error': 'Source consultant not found.'}, status=status.HTTP_404_NOT_FOUND)
        try:
            to_consultant = User.objects.get(pk=to_id, role__in=CONSULTANT_ROLES, is_active=True)
        except User.DoesNotExist:
            return Response({'error': 'Destination consultant not found or inactive.'}, status=status.HTTP_404_NOT_FOUND)

        qs = ClientProfile.objects.filter(assigned_consultant=from_consultant)
        if not transfer_all and client_ids:
            qs = qs.filter(id__in=client_ids)

        count = qs.count()
        if count == 0:
            return Response({'error': 'No matching clients found to transfer.'}, status=status.HTTP_400_BAD_REQUEST)

        qs.update(assigned_consultant=to_consultant)

        # Also update ClientAssessmentYear.assigned_by for future notifications
        from apps.tax_forms.models import TaxYear
        from .models import ClientAssessmentYear
        client_user_ids = list(qs.values_list('user_id', flat=True))
        ClientAssessmentYear.objects.filter(
            client_id__in=client_user_ids, assigned_by=from_consultant
        ).update(assigned_by=to_consultant)

        return Response({
            'transferred': count,
            'to_consultant': to_consultant.get_full_name() or to_consultant.email,
        })


class ClientAssessmentYearsView(APIView):
    """GET/POST assessment years assigned to a client."""
    permission_classes = [IsConsultantOrSuperAdmin]

    def _get_profile(self, pk, user):
        try:
            if user.role == 'super_admin':
                return ClientProfile.objects.get(pk=pk)
            return ClientProfile.objects.get(pk=pk, assigned_consultant=user)
        except ClientProfile.DoesNotExist:
            return None

    def get(self, request, pk):
        from apps.tax_forms.models import TaxSubmission
        profile = self._get_profile(pk, request.user)
        if not profile:
            return Response({'error': 'Client not found.'}, status=status.HTTP_404_NOT_FOUND)

        assignments = ClientAssessmentYear.objects.filter(
            client=profile.user
        ).select_related('tax_year')

        data = []
        for a in assignments:
            submission = TaxSubmission.objects.filter(
                client=profile.user, tax_year=a.tax_year
            ).first()
            data.append({
                'id': a.id,
                'year_id': a.tax_year.id,
                'year_label': a.tax_year.label,
                'year': a.tax_year.year,
                'assessment_year_start': a.tax_year.assessment_year_start,
                'form_sent': a.form_sent,
                'notification_sent': a.notification_sent,
                'assigned_at': a.assigned_at,
                'submission_id': submission.id if submission else None,
                'submission_status': submission.status if submission else None,
            })
        return Response(data)

    def post(self, request, pk):
        from apps.tax_forms.models import TaxYear
        profile = self._get_profile(pk, request.user)
        if not profile:
            return Response({'error': 'Client not found.'}, status=status.HTTP_404_NOT_FOUND)

        year_ids = request.data.get('year_ids', [])
        if not year_ids:
            return Response({'error': 'year_ids is required.'}, status=status.HTTP_400_BAD_REQUEST)

        years = TaxYear.objects.filter(id__in=year_ids)
        assigned = []
        for year in years:
            obj, created = ClientAssessmentYear.objects.get_or_create(
                client=profile.user, tax_year=year,
                defaults={'assigned_by': request.user}
            )
            assigned.append({'year_id': year.id, 'year_label': year.label, 'created': created})

        return Response({'assigned': assigned}, status=status.HTTP_201_CREATED)
