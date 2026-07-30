from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.db import IntegrityError
from .models import ClientProfile

User = get_user_model()


class ClientProfileSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(source='user.email', read_only=True)
    username = serializers.CharField(source='user.username', read_only=True)
    user_id = serializers.IntegerField(source='user.id', read_only=True)
    consultant_name = serializers.SerializerMethodField()
    is_active = serializers.BooleanField(source='user.is_active')

    class Meta:
        model = ClientProfile
        fields = [
            'id', 'user_id', 'email', 'username', 'full_name', 'tin', 'pin', 'nic_passport',
            'telephone', 'mobile', 'address', 'status', 'is_active', 'notes',
            'assigned_consultant', 'consultant_name', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_consultant_name(self, obj):
        if obj.assigned_consultant:
            return obj.assigned_consultant.get_full_name() or obj.assigned_consultant.email
        return None

    def update(self, instance, validated_data):
        user_data = validated_data.pop('user', None)
        instance = super().update(instance, validated_data)
        if user_data and 'is_active' in user_data:
            instance.user.is_active = user_data['is_active']
            instance.user.save(update_fields=['is_active'])
        return instance


class RegisterClientSerializer(serializers.Serializer):
    email = serializers.EmailField()
    username = serializers.CharField(max_length=150)
    first_name = serializers.CharField(max_length=100)
    last_name = serializers.CharField(max_length=100)
    password = serializers.CharField(write_only=True, validators=[validate_password])
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True)
    tin = serializers.CharField(max_length=50)
    pin = serializers.CharField(max_length=50, required=False, allow_blank=True)
    nic_passport = serializers.CharField(max_length=50, required=False, allow_blank=True)
    telephone = serializers.CharField(max_length=20, required=False, allow_blank=True)
    mobile = serializers.CharField(max_length=20, required=False, allow_blank=True)
    address = serializers.CharField(required=False, allow_blank=True)
    consultant_id = serializers.IntegerField(required=False, allow_null=True)
    assessment_year_ids = serializers.ListField(
        child=serializers.IntegerField(), required=False, allow_empty=True
    )

    def validate_email(self, value):
        # Multiple clients can share the same email (e.g. husband and wife).
        # They distinguish themselves at login by using their unique username.
        return value

    def validate_username(self, value):
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("A client with this username already exists.")
        return value

    def validate_tin(self, value):
        tin = value.strip()
        if not tin:
            raise serializers.ValidationError("TIN is required.")
        # TIN is stored encrypted (non-deterministic), so duplicate-check by decrypting each profile.
        for profile in ClientProfile.objects.select_related('user').only('tin', 'user__username'):
            if profile.tin and profile.tin.strip() == tin:
                raise serializers.ValidationError(
                    f"A client with this TIN is already registered (username: {profile.user.username})."
                )
        return tin

    def validate_consultant_id(self, value):
        if value is not None:
            if not User.objects.filter(id=value, role__in=('consultant', 'handling_person')).exists():
                raise serializers.ValidationError("Invalid consultant selected.")
        return value

    def validate(self, attrs):
        if not attrs.get('consultant_id'):
            raise serializers.ValidationError({'consultant_id': 'A handler must be selected.'})
        return attrs

    def create(self, validated_data):
        from apps.clients.models import ClientAssessmentYear
        from apps.tax_forms.models import TaxYear

        request = self.context['request']
        consultant_id = validated_data.pop('consultant_id', None)
        assessment_year_ids = validated_data.pop('assessment_year_ids', [])

        if consultant_id:
            consultant = User.objects.get(id=consultant_id)
        else:
            consultant = request.user

        try:
            user = User.objects.create_user(
                email=validated_data['email'],
                username=validated_data['username'],
                first_name=validated_data['first_name'],
                last_name=validated_data['last_name'],
                password=validated_data['password'],
                phone=validated_data.get('phone', ''),
                role='client',
                must_change_password=True,
            )
        except IntegrityError:
            raise serializers.ValidationError({'username': 'A user with this username already exists.'})

        profile = ClientProfile.objects.create(
            user=user,
            assigned_consultant=consultant,
            full_name=f"{validated_data['first_name']} {validated_data['last_name']}",
            tin=validated_data.get('tin', '').strip(),
            pin=validated_data.get('pin', ''),
            nic_passport=validated_data.get('nic_passport', ''),
            telephone=validated_data.get('telephone', ''),
            mobile=validated_data.get('mobile', ''),
            address=validated_data.get('address', ''),
        )

        if assessment_year_ids:
            years = TaxYear.objects.filter(id__in=assessment_year_ids)
            for year in years:
                ClientAssessmentYear.objects.get_or_create(
                    client=user, tax_year=year,
                    defaults={'assigned_by': consultant}
                )

        return user, profile


class ClientListSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(source='user.email', read_only=True)
    is_active = serializers.BooleanField(source='user.is_active', read_only=True)
    current_submission_status = serializers.SerializerMethodField()
    consultant_name = serializers.SerializerMethodField()
    consultant_id = serializers.IntegerField(source='assigned_consultant.id', read_only=True)

    class Meta:
        model = ClientProfile
        fields = ['id', 'email', 'full_name', 'tin', 'status', 'is_active', 'current_submission_status',
                  'created_at', 'consultant_name', 'consultant_id']

    def get_current_submission_status(self, obj):
        latest = obj.user.tax_submissions.order_by('-created_at').first()
        if latest:
            return latest.status
        return None

    def get_consultant_name(self, obj):
        if obj.assigned_consultant:
            return obj.assigned_consultant.get_full_name() or obj.assigned_consultant.email
        return None
