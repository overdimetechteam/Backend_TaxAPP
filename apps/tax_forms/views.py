from rest_framework import generics, parsers, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
from django.http import FileResponse, Http404
from django.conf import settings
from decimal import Decimal, ROUND_HALF_UP
import os
import shutil

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
from .serializers import (
    SubmissionEditLogSerializer,
    TaxYearSerializer, TaxSubmissionSerializer, TaxSubmissionListSerializer,
    LocalEmploymentIncomeSerializer, ForeignIncomeSerializer,
    TerminalBenefitSerializer, RentIncomeSerializer, InterestIncomeSerializer,
    DividendIncomeSerializer, SoleProprietorshipIncomeSerializer, OtherIncomeSerializer,
    TBSecuritiesIncomeSerializer,
    QualifyingPaymentsSerializer, SelfAssessmentPaymentSerializer,
    TaxCreditsSerializer, ImmovablePropertySerializer, MotorVehicleSerializer,
    BankBalanceSerializer, SharesStocksSerializer, CashInHandSerializer,
    LoansGivenSerializer, GoldSilverJewellerySerializer, BusinessPropertySerializer,
    OtherAssetSerializer, DisposalOfAssetSerializer, LiabilitySerializer,
    DeclarantDetailsSerializer,
    WHTCertificateSerializer, WHTCertificateUploadSerializer,
    PreviousYearAccessRequestSerializer, SystemSettingsSerializer,
    CashFlowStatementSerializer,
)
from .tax_calculator import calculate_full_tax
from .pdf_generator import generate_tax_submission_pdf
from apps.notifications.models import Notification
from apps.clients.models import ClientProfile
from django.contrib.auth import get_user_model

User = get_user_model()


# ── Permission helpers ────────────────────────────────────────────────────────

class IsConsultant(IsAuthenticated):
    """Allows consultant AND handling_person roles (plus admin and super_admin)."""
    def has_permission(self, request, view):
        return (
            super().has_permission(request, view)
            and request.user.role in ('consultant', 'handling_person', 'admin', 'super_admin')
        )


class IsAdmin(IsAuthenticated):
    def has_permission(self, request, view):
        return super().has_permission(request, view) and request.user.role == 'admin'


class IsAccountsDivision(IsAuthenticated):
    def has_permission(self, request, view):
        return (
            super().has_permission(request, view)
            and request.user.role in ('accounts_division', 'admin')
        )


class IsAdminOrConsultant(IsAuthenticated):
    def has_permission(self, request, view):
        return (
            super().has_permission(request, view)
            and request.user.role in ('admin', 'super_admin', 'consultant', 'handling_person')
        )


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _get_submission_for_user(submission_id, user):
    """Return TaxSubmission accessible by client or consultant (incl. orphaned clients)."""
    from django.db.models import Q
    try:
        if user.role in ('admin', 'super_admin'):
            return TaxSubmission.objects.get(id=submission_id)
        if user.role in ('consultant', 'handling_person'):
            assigned_ids = ClientProfile.objects.filter(
                assigned_consultant=user
            ).values_list('user_id', flat=True)
            profiled_ids = ClientProfile.objects.values_list('user_id', flat=True)
            orphan_ids = User.objects.filter(
                role='client'
            ).exclude(id__in=profiled_ids).values_list('id', flat=True)
            return TaxSubmission.objects.get(
                id=submission_id,
                client_id__in=[*assigned_ids, *orphan_ids]
            )
        return TaxSubmission.objects.get(id=submission_id, client=user)
    except TaxSubmission.DoesNotExist:
        return None


def _log_edit(submission, user, section, action, old_data=None, new_data=None, description=''):
    SubmissionEditLog.objects.create(
        submission=submission,
        edited_by=user,
        section=section,
        action=action,
        old_data=old_data,
        new_data=new_data,
        description=description or f'Consultant {action} {section}',
    )


