from rest_framework import serializers
from decimal import Decimal
from .models import (
    TaxYear, TaxSubmission, LocalEmploymentIncome, ForeignIncome,
    TerminalBenefit, RentIncome, InterestIncome, DividendIncome,
    SoleProprietorshipIncome, OtherIncome, TBSecuritiesIncome, QualifyingPayments,
    SelfAssessmentPayment, TaxCredits, ImmovableProperty, MotorVehicle,
    BankBalance, SharesStocks, CashInHand, LoansGiven, GoldSilverJewellery,
    BusinessProperty, OtherAsset, DisposalOfAsset, Liability, DeclarantDetails,
    SubmissionEditLog, WHTCertificate, PreviousYearAccessRequest, SystemSettings,
    CashFlowStatement,
)


def _get_final_document_url(submission, request):
    """
    URL of the final tax return document the consultant uploaded when archiving
    this submission (Document.document_type == 'final_submission'). None if the
    submission hasn't been archived / no such document exists.
    """
    doc = submission.documents.filter(document_type='final_submission').order_by('-uploaded_at').first()
    if not doc or not doc.file:
        return None
    if request:
        return request.build_absolute_uri(doc.file.url)
    return doc.file.url


class TaxYearSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaxYear
        fields = '__all__'


class LocalEmploymentIncomeSerializer(serializers.ModelSerializer):
    class Meta:
        model = LocalEmploymentIncome
        exclude = ['submission']


class ForeignIncomeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ForeignIncome
        exclude = ['submission']


class TerminalBenefitSerializer(serializers.ModelSerializer):
    class Meta:
        model = TerminalBenefit
        exclude = ['submission']


class RentIncomeSerializer(serializers.ModelSerializer):
    class Meta:
        model = RentIncome
        exclude = ['submission']


class InterestIncomeSerializer(serializers.ModelSerializer):
    class Meta:
        model = InterestIncome
        exclude = ['submission']


class DividendIncomeSerializer(serializers.ModelSerializer):
    class Meta:
        model = DividendIncome
        exclude = ['submission']


class SoleProprietorshipIncomeSerializer(serializers.ModelSerializer):
    class Meta:
        model = SoleProprietorshipIncome
        exclude = ['submission']


class OtherIncomeSerializer(serializers.ModelSerializer):
    class Meta:
        model = OtherIncome
        exclude = ['submission']


class TBSecuritiesIncomeSerializer(serializers.ModelSerializer):
    class Meta:
        model = TBSecuritiesIncome
        exclude = ['submission']


class QualifyingPaymentsSerializer(serializers.ModelSerializer):
    class Meta:
        model = QualifyingPayments
        exclude = ['submission']


class SelfAssessmentPaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = SelfAssessmentPayment
        exclude = ['submission']


class TaxCreditsSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaxCredits
        exclude = ['submission']


class ImmovablePropertySerializer(serializers.ModelSerializer):
    class Meta:
        model = ImmovableProperty
        exclude = ['submission']


class MotorVehicleSerializer(serializers.ModelSerializer):
    class Meta:
        model = MotorVehicle
        exclude = ['submission']


class BankBalanceSerializer(serializers.ModelSerializer):
    class Meta:
        model = BankBalance
        exclude = ['submission']


class SharesStocksSerializer(serializers.ModelSerializer):
    class Meta:
        model = SharesStocks
        exclude = ['submission']


class CashInHandSerializer(serializers.ModelSerializer):
    class Meta:
        model = CashInHand
        exclude = ['submission']


class LoansGivenSerializer(serializers.ModelSerializer):
    class Meta:
        model = LoansGiven
        fields = ['id', 'opening_balance', 'given_during_year', 'cash_received_from_debtors', 'amount']


class GoldSilverJewellerySerializer(serializers.ModelSerializer):
    class Meta:
        model = GoldSilverJewellery
        exclude = ['submission']


class BusinessPropertySerializer(serializers.ModelSerializer):
    class Meta:
        model = BusinessProperty
        exclude = ['submission']


class OtherAssetSerializer(serializers.ModelSerializer):
    class Meta:
        model = OtherAsset
        exclude = ['submission']


class DisposalOfAssetSerializer(serializers.ModelSerializer):
    class Meta:
        model = DisposalOfAsset
        exclude = ['submission']


class LiabilitySerializer(serializers.ModelSerializer):
    class Meta:
        model = Liability
        exclude = ['submission']


