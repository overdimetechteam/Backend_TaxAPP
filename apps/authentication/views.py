import logging

from rest_framework import status, generics
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.throttling import AnonRateThrottle
from rest_framework_simplejwt.tokens import RefreshToken
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import EmailMultiAlternatives
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode

from .serializers import (
    CustomTokenObtainPairSerializer,
    UserSerializer,
    ChangePasswordSerializer,
    ForgotPasswordSerializer,
    ResetPasswordConfirmSerializer,
)

User = get_user_model()
logger = logging.getLogger(__name__)

# Staff roles allowed to self-service a password reset. Clients are provisioned
# and re-credentialed by their consultant, so they're intentionally excluded.
RESETTABLE_ROLES = ('consultant', 'handling_person', 'super_admin')


class LoginView(APIView):
    """
    Accepts either email or username in the 'email' field.
    When multiple client accounts share the same email, the client must log in
    with their unique username instead.
    """
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        credential = (request.data.get('email') or '').strip()
        password   = request.data.get('password', '')

        if not credential or not password:
            return Response(
                {'detail': 'Email/username and password are required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if '@' in credential:
            users = User.objects.filter(email__iexact=credential, is_active=True)
            count = users.count()
            if count > 1:
                return Response(
                    {
                        'detail': (
                            'Multiple accounts share this email address. '
                            'Please log in using your username instead.'
                        ),
                        'use_username': True,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            elif count == 0:
                return Response(
                    {'detail': 'No active account found with this email.'},
                    status=status.HTTP_401_UNAUTHORIZED,
                )
            user_obj = users.first()
        else:
            try:
                user_obj = User.objects.get(username=credential, is_active=True)
            except User.DoesNotExist:
                return Response(
                    {'detail': 'No active account found with this username.'},
                    status=status.HTTP_401_UNAUTHORIZED,
                )

        if not user_obj.check_password(password):
            return Response(
                {'detail': 'Invalid credentials.'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        refresh = CustomTokenObtainPairSerializer.get_token(user_obj)

        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        ip = x_forwarded_for.split(',')[0] if x_forwarded_for else request.META.get('REMOTE_ADDR')
        user_obj.last_login_ip = ip
        user_obj.save(update_fields=['last_login_ip'])

        return Response({
            'access':               str(refresh.access_token),
            'refresh':              str(refresh),
            'role':                 user_obj.role,
            'email':                user_obj.email,
            'full_name':            user_obj.get_full_name(),
            'user_id':              user_obj.id,
            'must_change_password': user_obj.must_change_password,
        })


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            refresh_token = request.data.get('refresh')
            token = RefreshToken(refresh_token)
            token.blacklist()
            return Response({'message': 'Successfully logged out.'}, status=status.HTTP_200_OK)
        except Exception:
            return Response({'error': 'Invalid token.'}, status=status.HTTP_400_BAD_REQUEST)


class MeView(generics.RetrieveUpdateAPIView):
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return self.request.user


class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            request.user.set_password(serializer.validated_data['new_password'])
            request.user.must_change_password = False
            request.user.save()
            return Response({'message': 'Password changed successfully.'}, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class PasswordResetThrottle(AnonRateThrottle):
    scope = 'password_reset'


def _send_password_reset_email(user):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    frontend_url = getattr(settings, 'FRONTEND_URL', 'http://localhost:5173').rstrip('/')
    reset_link = f'{frontend_url}/reset-password?uid={uid}&token={token}'
    recipient_name = user.get_full_name().strip() or user.email.split('@')[0].capitalize()

    subject = '[DPR TMS] Reset your password'
    plain_body = (
        f'Dear {recipient_name},\n\n'
        f'We received a request to reset the password for your DPR Tax Management System account.\n'
        f'Use the link below to choose a new password. This link expires once used or after a short time.\n\n'
        f'{reset_link}\n\n'
        f"If you didn't request this, you can safely ignore this email.\n\n"
        f'— DPR Tax Management System'
    )
    html_body = (
        f'<p>Dear {recipient_name},</p>'
        f'<p>We received a request to reset the password for your DPR Tax Management System account. '
        f'Click the link below to choose a new password. This link expires once used or after a short time.</p>'
        f'<p><a href="{reset_link}">{reset_link}</a></p>'
        f"<p>If you didn't request this, you can safely ignore this email.</p>"
        f'<p>— DPR Tax Management System</p>'
    )

    try:
        msg = EmailMultiAlternatives(
            subject=subject,
            body=plain_body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[user.email],
        )
        msg.attach_alternative(html_body, 'text/html')
        msg.send(fail_silently=False)
        logger.info('Password reset email sent to %s', user.email)
    except Exception as exc:
        logger.warning('Failed to send password reset email to %s: %s', user.email, exc)


class ForgotPasswordView(APIView):
    """
    Request a password-reset email. Restricted to consultants/handling persons
    and super admins — clients are re-credentialed by their consultant instead.
    Always returns a generic 200 so the endpoint can't be used to enumerate
    which email addresses have accounts.
    """
    permission_classes = [AllowAny]
    throttle_classes = [PasswordResetThrottle]

    def post(self, request):
        generic_response = Response(
            {'detail': 'If an account with that email exists, a password reset link has been sent.'},
            status=status.HTTP_200_OK,
        )
        serializer = ForgotPasswordSerializer(data=request.data)
        if not serializer.is_valid():
            return generic_response

        users = User.objects.filter(
            email__iexact=serializer.validated_data['email'],
            is_active=True,
            role__in=RESETTABLE_ROLES,
        )
        for user in users:
            _send_password_reset_email(user)

        return generic_response


class ResetPasswordConfirmView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [PasswordResetThrottle]

    def post(self, request):
        serializer = ResetPasswordConfirmSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            uid = force_str(urlsafe_base64_decode(serializer.validated_data['uid']))
            user = User.objects.get(pk=uid, is_active=True, role__in=RESETTABLE_ROLES)
        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            return Response({'detail': 'Invalid or expired reset link.'}, status=status.HTTP_400_BAD_REQUEST)

        if not default_token_generator.check_token(user, serializer.validated_data['token']):
            return Response({'detail': 'Invalid or expired reset link.'}, status=status.HTTP_400_BAD_REQUEST)

        user.set_password(serializer.validated_data['new_password'])
        user.must_change_password = False
        user.save(update_fields=['password', 'must_change_password'])

        return Response({'detail': 'Password has been reset successfully.'}, status=status.HTTP_200_OK)