class TaxYearListView(APIView):
    """
    Returns tax years visible to the requesting user:
    - Consultants / admins / super_admin: all years
    - Clients: active year + any year with an approved access request

    Only super_admin may create a new Year of Assessment (POST).
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        if user.role in ('consultant', 'handling_person', 'admin', 'super_admin', 'accounts_division'):
            years = TaxYear.objects.all()
        else:
            # Client: active year + approved previous years
            approved_year_ids = PreviousYearAccessRequest.objects.filter(
                client=user, status='approved'
            ).values_list('tax_year_id', flat=True)
            from django.db.models import Q
            years = TaxYear.objects.filter(Q(is_active=True) | Q(id__in=approved_year_ids))
        return Response(TaxYearSerializer(years, many=True).data)

    def post(self, request):
        if request.user.role != 'super_admin':
            return Response(
                {'error': 'Only Super Admin can create a new Year of Assessment.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = TaxYearSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class TaxSubmissionListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role in ('admin', 'super_admin'):
            submissions = TaxSubmission.objects.all()
        elif request.user.role in ('consultant', 'handling_person'):
            from django.db.models import Q
            # Clients assigned to this consultant
            assigned_client_ids = ClientProfile.objects.filter(
                assigned_consultant=request.user
            ).values_list('user_id', flat=True)
            # Clients with NO ClientProfile at all (orphaned — visible to all consultants)
            profiled_user_ids = ClientProfile.objects.values_list('user_id', flat=True)
            unassigned_client_ids = User.objects.filter(
                role='client'
            ).exclude(id__in=profiled_user_ids).values_list('id', flat=True)
            submissions = TaxSubmission.objects.filter(
                Q(client_id__in=assigned_client_ids) | Q(client_id__in=unassigned_client_ids)
            )
        else:
            submissions = TaxSubmission.objects.filter(client=request.user)

        serializer = TaxSubmissionListSerializer(submissions, many=True, context={'request': request})
        return Response(serializer.data)

    def post(self, request):
        # Client creates a new submission
        tax_year_id = request.data.get('tax_year')
        if not tax_year_id:
            return Response({'error': 'tax_year is required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            tax_year = TaxYear.objects.get(id=tax_year_id)
        except TaxYear.DoesNotExist:
            return Response({'error': 'Invalid tax year.'}, status=status.HTTP_404_NOT_FOUND)

        # For non-active years, client must have an approved access request
        if not tax_year.is_active and request.user.role == 'client':
            has_access = PreviousYearAccessRequest.objects.filter(
                client=request.user, tax_year=tax_year, status='approved'
            ).exists()
            if not has_access:
                return Response(
                    {'error': 'You do not have approved access to this tax year.'},
                    status=status.HTTP_403_FORBIDDEN,
                )

        if TaxSubmission.objects.filter(client=request.user, tax_year=tax_year).exists():
            return Response({'error': 'Submission for this tax year already exists.'}, status=status.HTTP_400_BAD_REQUEST)

        submission = TaxSubmission.objects.create(
            client=request.user,
            tax_year=tax_year,
            status='draft',
        )
        _prefill_wht_brought_forward(submission)
        _prefill_refund_brought_forward(submission)

        # Ensure client has a profile; auto-create linked to first consultant if missing
        profile = getattr(request.user, 'client_profile', None)
        if not profile:
            consultant = User.objects.filter(role='consultant').first()
            if consultant:
                profile = ClientProfile.objects.create(
                    user=request.user,
                    assigned_consultant=consultant,
                    full_name=request.user.get_full_name() or request.user.email,
                    status='in_progress',
                )
        if profile:
            profile.status = 'in_progress'
            profile.save(update_fields=['status'])

        # Seed declarant details from client profile so the Review step never blocks
        _seed_declarant_from_profile(submission)

        return Response(TaxSubmissionSerializer(submission, context={'request': request}).data, status=status.HTTP_201_CREATED)


class TaxSubmissionDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get_submission(self, pk, user):
        try:
            if user.role in ('admin', 'super_admin'):
                return TaxSubmission.objects.get(id=pk)
            if user.role in ('consultant', 'handling_person'):
                client_ids = ClientProfile.objects.filter(
                    assigned_consultant=user
                ).values_list('user_id', flat=True)
                return TaxSubmission.objects.get(id=pk, client_id__in=client_ids)
            return TaxSubmission.objects.get(id=pk, client=user)
        except TaxSubmission.DoesNotExist:
            return None

    def get(self, request, pk):
        submission = self.get_submission(pk, request.user)
        if not submission:
            return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        # Back-fill declarant details for existing submissions that were created before
        # the auto-seed logic was added (first-time clients have no previous submission).
        if not hasattr(submission, 'declarant_details'):
            _seed_declarant_from_profile(submission)
        return Response(TaxSubmissionSerializer(submission, context={'request': request}).data)

    def patch(self, request, pk):
        submission = self.get_submission(pk, request.user)
        if not submission:
            return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        # Consultants and admins can update certain fields
        if request.user.role in ('consultant', 'handling_person', 'admin', 'super_admin'):
            allowed_fields = ['consultant_notes', 'info_request_message', 'status']
            data = {k: v for k, v in request.data.items() if k in allowed_fields}
            for field, value in data.items():
                setattr(submission, field, value)
            if 'status' in data:
                if data['status'] == 'under_review':
                    submission.reviewed_by = request.user
                    submission.reviewed_at = timezone.now()
            submission.save()
        return Response(TaxSubmissionSerializer(submission, context={'request': request}).data)


class SubmitTaxFormView(APIView):
    """Client submits the completed form."""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        try:
            submission = TaxSubmission.objects.get(id=pk, client=request.user)
        except TaxSubmission.DoesNotExist:
            return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        if submission.status not in ['draft', 'info_requested']:
            return Response({'error': 'Cannot submit in current status.'}, status=status.HTTP_400_BAD_REQUEST)

        # Validate declarant details are present
        if not hasattr(submission, 'declarant_details'):
            return Response({'error': 'Declarant details are required before submission.'}, status=status.HTTP_400_BAD_REQUEST)

        submission.status = 'submitted'
        submission.submitted_at = timezone.now()
        submission.save()

        # Ensure client has a profile; auto-create linked to first consultant if missing
        profile = getattr(request.user, 'client_profile', None)
        if not profile:
            consultant = User.objects.filter(role='consultant').first()
            if consultant:
                profile = ClientProfile.objects.create(
                    user=request.user,
                    assigned_consultant=consultant,
                    full_name=request.user.get_full_name() or request.user.email,
                    status='pending_review',
                )
        if profile:
            profile.status = 'pending_review'
            profile.save(update_fields=['status'])

        # Notify consultant
        if profile and profile.assigned_consultant:
            Notification.objects.create(
                recipient=profile.assigned_consultant,
                title='New Tax Form Submission',
                message=f'{profile.full_name} has submitted their tax form for {submission.tax_year.label}. Please review.',
                notification_type='action_required',
                related_submission_id=submission.id,
            )

        return Response({'message': 'Form submitted successfully.', 'status': 'submitted'})


class RequestInfoView(APIView):
    """Consultant requests additional information from client."""
    permission_classes = [IsConsultant]

    def post(self, request, pk):
        submission = _get_submission_for_user(pk, request.user)
        if not submission:
            return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        message = request.data.get('message', '')
        if not message:
            return Response({'error': 'Message is required.'}, status=status.HTTP_400_BAD_REQUEST)

        submission.status = 'info_requested'
        submission.info_request_message = message
        submission.save()

        # Update client profile status
        profile = getattr(submission.client, 'client_profile', None)
        if profile:
            profile.status = 'in_progress'
            profile.save(update_fields=['status'])

        # Notify client
        Notification.objects.create(
            recipient=submission.client,
            title='Additional Information Required',
            message=f'Your tax consultant has requested additional information for {submission.tax_year.label}: {message}',
            notification_type='action_required',
            related_submission_id=submission.id,
        )

        return Response({'message': 'Information request sent.'})


class ConfirmCalculationView(APIView):
    """Consultant confirms calculation and notifies client."""
    permission_classes = [IsConsultant]

    def post(self, request, pk):
        try:
            if request.user.role in ('admin', 'super_admin'):
                submission = TaxSubmission.objects.get(id=pk)
            else:
                client_ids = ClientProfile.objects.filter(
                    assigned_consultant=request.user
                ).values_list('user_id', flat=True)
                submission = TaxSubmission.objects.get(id=pk, client_id__in=client_ids)
        except TaxSubmission.DoesNotExist:
            return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        # Calculate tax
        result = calculate_full_tax(submission)

        # Update submission with calculated values
        submission.total_assessable_income = result['total_assessable_income']
        submission.exempt_dividend_income = result['exempt_dividend_income']
        submission.total_qualifying_payments = result['total_qualifying_payments']
        submission.personal_relief = result['personal_relief']
        submission.rent_relief = result['rent_relief']
        submission.net_taxable_income = result['net_taxable_income']
        submission.gross_tax = result['gross_tax']
        submission.total_tax_credits = result['total_tax_credits']
        submission.wht_carried_forward = result['wht_carried_forward']
        submission.refund_carried_forward = result['refund_carried_forward']
        submission.capital_gain_tax = result['capital_gain_tax']
        submission.foreign_income_tax = result['foreign_income_tax']
        submission.net_tax_payable = result['net_tax_payable']
        submission.slab_breakdown = result['slab_breakdown']
        submission.status = 'awaiting_confirmation'
        submission.reviewed_by = request.user
        submission.reviewed_at = timezone.now()
        submission.save()

        # Update client profile
        profile = getattr(submission.client, 'client_profile', None)
        if profile:
            profile.status = 'awaiting_confirmation'
            profile.save(update_fields=['status'])

        # Notify client — do NOT reveal tax figures; payment must be settled first
        Notification.objects.create(
            recipient=submission.client,
            title='Payment Required — Tax Return Processed',
            message=f'Your tax return for {submission.tax_year.label} has been processed. A payment is required to complete the process. Please contact the office for payment details.',
            notification_type='action_required',
            related_submission_id=submission.id,
        )

        # Notify all Accounts Division users that payment will be required
        client_name = getattr(getattr(submission.client, 'client_profile', None), 'full_name', None) or submission.client.email
        accounts_users = User.objects.filter(role='accounts_division', is_active=True)
        for acc_user in accounts_users:
            Notification.objects.create(
                recipient=acc_user,
                title='Payment Pending — New Tax Submission',
                message=f'Tax submission for {client_name} ({submission.tax_year.label}) has been sent to the client for confirmation. Net Tax Payable: Rs. {result["net_tax_payable"]:,.2f}. Please prepare to confirm payment.',
                notification_type='action_required',
                related_submission_id=submission.id,
            )

        return Response({
            'message': 'Calculation confirmed. Client notified. Accounts Division alerted.',
            'calculation': result,
        })


class ClientConfirmView(APIView):
    """Client acknowledges payment notice and optionally uploads bank payment slip."""
    permission_classes = [IsAuthenticated]
    parser_classes = [parsers.MultiPartParser, parsers.FormParser, parsers.JSONParser]

    def post(self, request, pk):
        try:
            submission = TaxSubmission.objects.get(id=pk, client=request.user, status='awaiting_confirmation')
        except TaxSubmission.DoesNotExist:
            return Response({'error': 'Not found or not in awaiting confirmation status.'}, status=status.HTTP_404_NOT_FOUND)

        submission.status = 'confirmed'
        submission.confirmed_at = timezone.now()
        update_fields = ['status', 'confirmed_at', 'updated_at']

        slip_file = request.FILES.get('payment_slip')
        if slip_file:
            if submission.payment_slip:
                submission.payment_slip.delete(save=False)
            submission.payment_slip = slip_file
            update_fields.append('payment_slip')

        submission.save(update_fields=update_fields)

        profile = getattr(request.user, 'client_profile', None)

        # Notify consultant — do NOT archive yet; awaiting payment confirmation from Accounts Division
        if profile and profile.assigned_consultant:
            Notification.objects.create(
                recipient=profile.assigned_consultant,
                title='Client Confirmed — Awaiting Payment',
                message=f'{profile.full_name or request.user.email} has confirmed their tax submission for {submission.tax_year.label}. Waiting for Accounts Division to confirm payment before final submission.',
                notification_type='info',
                related_submission_id=submission.id,
            )

        return Response({'message': 'Tax submission confirmed. Awaiting payment confirmation from Accounts Division.'})


def _archive_submission(submission):
    """Archive documents following the required folder structure."""
    profile = getattr(submission.client, 'client_profile', None)
    client_name = profile.full_name if profile else submission.client.email
    year_label = submission.tax_year.label.replace('/', '-')

    archive_path = os.path.join(
        str(settings.ARCHIVE_ROOT),
        _sanitize_folder_name(client_name),
        year_label,
        'Final TAX Submission',
    )
    os.makedirs(archive_path, exist_ok=True)

    # Copy all documents
    for doc in submission.documents.all():
        if doc.file and os.path.exists(doc.file.path):
            dest = os.path.join(archive_path, os.path.basename(doc.file.name))
            shutil.copy2(doc.file.path, dest)

    # Generate and save PDF
    pdf_buffer = generate_tax_submission_pdf(submission, include_assets_liabilities=True)
    pdf_path = os.path.join(archive_path, f'Tax_Return_{year_label}.pdf')
    with open(pdf_path, 'wb') as f:
        f.write(pdf_buffer.read())

    submission.status = 'archived'
    submission.archived_at = timezone.now()
    submission.save(update_fields=['status', 'archived_at'])


def _sanitize_folder_name(name):
    """Remove characters not suitable for folder names."""
    import re
    return re.sub(r'[<>:"/\\|?*]', '_', name).strip()


class GeneratePDFView(APIView):
    """Generate and download the tax submission PDF."""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            if request.user.role in ('admin', 'super_admin'):
                submission = TaxSubmission.objects.get(id=pk)
            elif request.user.role in ('consultant', 'handling_person', 'accounts_division'):
                client_ids = ClientProfile.objects.filter(
                    assigned_consultant=request.user
                ).values_list('user_id', flat=True)
                submission = TaxSubmission.objects.get(id=pk, client_id__in=client_ids)
            else:
                submission = TaxSubmission.objects.get(id=pk, client=request.user)
                # Clients may only download after payment is confirmed by Accounts Division
                if submission.payment_status != 'paid':
                    return Response(
                        {'error': 'The PDF will be available once Accounts Division confirms your payment.'},
                        status=status.HTTP_403_FORBIDDEN,
                    )
        except TaxSubmission.DoesNotExist:
            raise Http404

        # Recalculate before export so the PDF always reflects the current
        # calculation logic and latest income data, rather than persisted
        # totals that may predate a calculation-rule change or a stale save.
        result = calculate_full_tax(submission)
        submission.total_assessable_income = result['total_assessable_income']
        submission.exempt_dividend_income = result['exempt_dividend_income']
        submission.total_qualifying_payments = result['total_qualifying_payments']
        submission.personal_relief = result['personal_relief']
        submission.rent_relief = result['rent_relief']
        submission.net_taxable_income = result['net_taxable_income']
        submission.gross_tax = result['gross_tax']
        submission.total_tax_credits = result['total_tax_credits']
        submission.wht_carried_forward = result['wht_carried_forward']
        submission.refund_carried_forward = result['refund_carried_forward']
        submission.capital_gain_tax = result['capital_gain_tax']
        submission.foreign_income_tax = result['foreign_income_tax']
        submission.net_tax_payable = result['net_tax_payable']
        submission.slab_breakdown = result['slab_breakdown']
        submission.save()

        pdf_buffer = generate_tax_submission_pdf(submission, include_assets_liabilities=True)
        filename = f"Tax_Return_{submission.tax_year.label.replace('/', '-')}_{submission.client.email}.pdf"

        return FileResponse(
            pdf_buffer,
            as_attachment=True,
            filename=filename,
            content_type='application/pdf',
        )


# ── Section-specific CRUD views ───────────────────────────────────────────────

class SectionUpdateView(APIView):
    """Generic view for updating specific form sections (client + consultant)."""
    permission_classes = [IsAuthenticated]
    model_class = None
    serializer_class = None
    section_name = ''

    def get_or_create_section(self, submission):
        obj, _ = self.model_class.objects.get_or_create(submission=submission)
        return obj

    def get(self, request, submission_id):
        submission = _get_submission_for_user(submission_id, request.user)
        if not submission:
            return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        try:
            obj = self.model_class.objects.get(submission=submission)
            return Response(self.serializer_class(obj).data)
        except self.model_class.DoesNotExist:
            return Response({})

    def post(self, request, submission_id):
        submission = _get_submission_for_user(submission_id, request.user)
        if not submission:
            return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        if submission.status == 'archived' and request.user.role != 'super_admin':
            return Response({'error': 'Cannot edit archived submission.'}, status=status.HTTP_400_BAD_REQUEST)
        if request.user.role not in ('consultant', 'super_admin') and submission.status not in ['draft', 'info_requested']:
            return Response({'error': 'Cannot edit in current status.'}, status=status.HTTP_400_BAD_REQUEST)

        obj = self.get_or_create_section(submission)
        old_data = dict(self.serializer_class(obj).data) if obj.pk else {}
        serializer = self.serializer_class(obj, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            if request.user.role in ('consultant', 'super_admin'):
                _log_edit(
                    submission, request.user,
                    section=self.section_name or self.model_class.__name__,
                    action='update',
                    old_data=old_data,
                    new_data=dict(serializer.data),
                )
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ForeignIncomeView(SectionUpdateView):
    model_class = ForeignIncome
    serializer_class = ForeignIncomeSerializer
    section_name = 'Foreign Income'


class TerminalBenefitView(SectionUpdateView):
    model_class = TerminalBenefit
    serializer_class = TerminalBenefitSerializer
    section_name = 'Terminal Benefit'


class RentIncomeView(SectionUpdateView):
    model_class = RentIncome
    serializer_class = RentIncomeSerializer
    section_name = 'Rent Income'


class InterestIncomeView(SectionUpdateView):
    model_class = InterestIncome
    serializer_class = InterestIncomeSerializer
    section_name = 'Interest Income'


class DividendIncomeView(SectionUpdateView):
    model_class = DividendIncome
    serializer_class = DividendIncomeSerializer
    section_name = 'Dividend Income'


class OtherIncomeView(SectionUpdateView):
    model_class = OtherIncome
    serializer_class = OtherIncomeSerializer
    section_name = 'Other Income'


class TBSecuritiesIncomeView(SectionUpdateView):
    model_class = TBSecuritiesIncome
    serializer_class = TBSecuritiesIncomeSerializer
    section_name = 'TB & Securities Income'


class CashFlowSuggestedView(APIView):
    """Aggregates source data to compute suggested R&P field values."""
    permission_classes = [IsAuthenticated]

    def get(self, request, submission_id):
        submission = _get_submission_for_user(submission_id, request.user)
        if not submission:
            return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        sub = TaxSubmission.objects.select_related(
            'foreign_income', 'rent_income', 'interest_income',
            'dividend_income', 'other_income', 'qualifying_payments', 'tax_credits',
            'cash_in_hand', 'tb_securities', 'gold_jewellery', 'tax_year',
        ).prefetch_related(
            'local_employments', 'self_assessment_payments', 'bank_balances',
            'immovable_properties', 'motor_vehicles', 'disposals', 'liabilities',
            'loans_given', 'shares_stocks', 'other_assets',
        ).get(pk=submission_id)

        year_start = sub.tax_year.assessment_year_start
        year_end = sub.tax_year.assessment_year_end

        def fv(val):
            return float(val) if val is not None else 0.0

        def amt(v):
            n = round(fv(v))
            return str(n) if n > 0 else ''

        # Previous tax year's submission for the same client — drives opening
        # balance carry-forward (cash in hand + bank balances).
        prev_sub = TaxSubmission.objects.filter(
            client_id=sub.client_id, tax_year__year=sub.tax_year.year - 1
        ).select_related('cash_in_hand').prefetch_related(
            'bank_balances', 'loans_given'
        ).order_by('-created_at').first()

        # Opening cash in hand — prior year's closing cash in hand
        prev_cih = getattr(prev_sub, 'cash_in_hand', None) if prev_sub else None
        opening_cash = fv(prev_cih.amount) if prev_cih else 0.0

        # Opening favourable bank balances — prior year's closing bank balances
        opening_banks = [
            {'bank_name': b.bank_name or '', 'account_no': b.account_no or '', 'amount': str(round(fv(b.balance)))}
            for b in (prev_sub.bank_balances.all() if prev_sub else [])
            if fv(b.balance) != 0
        ]

        # Employment — local (all employers) + all foreign income streams
        fi  = getattr(sub, 'foreign_income', None)
        emp = sum(fv(lei.amount) for lei in sub.local_employments.all())
        emp += (fv(fi.employment_service_fee) + fv(fi.foreign_business_income) + fv(fi.foreign_interest_income) + fv(fi.other_foreign_income)) if fi else 0.0

        # Interest — savings from the dedicated Interest Income section (Total Interest Received field)
        ii           = getattr(sub, 'interest_income', None)
        sav_interest = fv(ii.amount) if ii else 0.0

        # Rent
        ri = getattr(sub, 'rent_income', None)
        rent = fv(ri.gross_amount) if ri else 0.0

        # TB & Securities
        tbs = getattr(sub, 'tb_securities', None)
        tb_sec = fv(tbs.gross_amount) if tbs else 0.0

        # Dividend (taxable + exempt, net of final WHT — exempt_amount is gross)
        di = getattr(sub, 'dividend_income', None)
        dividend = (fv(di.amount) + fv(di.exempt_amount) - fv(di.final_wht)) if di else 0.0

        # Disposals by category
        disposals_by_cat = {}
        for d in sub.disposals.all():
            cat = d.category if d.category else 'other'
            disposals_by_cat[cat] = disposals_by_cat.get(cat, 0.0) + fv(d.sales_proceed)

        # New loans received during year
        new_bank_loans = sum(
            fv(l.original_amount) for l in sub.liabilities.all()
            if l.date_of_commencement and year_start <= l.date_of_commencement <= year_end
        )

        # Asset purchases during year
        land_purchases = sum(
            fv(p.cost) for p in sub.immovable_properties.all()
            if p.date_of_acquisition and year_start <= p.date_of_acquisition <= year_end
        )
        vehicle_purchases = sum(
            fv(v.cost_market_value) for v in sub.motor_vehicles.all()
            if v.date_of_acquisition and year_start <= v.date_of_acquisition <= year_end
        )
        shares_purchases = sum(
            fv(s.cost_market_value) for s in sub.shares_stocks.all()
            if s.date_of_acquisition and year_start <= s.date_of_acquisition <= year_end
        )
        other_asset_purchases = sum(
            fv(a.cost_value) for a in sub.other_assets.all()
            if a.date_of_acquisition and year_start <= a.date_of_acquisition <= year_end
        )

        # Loan repayments
        bank_repayments = sum(fv(l.amount_repaid_during_year) for l in sub.liabilities.all())

        # Loans given — given_during_year feeds payment_loans_given_others;
        # cash_received_from_debtors feeds receipt_debtor_received
        lg = getattr(sub, 'loans_given', None)
        loans_given_during_year = fv(lg.given_during_year)          if lg else 0.0
        debtor_received         = fv(lg.cash_received_from_debtors) if lg else 0.0

        # Tax credits
        tc = getattr(sub, 'tax_credits', None)
        wht_total = fv(tc.wht_rent_interest_service) if tc else 0.0
        apit = fv(tc.apit_on_salary) if tc else 0.0

        # Self-assessment tax payments
        sap_total = sum(fv(p.amount) for p in sub.self_assessment_payments.all())

        # Closing bank balances (from bank_balances.balance)
        closing_banks = [
            {'bank_name': b.bank_name or '', 'account_no': b.account_no or '', 'amount': str(round(fv(b.balance)))}
            for b in sub.bank_balances.all()
            if fv(b.balance) != 0
        ]

        # Cash in hand
        cih = getattr(sub, 'cash_in_hand', None)
        closing_cash = fv(cih.amount) if cih else 0.0

        # Other income → receipt_other_items
        oi = getattr(sub, 'other_income', None)
        receipt_other_items = []
        if oi and fv(oi.amount) > 0:
            receipt_other_items.append({'description': 'Other', 'amount': str(round(fv(oi.amount)))})

        # Donations → payment_other_items
        payment_other_items = []
        qp = getattr(sub, 'qualifying_payments', None)
        if qp:
            if fv(qp.donation_charitable) > 0:
                payment_other_items.append({'description': 'Charitable Donations', 'amount': str(round(fv(qp.donation_charitable)))})
            if fv(qp.donation_government) > 0:
                payment_other_items.append({'description': 'Government Donations', 'amount': str(round(fv(qp.donation_government)))})

        suggested = {
            'opening_cash_in_hand': amt(opening_cash),
            'opening_favourable_banks': opening_banks,
            'receipt_employment_income': amt(emp),
            'receipt_interest_savings': amt(sav_interest),
            'receipt_rent_income': amt(rent),
            'receipt_tb_securities': amt(tb_sec),
            'receipt_sale_shares': amt(disposals_by_cat.get('shares', 0)),
            'receipt_dividend_income': amt(dividend),
            'receipt_bank_loan': amt(new_bank_loans),
            'receipt_debtor_received': amt(debtor_received),
            'receipt_sale_land_building': amt(disposals_by_cat.get('land_building', 0)),
            'receipt_sale_motor_vehicle': amt(disposals_by_cat.get('motor_vehicle', 0)),
            'receipt_sale_other_assets': amt(disposals_by_cat.get('other', 0)),
            'receipt_other_items': receipt_other_items,
            'payment_purchase_land_building': amt(land_purchases),
            'payment_purchase_motor_vehicle': amt(vehicle_purchases),
            'payment_purchase_other_assets': amt(other_asset_purchases),
            'payment_repayment_bank_loan': amt(bank_repayments),
            'payment_wht': amt(wht_total),
            'payment_income_tax': amt(sap_total),
            'payment_apit': amt(apit),
            'payment_investment_shares': amt(shares_purchases),
            'payment_loans_given_others': amt(loans_given_during_year),
            'payment_other_items': payment_other_items,
            'closing_cash_in_hand': amt(closing_cash),
            'closing_favourable_banks': closing_banks,
        }

        return Response(suggested)


class QualifyingPaymentsView(SectionUpdateView):
    model_class = QualifyingPayments
    serializer_class = QualifyingPaymentsSerializer
    section_name = 'Qualifying Payments'


class TaxCreditsView(SectionUpdateView):
    model_class = TaxCredits
    serializer_class = TaxCreditsSerializer
    section_name = 'Tax Credits'


class CashInHandView(SectionUpdateView):
    model_class = CashInHand
    serializer_class = CashInHandSerializer
    section_name = 'Cash in Hand'


class GoldJewelleryView(SectionUpdateView):
    model_class = GoldSilverJewellery
    serializer_class = GoldSilverJewellerySerializer
    section_name = 'Gold & Jewellery'


class DeclarantDetailsView(SectionUpdateView):
    model_class = DeclarantDetails
    serializer_class = DeclarantDetailsSerializer
    section_name = 'Declarant Details'


class CashFlowStatementView(SectionUpdateView):
    model_class = CashFlowStatement
    serializer_class = CashFlowStatementSerializer
    section_name = 'Cash Flow Statement'


# ── List/Create/Delete for multi-row sections ─────────────────────────────────

class MultiRowSectionView(APIView):
    permission_classes = [IsAuthenticated]
    model_class = None
    serializer_class = None
    section_name = ''

    def get(self, request, submission_id):
        submission = _get_submission_for_user(submission_id, request.user)
        if not submission:
            return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        objects = self.model_class.objects.filter(submission=submission)
        return Response(self.serializer_class(objects, many=True).data)

    def post(self, request, submission_id):
        submission = _get_submission_for_user(submission_id, request.user)
        if not submission:
            return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        if submission.status == 'archived' and request.user.role != 'super_admin':
            return Response({'error': 'Cannot edit archived submission.'}, status=status.HTTP_400_BAD_REQUEST)
        if request.user.role not in ('consultant', 'handling_person', 'admin', 'super_admin') and submission.status not in ['draft', 'info_requested']:
            return Response({'error': 'Cannot edit in current status.'}, status=status.HTTP_400_BAD_REQUEST)

        serializer = self.serializer_class(data=request.data)
        if serializer.is_valid():
            obj = serializer.save(submission=submission)
            if request.user.role in ('consultant', 'handling_person', 'admin', 'super_admin'):
                _log_edit(
                    submission, request.user,
                    section=self.section_name or self.model_class.__name__,
                    action='add',
                    new_data=dict(serializer.data),
                    description=f'Added row to {self.section_name or self.model_class.__name__}',
                )
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class MultiRowItemView(APIView):
    permission_classes = [IsAuthenticated]
    model_class = None
    serializer_class = None
    section_name = ''

    def get_object(self, pk, user):
        try:
            obj = self.model_class.objects.select_related('submission').get(id=pk)
            # Admins can access any submission row
            if user.role in ('admin', 'super_admin'):
                return obj
            # Consultants can access assigned clients
            if user.role in ('consultant', 'handling_person'):
                allowed = _get_submission_for_user(obj.submission_id, user)
                if not allowed:
                    return None
            elif obj.submission.client != user:
                return None
            return obj
        except self.model_class.DoesNotExist:
            return None

    def patch(self, request, pk):
        obj = self.get_object(pk, request.user)
        if not obj:
            return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        if obj.submission.status == 'archived' and request.user.role != 'super_admin':
            return Response({'error': 'Cannot edit archived submission.'}, status=status.HTTP_400_BAD_REQUEST)
        old_data = dict(self.serializer_class(obj).data)
        serializer = self.serializer_class(obj, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            if request.user.role in ('consultant', 'handling_person', 'admin', 'super_admin'):
                _log_edit(
                    obj.submission, request.user,
                    section=self.section_name or self.model_class.__name__,
                    action='update',
                    old_data=old_data,
                    new_data=dict(serializer.data),
                )
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        obj = self.get_object(pk, request.user)
        if not obj:
            return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        if obj.submission.status == 'archived' and request.user.role != 'super_admin':
            return Response({'error': 'Cannot delete from archived submission.'}, status=status.HTTP_400_BAD_REQUEST)
        old_data = dict(self.serializer_class(obj).data)
        if request.user.role in ('consultant', 'super_admin'):
            _log_edit(
                obj.submission, request.user,
                section=self.section_name or self.model_class.__name__,
                action='delete',
                old_data=old_data,
                description=f'Deleted row from {self.section_name or self.model_class.__name__}',
            )
        obj.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# Local Employment — unlimited local employers per submission
class LocalEmploymentListView(MultiRowSectionView):
    model_class = LocalEmploymentIncome
    serializer_class = LocalEmploymentIncomeSerializer
    section_name = 'Local Employment Income'


class LocalEmploymentItemView(MultiRowItemView):
    model_class = LocalEmploymentIncome
    serializer_class = LocalEmploymentIncomeSerializer
    section_name = 'Local Employment Income'


# Sole Proprietorship
class SoleProprietorshipListView(MultiRowSectionView):
    model_class = SoleProprietorshipIncome
    serializer_class = SoleProprietorshipIncomeSerializer
    section_name = 'Sole Proprietorship'


class SoleProprietorshipItemView(MultiRowItemView):
    model_class = SoleProprietorshipIncome
    serializer_class = SoleProprietorshipIncomeSerializer
    section_name = 'Sole Proprietorship'


# Immovable Properties
class ImmovablePropertyListView(MultiRowSectionView):
    model_class = ImmovableProperty
    serializer_class = ImmovablePropertySerializer
    section_name = 'Immovable Property'


class ImmovablePropertyItemView(MultiRowItemView):
    model_class = ImmovableProperty
    serializer_class = ImmovablePropertySerializer
    section_name = 'Immovable Property'


# Motor Vehicles
class MotorVehicleListView(MultiRowSectionView):
    model_class = MotorVehicle
    serializer_class = MotorVehicleSerializer
    section_name = 'Motor Vehicles'


class MotorVehicleItemView(MultiRowItemView):
    model_class = MotorVehicle
    serializer_class = MotorVehicleSerializer
    section_name = 'Motor Vehicles'


# Bank Balances
class BankBalanceListView(MultiRowSectionView):
    model_class = BankBalance
    serializer_class = BankBalanceSerializer
    section_name = 'Bank Balances'


class BankBalanceItemView(MultiRowItemView):
    model_class = BankBalance
    serializer_class = BankBalanceSerializer
    section_name = 'Bank Balances'


# Shares
class SharesListView(MultiRowSectionView):
    model_class = SharesStocks
    serializer_class = SharesStocksSerializer
    section_name = 'Shares & Stocks'


class SharesItemView(MultiRowItemView):
    model_class = SharesStocks
    serializer_class = SharesStocksSerializer
    section_name = 'Shares & Stocks'


# Loans Given — single aggregate record per submission
class LoansGivenView(SectionUpdateView):
    model_class = LoansGiven
    serializer_class = LoansGivenSerializer
    section_name = 'Loans Given'


# Business Properties
class BusinessPropertyListView(MultiRowSectionView):
    model_class = BusinessProperty
    serializer_class = BusinessPropertySerializer
    section_name = 'Business Property'


class BusinessPropertyItemView(MultiRowItemView):
    model_class = BusinessProperty
    serializer_class = BusinessPropertySerializer
    section_name = 'Business Property'


# Other Assets
class OtherAssetListView(MultiRowSectionView):
    model_class = OtherAsset
    serializer_class = OtherAssetSerializer
    section_name = 'Other Assets'


class OtherAssetItemView(MultiRowItemView):
    model_class = OtherAsset
    serializer_class = OtherAssetSerializer
    section_name = 'Other Assets'


# Disposals
class DisposalListView(MultiRowSectionView):
    model_class = DisposalOfAsset
    serializer_class = DisposalOfAssetSerializer
    section_name = 'Disposal of Assets'


class DisposalItemView(MultiRowItemView):
    model_class = DisposalOfAsset
    serializer_class = DisposalOfAssetSerializer
    section_name = 'Disposal of Assets'


# Liabilities
class LiabilityListView(MultiRowSectionView):
    model_class = Liability
    serializer_class = LiabilitySerializer
    section_name = 'Liabilities'


class LiabilityItemView(MultiRowItemView):
    model_class = Liability
    serializer_class = LiabilitySerializer
    section_name = 'Liabilities'


# Self Assessment Payments
class SelfAssessmentListView(MultiRowSectionView):
    model_class = SelfAssessmentPayment
    serializer_class = SelfAssessmentPaymentSerializer
    section_name = 'Self-Assessment Payments'


class SelfAssessmentItemView(MultiRowItemView):
    model_class = SelfAssessmentPayment
    serializer_class = SelfAssessmentPaymentSerializer
    section_name = 'Self-Assessment Payments'


# Consultant update tax calculation view
class ConsultantUpdateCalculationView(APIView):
    permission_classes = [IsConsultant]

    def patch(self, request, pk):
        submission = _get_submission_for_user(pk, request.user)
        if not submission:
            return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        allowed = [
            'total_assessable_income', 'total_qualifying_payments', 'personal_relief',
            'rent_relief', 'net_taxable_income', 'gross_tax', 'total_tax_credits',
            'foreign_income_tax', 'net_tax_payable',
        ]
        changed = {f: request.data[f] for f in allowed if f in request.data}
        old_data = {f: str(getattr(submission, f, '')) for f in changed}
        for field, value in changed.items():
            setattr(submission, field, value)
        submission.save()

        if changed:
            _log_edit(
                submission, request.user,
                section='Tax Computation',
                action='update',
                old_data=old_data,
                new_data={f: str(request.data[f]) for f in changed},
                description=f'Updated tax computation fields: {", ".join(changed.keys())}',
            )
        return Response(TaxSubmissionSerializer(submission, context={'request': request}).data)


# ── Archive tree view ──────────────────────────────────────────────────────────

class ArchiveTreeView(APIView):
    """Returns archived submissions grouped by client → year for the consultant."""
    permission_classes = [IsConsultant]

    def get(self, request):
        from apps.documents.models import Document
        client_ids = ClientProfile.objects.filter(
            assigned_consultant=request.user
        ).values_list('user_id', flat=True)

        archived = TaxSubmission.objects.filter(
            client_id__in=client_ids,
            status='archived',
        ).select_related('client', 'client__client_profile', 'tax_year').order_by(
            'client__client_profile__full_name', '-tax_year__year'
        )

        tree = {}
        for sub in archived:
            profile = getattr(sub.client, 'client_profile', None)
            client_name = profile.full_name if profile else sub.client.email
            client_email = sub.client.email
            client_profile_id = profile.id if profile else None

            if client_name not in tree:
                tree[client_name] = {
                    'client_name': client_name,
                    'client_email': client_email,
                    'client_profile_id': client_profile_id,
                    'years': [],
                }

            docs = Document.objects.filter(submission=sub).order_by('section', 'uploaded_at')
            tree[client_name]['years'].append({
                'submission_id': sub.id,
                'tax_year_label': sub.tax_year.label,
                'archived_at': sub.archived_at,
                'submitted_at': sub.submitted_at,
                'net_tax_payable': str(sub.net_tax_payable),
                'total_assessable_income': str(sub.total_assessable_income),
                'net_taxable_income': str(sub.net_taxable_income),
                'documents': [
                    {
                        'id': doc.id,
                        'original_filename': doc.original_filename,
                        'file_url': request.build_absolute_uri(doc.file.url) if doc.file else None,
                        'document_type_display': doc.get_document_type_display(),
                        'section': doc.section,
                        'is_verified': doc.is_verified,
                        'file_size_kb': doc.file_size_kb,
                    }
                    for doc in docs
                ],
            })

        return Response(list(tree.values()))


# ── Edit log view ──────────────────────────────────────────────────────────────

class SubmissionEditLogsView(APIView):
    """Returns the edit audit log for a submission."""
    permission_classes = [IsConsultant]

    def get(self, request, pk):
        submission = _get_submission_for_user(pk, request.user)
        if not submission:
            return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        logs = submission.edit_logs.select_related('edited_by').all()
        return Response(SubmissionEditLogSerializer(logs, many=True).data)


# ── Live Tax Calculation (no save) ─────────────────────────────────────────────

class LiveCalculateView(APIView):
    """
    Returns the calculated tax values for a submission WITHOUT saving.
    Used by the consultant page to show a live preview before confirming.
    Fixes the 'Total Assessable Income shows as 0' issue (Change 5).
    """
    permission_classes = [IsConsultant]

    def get(self, request, pk):
        submission = _get_submission_for_user(pk, request.user)
        if not submission:
            return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        result = calculate_full_tax(submission)

        def _serialise(v):
            if hasattr(v, 'quantize'):   # Decimal
                return str(v)
            if isinstance(v, dict):
                return {kk: _serialise(vv) for kk, vv in v.items()}
            if isinstance(v, list):
                return [_serialise(i) for i in v]
            return v

        return Response({k: _serialise(v) for k, v in result.items()})


# ── Payment Status Update (Accounts Division) ─────────────────────────────────

class PaymentStatusView(APIView):
    """PATCH payment_status on a submission. Accounts Division only.
    Accepts multipart/form-data so a payment slip file can be attached."""
    permission_classes = [IsAccountsDivision]
    parser_classes = [parsers.MultiPartParser, parsers.FormParser, parsers.JSONParser]

    def patch(self, request, pk):
        try:
            submission = TaxSubmission.objects.get(id=pk)
        except TaxSubmission.DoesNotExist:
            return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        new_status = request.data.get('payment_status')
        if new_status not in ('pending', 'paid', 'overdue'):
            return Response(
                {'error': "payment_status must be 'pending', 'paid', or 'overdue'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        update_fields = ['payment_status', 'payment_updated_by', 'payment_updated_at']
        submission.payment_status = new_status
        submission.payment_updated_by = request.user
        submission.payment_updated_at = timezone.now()

        slip_file = request.FILES.get('payment_slip')
        if slip_file:
            if submission.payment_slip:
                submission.payment_slip.delete(save=False)
            submission.payment_slip = slip_file
            update_fields.append('payment_slip')

        submission.save(update_fields=update_fields)

        if new_status == 'paid':
            profile = getattr(submission.client, 'client_profile', None)
            client_name = getattr(profile, 'full_name', None) or submission.client.email
            if profile and profile.assigned_consultant:
                Notification.objects.create(
                    recipient=profile.assigned_consultant,
                    title='Payment Received — Ready to Submit Final Return',
                    message=f'Payment has been confirmed for {client_name} ({submission.tax_year.label}). You can now submit the final tax return to the client.',
                    notification_type='success',
                    related_submission_id=submission.id,
                )

        slip_url = None
        if submission.payment_slip:
            slip_url = request.build_absolute_uri(submission.payment_slip.url)

        return Response({
            'payment_status': submission.payment_status,
            'payment_updated_at': submission.payment_updated_at,
            'payment_slip_url': slip_url,
        })


# ── Accounts Division Queue ───────────────────────────────────────────────────

class AccountsQueueView(APIView):
    """Submissions in the accounts payment workflow — visible to Accounts Division.
    ?confirmed=1 returns payment-confirmed records; default returns the pending queue."""
    permission_classes = [IsAccountsDivision]

    def get(self, request):
        if request.query_params.get('confirmed') == '1':
            submissions = TaxSubmission.objects.filter(
                payment_status='paid',
            ).select_related('client', 'tax_year', 'reviewed_by').order_by('-payment_updated_at')
        else:
            submissions = TaxSubmission.objects.filter(
                status__in=['awaiting_confirmation', 'confirmed'],
                payment_status='pending',
            ).select_related('client', 'tax_year', 'reviewed_by')
        return Response(TaxSubmissionListSerializer(submissions, many=True, context={'request': request}).data)


# ── Final Submit (Consultant → after payment confirmed, sends to client for review) ──

class FinalSubmitView(APIView):
    """
    Consultant sends the final tax computation to the client for review after
    Accounts Division has confirmed payment. Client can now see full tax figures.
    """
    permission_classes = [IsConsultant]

    def post(self, request, pk):
        submission = _get_submission_for_user(pk, request.user)
        if not submission:
            return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        if submission.status != 'confirmed':
            return Response(
                {'error': 'Submission must be in confirmed status before final submission.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if submission.payment_status != 'paid':
            return Response(
                {
                    'error': 'Payment has not been received. Please wait for Accounts Division to confirm payment before submitting.',
                    'payment_not_received': True,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        submission.status = 'awaiting_client_review'
        submission.save(update_fields=['status', 'updated_at'])

        Notification.objects.create(
            recipient=submission.client,
            title='Tax Return Ready for Review',
            message=(
                f'Your tax return for {submission.tax_year.label} is ready for your review. '
                f'Total Assessable Income: Rs. {submission.total_assessable_income:,.2f} | '
                f'Net Tax Payable: Rs. {submission.net_tax_payable:,.2f}. '
                f'Please log in and confirm your tax computation.'
            ),
            notification_type='info',
            related_submission_id=submission.id,
        )

        return Response({'message': 'Tax computation sent to client for review.'})


# ── Client Final Confirm (Client confirms the tax computation) ────────────────

class ClientFinalConfirmView(APIView):
    """Client confirms the full tax computation after payment. Notifies consultant."""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        try:
            submission = TaxSubmission.objects.get(id=pk, client=request.user)
        except TaxSubmission.DoesNotExist:
            return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        if submission.status != 'awaiting_client_review':
            return Response({'error': 'No action required at this stage.'}, status=status.HTTP_400_BAD_REQUEST)

        submission.status = 'client_confirmed'
        submission.confirmed_at = timezone.now()
        submission.save(update_fields=['status', 'confirmed_at', 'updated_at'])

        consultant = getattr(getattr(submission.client, 'client_profile', None), 'assigned_consultant', None)
        if consultant:
            Notification.objects.create(
                recipient=consultant,
                title='Client Confirmed Tax Return',
                message=(
                    f'{submission.client.get_full_name()} has confirmed their tax return for '
                    f'{submission.tax_year.label}. You can now proceed with uploading your Income Tax '
                    f'Return to the RAMIS system.'
                ),
                notification_type='success',
                related_submission_id=submission.id,
            )

        return Response({'message': 'Tax return confirmed. Consultant has been notified.'})


# ── Archive Submission (Consultant marks complete & archives) ─────────────────

class ArchiveSubmissionView(APIView):
    """Consultant marks a client-confirmed submission as complete and archives documents.
    Requires both the IRD return and the IRD acknowledgement to be uploaded (multipart/form-data)."""
    permission_classes = [IsConsultant]

    def post(self, request, pk):
        submission = _get_submission_for_user(pk, request.user)
        if not submission:
            return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        if submission.status != 'client_confirmed':
            return Response(
                {'error': 'Submission must be client-confirmed before archiving.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ird_return_file = request.FILES.get('ird_return')
        acknowledgement_file = request.FILES.get('acknowledgement')
        if not ird_return_file or not acknowledgement_file:
            return Response(
                {
                    'error': 'Both the IRD return and the IRD acknowledgement must be uploaded before completing and archiving this submission.',
                    'requires_document': True,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Save the uploaded documents
        from apps.documents.models import Document
        Document.objects.create(
            submission=submission,
            uploaded_by=request.user,
            document_type='ird_return',
            section='general',
            file=ird_return_file,
            original_filename=ird_return_file.name,
            file_size=ird_return_file.size,
            description=request.data.get('ird_return_description', 'IRD Return'),
        )
        Document.objects.create(
            submission=submission,
            uploaded_by=request.user,
            document_type='ird_acknowledgement',
            section='general',
            file=acknowledgement_file,
            original_filename=acknowledgement_file.name,
            file_size=acknowledgement_file.size,
            description=request.data.get('acknowledgement_description', 'IRD Acknowledgement'),
        )

        _archive_submission(submission)

        submission.status = 'archived'
        submission.archived_at = timezone.now()
        submission.save()

        profile = getattr(submission.client, 'client_profile', None)
        if profile:
            profile.status = 'archived'
            profile.save(update_fields=['status'])

        year_range = f'{submission.tax_year.year}/{submission.tax_year.year + 1}'
        Notification.objects.create(
            recipient=submission.client,
            title='Tax Return Finalised & Archived',
            message=(
                'Dear Valued Client,\n'
                f'Your Income Tax Return for the Year of Assessment {year_range} has been successfully '
                'submitted to the Inland Revenue Department (IRD). Please log in to your DPR Tax Portal '
                'to view the submission details and related documents.\n'
                f'{settings.FRONTEND_URL}\n'
                'Thank you,'
            ),
            notification_type='success',
            related_submission_id=submission.id,
        )

        return Response({'message': 'Submission archived successfully.'})


# ── IRD Submission Upload ─────────────────────────────────────────────────────

class IRDSubmissionUploadView(APIView):
    """POST to upload the IRD return copy. Admin / Handling Person only."""
    permission_classes = [IsAdminOrConsultant]

    def post(self, request, pk):
        submission = _get_submission_for_user(pk, request.user)
        if not submission:
            return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        file = request.FILES.get('ird_submission_file')
        if not file:
            return Response({'error': 'No file uploaded.'}, status=status.HTTP_400_BAD_REQUEST)
        if not file.name.lower().endswith('.pdf'):
            return Response({'error': 'Only PDF files are accepted.'}, status=status.HTTP_400_BAD_REQUEST)

        submission.ird_submission_file = file
        submission.ird_submitted_at = timezone.now()
        submission.save(update_fields=['ird_submission_file', 'ird_submitted_at'])
        return Response({
            'message': 'IRD submission file uploaded.',
            'ird_submitted_at': submission.ird_submitted_at,
        })


# ── WHT Certificates ──────────────────────────────────────────────────────────

def _sync_wht_total(submission):
    """Sum WHT certificate amounts and store in TaxCredits.wht_rent_interest_service."""
    from django.db.models import Sum
    from decimal import Decimal
    total = submission.wht_certificates.aggregate(t=Sum('amount'))['t'] or Decimal('0.00')
    tc, _ = TaxCredits.objects.get_or_create(submission=submission)
    tc.wht_rent_interest_service = total
    tc.save(update_fields=['wht_rent_interest_service'])


class WHTCertificateListView(APIView):
    """List and upload WHT certificates for a submission."""
    permission_classes = [IsAuthenticated]

    def get(self, request, submission_id):
        submission = _get_submission_for_user(submission_id, request.user)
        if not submission:
            return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        certs = submission.wht_certificates.all()
        return Response(WHTCertificateSerializer(certs, many=True, context={'request': request}).data)

    def post(self, request, submission_id):
        submission = _get_submission_for_user(submission_id, request.user)
        if not submission:
            return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        if submission.status == 'archived' and request.user.role != 'super_admin':
            return Response({'error': 'Cannot edit archived submission.'}, status=status.HTTP_400_BAD_REQUEST)

        file = request.FILES.get('certificate_file')
        if file and not file.name.lower().endswith('.pdf'):
            return Response({'error': 'Only PDF files are accepted for WHT certificates.'}, status=status.HTTP_400_BAD_REQUEST)

        serializer = WHTCertificateUploadSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            cert = serializer.save(submission=submission)
            if file:
                cert.certificate_file = file
                cert.original_filename = file.name
                cert.save(update_fields=['certificate_file', 'original_filename'])
            _sync_wht_total(submission)
            return Response(
                WHTCertificateSerializer(cert, context={'request': request}).data,
                status=status.HTTP_201_CREATED,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class WHTCertificateItemView(APIView):
    """Patch / delete a WHT certificate."""
    permission_classes = [IsAuthenticated]

    def _get_cert_and_submission(self, pk, user):
        try:
            cert = WHTCertificate.objects.select_related('submission').get(id=pk)
        except WHTCertificate.DoesNotExist:
            return None, None
        sub = _get_submission_for_user(cert.submission_id, user)
        return cert, sub

    def patch(self, request, pk):
        cert, sub = self._get_cert_and_submission(pk, request.user)
        if not cert or not sub:
            return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        if sub.status == 'archived' and request.user.role != 'super_admin':
            return Response({'error': 'Cannot edit archived submission.'}, status=status.HTTP_400_BAD_REQUEST)
        serializer = WHTCertificateSerializer(cert, data=request.data, partial=True, context={'request': request})
        if serializer.is_valid():
            serializer.save()
            _sync_wht_total(sub)
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        cert, sub = self._get_cert_and_submission(pk, request.user)
        if not cert or not sub:
            return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        cert.delete()
        _sync_wht_total(sub)
        return Response(status=status.HTTP_204_NO_CONTENT)

    def get(self, request, pk):
        try:
            cert = WHTCertificate.objects.select_related('submission').get(id=pk)
        except WHTCertificate.DoesNotExist:
            return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        sub = _get_submission_for_user(cert.submission_id, request.user)
        if not sub:
            return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(WHTCertificateSerializer(cert, context={'request': request}).data)


# ── WHT Category Choices ──────────────────────────────────────────────────────

class WHTCategoryListView(APIView):
    """Returns available WHT categories for dropdown rendering (Change 14)."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        categories = [
            {'value': k, 'label': v}
            for k, v in WHTCertificate.WHT_CATEGORY_CHOICES
        ]
        return Response(categories)