class DeclarantDetailsSerializer(serializers.ModelSerializer):
    # All declarant fields are mandatory in the client-facing form even though the
    # model allows blank/null (kept nullable to avoid a destructive migration for
    # older records that pre-date this requirement).
    telephone = serializers.CharField(required=True, allow_blank=False)
    mobile = serializers.CharField(required=True, allow_blank=False)
    tin = serializers.CharField(required=True, allow_blank=False)
    pin = serializers.CharField(required=True, allow_blank=False)

    class Meta:
        model = DeclarantDetails
        exclude = ['submission']


class CashFlowStatementSerializer(serializers.ModelSerializer):
    # EncryptedJSONField extends models.TextField, so DRF auto-generates a
    # CharField for these — explicitly declare them as list fields instead.
    opening_favourable_banks  = serializers.ListField(child=serializers.DictField(), required=False, default=list)
    opening_overdraft_banks   = serializers.ListField(child=serializers.DictField(), required=False, default=list)
    receipt_other_items       = serializers.ListField(child=serializers.DictField(), required=False, default=list)
    payment_other_items       = serializers.ListField(child=serializers.DictField(), required=False, default=list)
    closing_favourable_banks  = serializers.ListField(child=serializers.DictField(), required=False, default=list)
    closing_overdraft_banks   = serializers.ListField(child=serializers.DictField(), required=False, default=list)

    class Meta:
        model = CashFlowStatement
        exclude = ['submission']


# ── WHT Certificates ──────────────────────────────────────────────────────────

class WHTCertificateSerializer(serializers.ModelSerializer):
    category_display = serializers.CharField(source='get_category_display', read_only=True)
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = WHTCertificate
        exclude = ['submission']

    def get_file_url(self, obj):
        if obj.certificate_file:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.certificate_file.url)
            return obj.certificate_file.url
        return None


class WHTCertificateUploadSerializer(serializers.ModelSerializer):
    class Meta:
        model = WHTCertificate
        fields = ['category', 'amount', 'notes']

    def validate(self, data):
        request = self.context.get('request')
        if request and 'certificate_file' in request.FILES:
            f = request.FILES['certificate_file']
            if not f.name.lower().endswith('.pdf'):
                raise serializers.ValidationError('Only PDF files are accepted for WHT certificates.')
        return data


# ── Previous Year Access Requests ─────────────────────────────────────────────

class PreviousYearAccessRequestSerializer(serializers.ModelSerializer):
    client_email = serializers.EmailField(source='client.email', read_only=True)
    client_name = serializers.SerializerMethodField()
    tax_year_label = serializers.CharField(source='tax_year.label', read_only=True)
    approved_by_name = serializers.SerializerMethodField()

    class Meta:
        model = PreviousYearAccessRequest
        fields = [
            'id', 'client', 'client_email', 'client_name', 'tax_year', 'tax_year_label',
            'requested_at', 'status', 'approved_by', 'approved_by_name',
            'reviewed_at', 'notes',
        ]
        read_only_fields = ['client', 'requested_at', 'approved_by', 'reviewed_at']

    def get_client_name(self, obj):
        profile = getattr(obj.client, 'client_profile', None)
        if profile:
            return profile.full_name
        return obj.client.get_full_name() or obj.client.email

    def get_approved_by_name(self, obj):
        if obj.approved_by:
            return obj.approved_by.get_full_name() or obj.approved_by.email
        return None


# ── System Settings ───────────────────────────────────────────────────────────

class SystemSettingsSerializer(serializers.ModelSerializer):
    logo_url = serializers.SerializerMethodField()

    class Meta:
        model = SystemSettings
        fields = ['company_name', 'company_tagline', 'company_logo', 'logo_url', 'footer_text', 'updated_at']
        read_only_fields = ['updated_at']

    def get_logo_url(self, obj):
        if obj.company_logo:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.company_logo.url)
            return obj.company_logo.url
        return None


# ── Full Submission Serializer ────────────────────────────────────────────────

class TaxSubmissionSerializer(serializers.ModelSerializer):
    local_employments = LocalEmploymentIncomeSerializer(many=True, read_only=True)
    foreign_income = ForeignIncomeSerializer(read_only=True)
    terminal_benefit = TerminalBenefitSerializer(read_only=True)
    rent_income = RentIncomeSerializer(read_only=True)
    interest_income = InterestIncomeSerializer(read_only=True)
    dividend_income = DividendIncomeSerializer(read_only=True)
    sole_proprietorships = SoleProprietorshipIncomeSerializer(many=True, read_only=True)
    other_income = OtherIncomeSerializer(read_only=True)
    tb_securities = TBSecuritiesIncomeSerializer(read_only=True)
    qualifying_payments = QualifyingPaymentsSerializer(read_only=True)
    self_assessment_payments = SelfAssessmentPaymentSerializer(many=True, read_only=True)
    tax_credits = TaxCreditsSerializer(read_only=True)
    immovable_properties = ImmovablePropertySerializer(many=True, read_only=True)
    motor_vehicles = MotorVehicleSerializer(many=True, read_only=True)
    bank_balances = BankBalanceSerializer(many=True, read_only=True)
    shares_stocks = SharesStocksSerializer(many=True, read_only=True)
    cash_in_hand = CashInHandSerializer(read_only=True)
    loans_given = LoansGivenSerializer(read_only=True)
    gold_jewellery = GoldSilverJewellerySerializer(read_only=True)
    business_properties = BusinessPropertySerializer(many=True, read_only=True)
    other_assets = OtherAssetSerializer(many=True, read_only=True)
    disposals = DisposalOfAssetSerializer(many=True, read_only=True)
    liabilities = LiabilitySerializer(many=True, read_only=True)
    declarant_details = DeclarantDetailsSerializer(read_only=True)
    wht_certificates = WHTCertificateSerializer(many=True, read_only=True)
    cash_flow = CashFlowStatementSerializer(read_only=True)
    tax_year_label        = serializers.CharField(source='tax_year.label',                read_only=True)
    assessment_year_start = serializers.DateField(source='tax_year.assessment_year_start', read_only=True)
    assessment_year_end   = serializers.DateField(source='tax_year.assessment_year_end',   read_only=True)
    client_name = serializers.SerializerMethodField()
    client_email = serializers.EmailField(source='client.email', read_only=True)
    payment_status_display = serializers.CharField(source='get_payment_status_display', read_only=True)
    payment_slip_url = serializers.SerializerMethodField()
    final_document_url = serializers.SerializerMethodField()
    consultant_name = serializers.SerializerMethodField()
    consultant_email = serializers.SerializerMethodField()
    consultant_phone = serializers.SerializerMethodField()

    class Meta:
        model = TaxSubmission
        fields = '__all__'

    def get_client_name(self, obj):
        profile = getattr(obj.client, 'client_profile', None)
        if profile:
            return profile.full_name
        return obj.client.get_full_name() or obj.client.email

    def get_consultant_name(self, obj):
        if not obj.reviewed_by:
            return None
        return obj.reviewed_by.get_full_name() or obj.reviewed_by.email

    def get_consultant_email(self, obj):
        return obj.reviewed_by.email if obj.reviewed_by else None

    def get_consultant_phone(self, obj):
        return obj.reviewed_by.phone if obj.reviewed_by else None

    def get_payment_slip_url(self, obj):
        if not obj.payment_slip:
            return None
        request = self.context.get('request')
        if request:
            return request.build_absolute_uri(obj.payment_slip.url)
        return obj.payment_slip.url

    def get_final_document_url(self, obj):
        return _get_final_document_url(obj, self.context.get('request'))


class TaxSubmissionListSerializer(serializers.ModelSerializer):
    tax_year_label = serializers.CharField(source='tax_year.label', read_only=True)
    client_name = serializers.SerializerMethodField()
    client_email = serializers.EmailField(source='client.email', read_only=True)
    payment_status_display = serializers.CharField(source='get_payment_status_display', read_only=True)
    payment_slip_url = serializers.SerializerMethodField()
    final_document_url = serializers.SerializerMethodField()

    class Meta:
        model = TaxSubmission
        fields = [
            'id', 'client', 'client_name', 'client_email',
            'tax_year', 'tax_year_label', 'status',
            'total_assessable_income', 'net_taxable_income',
            'total_tax_credits', 'net_tax_payable',
            'payment_status', 'payment_status_display', 'payment_slip_url',
            'final_document_url',
            'payment_updated_at',
            'info_request_message',
            'created_at', 'submitted_at', 'confirmed_at',
        ]

    def get_client_name(self, obj):
        profile = getattr(obj.client, 'client_profile', None)
        if profile:
            return profile.full_name
        return obj.client.get_full_name() or obj.client.email

    def get_payment_slip_url(self, obj):
        if not obj.payment_slip:
            return None
        request = self.context.get('request')
        if request:
            return request.build_absolute_uri(obj.payment_slip.url)
        return obj.payment_slip.url

    def get_final_document_url(self, obj):
        return _get_final_document_url(obj, self.context.get('request'))


class SubmissionEditLogSerializer(serializers.ModelSerializer):
    edited_by_name = serializers.SerializerMethodField()

    class Meta:
        model = SubmissionEditLog
        fields = ['id', 'section', 'action', 'description', 'old_data', 'new_data', 'edited_by_name', 'edited_at']

    def get_edited_by_name(self, obj):
        if obj.edited_by:
            return obj.edited_by.get_full_name() or obj.edited_by.email
        return 'Unknown'