# ── Previous Year Access Requests ─────────────────────────────────────────────

class AccessRequestListView(APIView):
    """
    Client: POST to request access to a previous year's data.
    Admin/Consultant: GET to list all pending requests.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role not in ('admin', 'consultant', 'handling_person'):
            return Response({'error': 'Forbidden.'}, status=status.HTTP_403_FORBIDDEN)
        requests_qs = PreviousYearAccessRequest.objects.select_related(
            'client', 'client__client_profile', 'tax_year', 'approved_by'
        ).order_by('-requested_at')
        if request.user.role in ('consultant', 'handling_person'):
            requests_qs = requests_qs.filter(
                client__client_profile__assigned_consultant=request.user
            )
        return Response(PreviousYearAccessRequestSerializer(requests_qs, many=True).data)

    def post(self, request):
        if request.user.role != 'client':
            return Response({'error': 'Only clients can request access.'}, status=status.HTTP_403_FORBIDDEN)
        tax_year_id = request.data.get('tax_year')
        if not tax_year_id:
            return Response({'error': 'tax_year is required.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            tax_year = TaxYear.objects.get(id=tax_year_id)
        except TaxYear.DoesNotExist:
            return Response({'error': 'Invalid tax year.'}, status=status.HTTP_404_NOT_FOUND)

        obj, created = PreviousYearAccessRequest.objects.get_or_create(
            client=request.user, tax_year=tax_year,
            defaults={'status': 'pending'},
        )
        if not created and obj.status == 'denied':
            # Allow re-request after denial
            obj.status = 'pending'
            obj.reviewed_at = None
            obj.save(update_fields=['status', 'reviewed_at'])

        # Notify assigned consultant about the view request
        if created or obj.status == 'pending':
            profile = getattr(request.user, 'client_profile', None)
            if profile and profile.assigned_consultant:
                client_display = profile.full_name or request.user.email
                Notification.objects.create(
                    recipient=profile.assigned_consultant,
                    title='Previous Year View Request',
                    message=(
                        f'{client_display} has requested access to view their '
                        f'{tax_year.label} submission. Please review and approve or deny.'
                    ),
                    notification_type='action_required',
                    related_client_id=profile.id,
                )

        return Response(
            PreviousYearAccessRequestSerializer(obj).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class AccessRequestDetailView(APIView):
    """Consultant/Admin: PATCH to approve or deny a client's view access request."""
    permission_classes = [IsConsultant]

    def patch(self, request, pk):
        try:
            access_req = PreviousYearAccessRequest.objects.select_related(
                'client__client_profile', 'tax_year'
            ).get(id=pk)
        except PreviousYearAccessRequest.DoesNotExist:
            return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        # Consultants can only act on their own clients' requests
        if request.user.role in ('consultant', 'handling_person'):
            profile = getattr(access_req.client, 'client_profile', None)
            if not profile or profile.assigned_consultant_id != request.user.id:
                return Response({'error': 'You can only manage requests for your own clients.'}, status=status.HTTP_403_FORBIDDEN)

        new_status = request.data.get('status')
        if new_status not in ('approved', 'denied'):
            return Response({'error': "status must be 'approved' or 'denied'."}, status=status.HTTP_400_BAD_REQUEST)

        access_req.status = new_status
        access_req.approved_by = request.user
        access_req.reviewed_at = timezone.now()
        access_req.notes = request.data.get('notes', access_req.notes)
        access_req.save()

        # Notify client
        if new_status == 'approved':
            msg = (
                f'Your request to view your {access_req.tax_year.label} submission has been approved. '
                f'You can now view it from your dashboard.'
            )
            notification_type = 'success'
        else:
            msg = f'Your request to view your {access_req.tax_year.label} submission has been denied.'
            notification_type = 'warning'

        Notification.objects.create(
            recipient=access_req.client,
            title='Access Request ' + ('Approved' if new_status == 'approved' else 'Denied'),
            message=msg,
            notification_type=notification_type,
        )

        # Notify assigned consultant so they can start the form for the client
        if new_status == 'approved':
            profile = getattr(access_req.client, 'client_profile', None)
            if profile and profile.assigned_consultant:
                Notification.objects.create(
                    recipient=profile.assigned_consultant,
                    title='Previous Year Access Approved',
                    message=(
                        f'{profile.full_name or access_req.client.email} has been granted access to '
                        f'{access_req.tax_year.label}. You can now manage their submission for that year.'
                    ),
                    notification_type='info',
                )

        return Response(PreviousYearAccessRequestSerializer(access_req).data)


# ── Dashboard Status Drill-Down (Change 4) ────────────────────────────────────

class DashboardStatusDetailView(APIView):
    """
    GET /api/tax/dashboard/status/<status_key>/
    Returns paginated list of submissions for the given status.
    Query params: ?handling_person_id=, ?year=, ?page=
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, status_key):
        from django.core.paginator import Paginator

        valid_statuses = dict(TaxSubmission.STATUS_CHOICES).keys()
        if status_key not in valid_statuses:
            return Response({'error': 'Invalid status.'}, status=status.HTTP_400_BAD_REQUEST)

        if request.user.role in ('admin',):
            qs = TaxSubmission.objects.filter(status=status_key)
        elif request.user.role in ('consultant', 'handling_person'):
            client_ids = ClientProfile.objects.filter(
                assigned_consultant=request.user
            ).values_list('user_id', flat=True)
            qs = TaxSubmission.objects.filter(status=status_key, client_id__in=client_ids)
        else:
            return Response({'error': 'Forbidden.'}, status=status.HTTP_403_FORBIDDEN)

        # Filters
        hp_id = request.query_params.get('handling_person_id')
        if hp_id:
            hp_client_ids = ClientProfile.objects.filter(
                assigned_consultant_id=hp_id
            ).values_list('user_id', flat=True)
            qs = qs.filter(client_id__in=hp_client_ids)
        year = request.query_params.get('year')
        if year:
            qs = qs.filter(tax_year__year=year)

        qs = qs.select_related('client', 'client__client_profile', 'tax_year')
        page_num = int(request.query_params.get('page', 1))
        paginator = Paginator(qs, 20)
        page = paginator.get_page(page_num)

        return Response({
            'count': paginator.count,
            'num_pages': paginator.num_pages,
            'page': page_num,
            'results': TaxSubmissionListSerializer(page.object_list, many=True, context={'request': request}).data,
        })


# ── Portfolio Dashboard (Change 7) ────────────────────────────────────────────

class PortfolioDashboardView(APIView):
    """
    GET /api/tax/dashboard/portfolio/
    Returns per-handling-person stats.
    Admin sees all; Handling Person sees only themselves.
    """
    permission_classes = [IsConsultant]

    def get(self, request):
        year = request.query_params.get('year')

        if request.user.role == 'admin':
            handling_persons = User.objects.filter(
                role__in=('consultant', 'handling_person')
            )
        else:
            handling_persons = User.objects.filter(id=request.user.id)

        result = []
        for hp in handling_persons:
            client_ids = ClientProfile.objects.filter(
                assigned_consultant=hp
            ).values_list('user_id', flat=True)
            sub_qs = TaxSubmission.objects.filter(client_id__in=client_ids)
            if year:
                sub_qs = sub_qs.filter(tax_year__year=year)

            total = sub_qs.count()
            breakdown = {}
            for st_key, _ in TaxSubmission.STATUS_CHOICES:
                breakdown[st_key] = sub_qs.filter(status=st_key).count()

            completed = sub_qs.filter(status__in=('confirmed', 'archived')).count()
            pct = round((completed / total * 100), 1) if total else 0.0

            result.append({
                'handling_person_id': hp.id,
                'handling_person_name': hp.get_full_name() or hp.email,
                'handling_person_email': hp.email,
                'total_clients': len(client_ids),
                'total_submissions': total,
                'status_breakdown': breakdown,
                'completion_percentage': pct,
            })
        return Response(result)


# ── Assessment Year Cyclic Process ───────────────────────────────────────────

def _seed_declarant_from_profile(submission):
    """
    Auto-create DeclarantDetails from the client's profile for first-time submissions.
    Only called when there is no previous submission to carry forward from.
    """
    user = submission.client
    profile = getattr(user, 'client_profile', None)
    if not profile:
        return
    DeclarantDetails.objects.get_or_create(
        submission=submission,
        defaults={
            'full_name':    profile.full_name or user.get_full_name() or user.email,
            'email':        user.email,
            'telephone':    profile.telephone or '',
            'mobile':       profile.mobile or '',
            'nic_passport': profile.nic_passport or '',
            'tin':          profile.tin or '',
            'pin':          profile.pin or '',
        },
    )


def _prefill_wht_brought_forward(new_submission):
    """
    Pulls last year's unused WHT credit (wht_carried_forward) from the client's most
    recent FINALIZED submission for tax_year - 1, and seeds it as this year's
    TaxCredits.wht_brought_forward. Only client_confirmed / archived prior-year
    submissions are trusted as a source — an in-progress prior year's figure could
    still change before it's finalized.
    """
    prev = TaxSubmission.objects.filter(
        client_id=new_submission.client_id,
        tax_year__year=new_submission.tax_year.year - 1,
        status__in=('client_confirmed', 'archived'),
    ).order_by('-created_at').first()

    if prev and prev.wht_carried_forward:
        TaxCredits.objects.update_or_create(
            submission=new_submission,
            defaults={'wht_brought_forward': prev.wht_carried_forward},
        )


REFUND_CARRY_FORWARD_RATE = Decimal('0.60')


def _prefill_refund_brought_forward(new_submission):
    """
    Pulls 60% of last year's unused refund entitlement (refund_carried_forward) from
    the client's most recent FINALIZED submission for tax_year - 1, and seeds it as
    this year's TaxCredits.refund_brought_forward. Only client_confirmed / archived
    prior-year submissions are trusted as a source — an in-progress prior year's
    figure could still change before it's finalized.
    """
    prev = TaxSubmission.objects.filter(
        client_id=new_submission.client_id,
        tax_year__year=new_submission.tax_year.year - 1,
        status__in=('client_confirmed', 'archived'),
    ).order_by('-created_at').first()

    if prev and prev.refund_carried_forward:
        claimable = (prev.refund_carried_forward * REFUND_CARRY_FORWARD_RATE).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
        TaxCredits.objects.update_or_create(
            submission=new_submission,
            defaults={'refund_brought_forward': claimable},
        )


def _prefill_from_previous(new_submission, prev_submission):
    """
    Copy carry-forward data from the previous year's submission to the new one.
    Assets: description/name/type/acquisition date/cost — NOT market value or balances.
    Liabilities: description/security/commencement date/original amount — NOT current balance.
    Declarant: all fields.
    """
    from decimal import Decimal

    # Declarant Details — full carry-forward
    if hasattr(prev_submission, 'declarant_details'):
        dd = prev_submission.declarant_details
        DeclarantDetails.objects.create(
            submission=new_submission,
            full_name=dd.full_name,
            telephone=dd.telephone,
            mobile=dd.mobile,
            email=dd.email,
            nic_passport=dd.nic_passport,
            tin=dd.tin,
            pin=dd.pin,
        )

    # Immovable Properties — name/date/cost, market_value reset to 0
    for a in prev_submission.immovable_properties.all():
        ImmovableProperty.objects.create(
            submission=new_submission,
            situation_of_property=a.situation_of_property,
            date_of_acquisition=a.date_of_acquisition,
            cost=a.cost,
            market_value=Decimal('0.00'),
            order=a.order,
        )

    # Motor Vehicles — all fields (no separate cost vs market value)
    for v in prev_submission.motor_vehicles.all():
        MotorVehicle.objects.create(
            submission=new_submission,
            description=v.description,
            registration_no=v.registration_no,
            date_of_acquisition=v.date_of_acquisition,
            cost_market_value=v.cost_market_value,
            order=v.order,
        )

    # Bank Balances — bank name + account no; amounts reset to 0 (change each year)
    for b in prev_submission.bank_balances.all():
        BankBalance.objects.create(
            submission=new_submission,
            bank_name=b.bank_name,
            account_no=b.account_no,
            amount_invested=Decimal('0.00'),
            interest=Decimal('0.00'),
            balance=Decimal('0.00'),
            order=b.order,
        )

    # Shares — description/count/date/cost; net_dividend reset (earned each year)
    for s in prev_submission.shares_stocks.all():
        SharesStocks.objects.create(
            submission=new_submission,
            description=s.description,
            no_of_shares=s.no_of_shares,
            date_of_acquisition=s.date_of_acquisition,
            cost_market_value=s.cost_market_value,
            net_dividend_income=Decimal('0.00'),
            order=s.order,
        )

    # Gold/Jewellery — description + value carried forward
    if hasattr(prev_submission, 'gold_jewellery'):
        g = prev_submission.gold_jewellery
        GoldSilverJewellery.objects.create(
            submission=new_submission,
            description=g.description,
            value=g.value,
        )

    # Loans Given — carry closing balance forward as next year's opening balance
    prev_lg = getattr(prev_submission, 'loans_given', None)
    if prev_lg:
        LoansGiven.objects.create(
            submission=new_submission,
            opening_balance=prev_lg.amount,
        )

    # Business Properties — name only; account balances reset (change each year)
    for b in prev_submission.business_properties.all():
        BusinessProperty.objects.create(
            submission=new_submission,
            name_of_business=b.name_of_business,
            current_account_balance=Decimal('0.00'),
            capital_account_balance=Decimal('0.00'),
            order=b.order,
        )

    # Other Assets — carry forward
    for o in prev_submission.other_assets.all():
        OtherAsset.objects.create(
            submission=new_submission,
            description=o.description,
            acquisition_type=o.acquisition_type,
            date_of_acquisition=o.date_of_acquisition,
            cost_value=o.cost_value,
            order=o.order,
        )

    # Liabilities — description/security/commencement date/original amount
    # amount_as_at_date and amount_repaid reset (updated each year)
    for lib in prev_submission.liabilities.all():
        Liability.objects.create(
            submission=new_submission,
            description=lib.description,
            security_on_liability=lib.security_on_liability,
            date_of_commencement=lib.date_of_commencement,
            original_amount=lib.original_amount,
            amount_as_at_date=Decimal('0.00'),
            amount_repaid_during_year=Decimal('0.00'),
            order=lib.order,
        )


class SendAssessmentFormView(APIView):
    """
    Consultant sends the assessment year form to a client.
    Creates a new TaxSubmission pre-filled with previous year's carry-forward data.
    """
    permission_classes = [IsConsultant]

    def post(self, request):
        from apps.clients.models import ClientAssessmentYear

        client_profile_id = request.data.get('client_profile_id')
        tax_year_id = request.data.get('tax_year_id')

        if not client_profile_id or not tax_year_id:
            return Response(
                {'error': 'client_profile_id and tax_year_id are required.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            profile = ClientProfile.objects.get(pk=client_profile_id)
            tax_year = TaxYear.objects.get(pk=tax_year_id)
        except (ClientProfile.DoesNotExist, TaxYear.DoesNotExist):
            return Response({'error': 'Invalid client or tax year.'}, status=status.HTTP_404_NOT_FOUND)

        if TaxSubmission.objects.filter(client=profile.user, tax_year=tax_year).exists():
            return Response(
                {'error': 'A submission already exists for this client and year.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Find most recent previous submission to pre-fill from
        prev = TaxSubmission.objects.filter(
            client=profile.user
        ).exclude(tax_year=tax_year).order_by('-tax_year__year').first()

        new_sub = TaxSubmission.objects.create(
            client=profile.user,
            tax_year=tax_year,
            status='draft',
        )
        _prefill_wht_brought_forward(new_sub)
        _prefill_refund_brought_forward(new_sub)

        if prev:
            _prefill_from_previous(new_sub, prev)
        else:
            _seed_declarant_from_profile(new_sub)

        # Mark form_sent on assignment record
        ClientAssessmentYear.objects.filter(
            client=profile.user, tax_year=tax_year
        ).update(form_sent=True)

        # Notify client
        Notification.objects.create(
            recipient=profile.user,
            title=f'Tax Return Form Ready — {tax_year.label}',
            message=(
                f'Your {tax_year.label} income tax return form is ready. '
                f'Please log in, review the pre-filled details, complete the form, and submit.'
            ),
            notification_type='action_required',
            related_submission_id=new_sub.id,
        )

        return Response({
            'submission_id': new_sub.id,
            'prefilled': prev is not None,
            'message': f'Form sent to {profile.full_name} for {tax_year.label}.',
        }, status=status.HTTP_201_CREATED)


class SendAssessmentFormsBulkView(APIView):
    """
    Consultant sends assessment forms for multiple years at once.
    Accepts client_profile_id + tax_year_ids[]. Returns per-year results.
    """
    permission_classes = [IsConsultant]

    def post(self, request):
        from apps.clients.models import ClientAssessmentYear

        client_profile_id = request.data.get('client_profile_id')
        tax_year_ids = request.data.get('tax_year_ids', [])

        if not client_profile_id or not tax_year_ids:
            return Response(
                {'error': 'client_profile_id and tax_year_ids are required.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            profile = ClientProfile.objects.get(pk=client_profile_id)
        except ClientProfile.DoesNotExist:
            return Response({'error': 'Client not found.'}, status=status.HTTP_404_NOT_FOUND)

        sent = []
        skipped = []
        errors = []

        for year_id in tax_year_ids:
            try:
                tax_year = TaxYear.objects.get(pk=year_id)
            except TaxYear.DoesNotExist:
                errors.append({'year_id': year_id, 'error': 'Tax year not found.'})
                continue

            if TaxSubmission.objects.filter(client=profile.user, tax_year=tax_year).exists():
                skipped.append({'year_id': year_id, 'year_label': tax_year.label, 'reason': 'Already exists'})
                continue

            prev = TaxSubmission.objects.filter(
                client=profile.user
            ).exclude(tax_year=tax_year).order_by('-tax_year__year').first()

            new_sub = TaxSubmission.objects.create(
                client=profile.user,
                tax_year=tax_year,
                status='draft',
            )
            _prefill_wht_brought_forward(new_sub)
            _prefill_refund_brought_forward(new_sub)

            if prev:
                _prefill_from_previous(new_sub, prev)
            else:
                _seed_declarant_from_profile(new_sub)

            ClientAssessmentYear.objects.filter(
                client=profile.user, tax_year=tax_year
            ).update(form_sent=True)

            Notification.objects.create(
                recipient=profile.user,
                title=f'Tax Return Form Ready — {tax_year.label}',
                message=(
                    f'Your {tax_year.label} income tax return form is ready. '
                    f'Please log in, review the pre-filled details, complete the form, and submit.'
                ),
                notification_type='action_required',
                related_submission_id=new_sub.id,
            )

            sent.append({
                'year_id': year_id,
                'year_label': tax_year.label,
                'submission_id': new_sub.id,
                'prefilled': prev is not None,
            })

        return Response({
            'sent': sent,
            'skipped': skipped,
            'errors': errors,
            'message': f'{len(sent)} form(s) sent to {profile.full_name}.',
        }, status=status.HTTP_201_CREATED)


# ── System Settings (Change 12) ───────────────────────────────────────────────

class SystemSettingsView(APIView):
    """GET system settings (public); PATCH is admin-only."""

    def get_permissions(self):
        if self.request.method == 'PATCH':
            return [IsAdmin()]
        return [IsAuthenticated()]

    def get(self, request):
        obj = SystemSettings.get()
        return Response(SystemSettingsSerializer(obj, context={'request': request}).data)

    def patch(self, request):
        obj = SystemSettings.get()
        serializer = SystemSettingsSerializer(obj, data=request.data, partial=True, context={'request': request})
        if serializer.is_valid():
            # Handle logo upload separately
            if 'company_logo' in request.FILES:
                obj.company_logo = request.FILES['company_logo']
            serializer.save()
            return Response(SystemSettingsSerializer(obj, context={'request': request}).data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
