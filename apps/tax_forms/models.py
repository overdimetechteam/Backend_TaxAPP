from django.db import models
from django.conf import settings
from django.core.validators import MinValueValidator
from decimal import Decimal
import os

from encrypted_fields import EncryptedCharField, EncryptedJSONField


def _ird_upload_path(instance, filename):
    return f'ird_submissions/{instance.id}/{filename}'


def _wht_cert_upload_path(instance, filename):
    return f'wht_certificates/{instance.submission_id}/{filename}'


def _logo_upload_path(instance, filename):
    return f'system/logo/{filename}'


class TaxYear(models.Model):
    year = models.IntegerField(unique=True)
    label = models.CharField(max_length=50)  # e.g., "Y/A 2025/2026"
    assessment_year_start = models.DateField()
    assessment_year_end = models.DateField()
    personal_relief = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('1800000.00'))
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'tax_years'
        ordering = ['-year']

    def __str__(self):
        return self.label


class TaxSubmission(models.Model):
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('info_requested', 'Information Requested'),
        ('under_review', 'Under Consultant Review'),
        ('calculation_done', 'Calculation Done'),
        ('awaiting_confirmation', 'Awaiting Client Confirmation'),
        ('confirmed', 'Client Confirmed'),
        ('awaiting_client_review', 'Awaiting Client Review'),
        ('client_confirmed', 'Client Confirmed (Final)'),
        ('archived', 'Archived'),
    ]

    client = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='tax_submissions'
    )
    tax_year = models.ForeignKey(TaxYear, on_delete=models.PROTECT, related_name='submissions')
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='draft')

    # Consultant fields
    consultant_notes = models.TextField(blank=True, null=True)
    info_request_message = models.TextField(blank=True, null=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='reviewed_submissions'
    )

    # Calculated tax fields (set by consultant/system)
    total_assessable_income = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    exempt_dividend_income = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    total_qualifying_payments = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    personal_relief = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('1800000.00'))
    rent_relief = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    # net_taxable_income kept for backward compat; display label = "Taxable Income"
    net_taxable_income = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    gross_tax = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    total_tax_credits = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    foreign_income_tax = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    net_tax_payable = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    # Unused WHT credit left over after this year's tax is fully offset — flows into
    # next year's TaxCredits.wht_brought_forward when that submission is created.
    wht_carried_forward = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    # Unused refund entitlement left over after this year's tax is fully offset by
    # non-WHT credits (APIT, Self-Assessment, Partnership Credit, Tax Refund Claim,
    # Refund Brought Forward) — the excess that would otherwise be silently lost.
    # 60% of this flows into next year's TaxCredits.refund_brought_forward when that
    # submission is created.
    refund_carried_forward = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    # Flat 15% tax on net capital gain from disposal of assets — added to
    # net_tax_payable last, never reduced by WHT/APIT/self-assessment/etc.
    capital_gain_tax = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    # Slab breakdown stored as JSON after each calculation
    slab_breakdown = models.JSONField(null=True, blank=True)

    # Payment tracking (Accounts Division)
    PAYMENT_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('paid', 'Paid'),
        ('overdue', 'Overdue'),
    ]
    payment_status = models.CharField(
        max_length=10, choices=PAYMENT_STATUS_CHOICES, default='pending'
    )
    payment_updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='payment_updates',
    )
    payment_updated_at = models.DateTimeField(null=True, blank=True)
    payment_slip = models.FileField(upload_to='payment_slips/%Y/%m/', null=True, blank=True)
    payment_reminder_3d_sent = models.BooleanField(default=False)
    payment_reminder_5d_sent = models.BooleanField(default=False)
    payment_reminder_10d_sent = models.BooleanField(default=False)

    # IRD submission tracking
    ird_submission_file = models.FileField(
        upload_to='ird_submissions/', null=True, blank=True
    )
    ird_submitted_at = models.DateTimeField(null=True, blank=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'tax_submissions'
        unique_together = ['client', 'tax_year']
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.client.email} - {self.tax_year.label} ({self.status})"


# ─── INCOME SECTION ──────────────────────────────────────────────────────────

class LocalEmploymentIncome(models.Model):
    # Up to 3 local employers per submission (Change: multiple employee incomes) — capped in the view, not here.
    submission = models.ForeignKey(TaxSubmission, on_delete=models.CASCADE, related_name='local_employments')
    amount = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    employer_name = EncryptedCharField(max_length=200, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)

    class Meta:
        db_table = 'local_employment_income'


class ForeignIncome(models.Model):
    submission = models.OneToOneField(TaxSubmission, on_delete=models.CASCADE, related_name='foreign_income')
    source_country = models.CharField(max_length=100, blank=True, null=True)
    foreign_employer_name = EncryptedCharField(max_length=200, blank=True, null=True,
                                                help_text='Name of the foreign employer / company paying the income')
    employment_service_fee = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    foreign_business_income = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    foreign_interest_income = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'),
                                                   help_text='Interest earned on foreign bank deposits / investments')
    other_foreign_income = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    foreign_tax_paid = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'),
                                           help_text='Foreign tax already paid — eligible as tax credit')
    treaty_rate = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True,
                                      help_text='Double-tax treaty rate (e.g. 0.15 for 15%). Leave blank for standard rate.')
    notes = models.TextField(blank=True, null=True)

    class Meta:
        db_table = 'foreign_income'

    @property
    def total(self):
        # Foreign Interest is exempt from tax — excluded from taxable foreign income.
        return (
            self.employment_service_fee + self.foreign_business_income +
            self.other_foreign_income
        )


class TerminalBenefit(models.Model):
    submission = models.OneToOneField(TaxSubmission, on_delete=models.CASCADE, related_name='terminal_benefit')
    amount = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    benefit_types = models.CharField(max_length=200, blank=True, null=True, help_text='e.g., EPF, ETF, Pension, Gratuity')
    notes = models.TextField(blank=True, null=True)

    class Meta:
        db_table = 'terminal_benefit'


class RentIncome(models.Model):
    submission = models.OneToOneField(TaxSubmission, on_delete=models.CASCADE, related_name='rent_income')
    gross_amount = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    wht_deducted = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    notes = models.TextField(blank=True, null=True)

    class Meta:
        db_table = 'rent_income'


class InterestIncome(models.Model):
    submission = models.OneToOneField(TaxSubmission, on_delete=models.CASCADE, related_name='interest_income')
    amount = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    wht_deducted = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    notes = models.TextField(blank=True, null=True)

    class Meta:
        db_table = 'interest_income'


class DividendIncome(models.Model):
    submission = models.OneToOneField(TaxSubmission, on_delete=models.CASCADE, related_name='dividend_income')
    # Taxable dividends (not from resident companies subject to final WHT)
    amount = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    # Gross dividends from resident companies subject to final WHT — exempt from tax,
    # excluded from assessable income entirely (the WHT below fully settles the liability).
    exempt_amount = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'),
                                        help_text='Gross dividends from resident companies subject to final WHT — exempt from tax')
    # Actual WHT deducted at source on the exempt dividend (from the dividend certificate) —
    # informational/disclosure only (Schedule 6A); never a credit against other tax.
    final_wht = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'),
                                    help_text='Final WHT deducted on exempt dividends, per the dividend certificate')
    notes = models.TextField(blank=True, null=True)

    class Meta:
        db_table = 'dividend_income'


class SoleProprietorshipIncome(models.Model):
    submission = models.ForeignKey(TaxSubmission, on_delete=models.CASCADE, related_name='sole_proprietorships')
    amount = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    business_name = EncryptedCharField(max_length=200, blank=True, null=True)
    wht_deducted = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), null=True, blank=True)
    notes = models.TextField(blank=True, null=True)

    class Meta:
        db_table = 'sole_proprietorship_income'


class OtherIncome(models.Model):
    submission = models.OneToOneField(TaxSubmission, on_delete=models.CASCADE, related_name='other_income')
    amount = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    description = models.TextField(blank=True, null=True)

    class Meta:
        db_table = 'other_income'


class TBSecuritiesIncome(models.Model):
    submission = models.OneToOneField(TaxSubmission, on_delete=models.CASCADE, related_name='tb_securities')
    gross_amount = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    wht_deducted = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    notes = models.TextField(blank=True, null=True)

    class Meta:
        db_table = 'tb_securities_income'


# ─── QUALIFYING PAYMENTS ─────────────────────────────────────────────────────

class QualifyingPayments(models.Model):
    submission = models.OneToOneField(TaxSubmission, on_delete=models.CASCADE, related_name='qualifying_payments')
    donation_charitable = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    donation_government = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    solar_panels_expenditure = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    notes = models.TextField(blank=True, null=True)

    SOLAR_MAX = Decimal('600000.00')

    class Meta:
        db_table = 'qualifying_payments'

    @property
    def solar_allowed(self):
        return min(self.solar_panels_expenditure, self.SOLAR_MAX)

    @property
    def total(self):
        return self.donation_charitable + self.donation_government + self.solar_allowed


class SelfAssessmentPayment(models.Model):
    submission = models.ForeignKey(TaxSubmission, on_delete=models.CASCADE, related_name='self_assessment_payments')
    installment_number = models.IntegerField(choices=[(1, '1st'), (2, '2nd'), (3, '3rd'), (4, '4th')])
    amount = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    payment_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True, null=True)

    class Meta:
        db_table = 'self_assessment_payments'
        unique_together = ['submission', 'installment_number']


class TaxCredits(models.Model):
    submission = models.OneToOneField(TaxSubmission, on_delete=models.CASCADE, related_name='tax_credits')
    apit_on_salary = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    wht_rent_interest_service = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    partnership_tax_credit = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    tax_refund_claim = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'),
                                           help_text='Refund claimed for tax withheld / paid in excess of the liability')
    wht_brought_forward = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'),
                                               help_text='Unused WHT credit carried forward from last Y/A (prefilled from last year\'s carry-forward; editable)')
    refund_brought_forward = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'),
                                                  help_text='60% of last Y/A\'s unused refund entitlement, claimable as a tax credit this year (prefilled from last year\'s carry-forward; editable)')
    notes = models.TextField(blank=True, null=True)

    class Meta:
        db_table = 'tax_credits'


# ─── ASSETS ──────────────────────────────────────────────────────────────────

class ImmovableProperty(models.Model):
    submission = models.ForeignKey(TaxSubmission, on_delete=models.CASCADE, related_name='immovable_properties')
    situation_of_property = EncryptedCharField(max_length=500, blank=True)
    date_of_acquisition = models.DateField(null=True, blank=True)
    cost = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    market_value = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    order = models.IntegerField(default=1)

    class Meta:
        db_table = 'immovable_properties'
        ordering = ['order']


class MotorVehicle(models.Model):
    submission = models.ForeignKey(TaxSubmission, on_delete=models.CASCADE, related_name='motor_vehicles')
    description = models.CharField(max_length=200, blank=True)
    registration_no = EncryptedCharField(max_length=50, blank=True)
    date_of_acquisition = models.DateField(null=True, blank=True)
    cost_market_value = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    order = models.IntegerField(default=1)

    class Meta:
        db_table = 'motor_vehicles'
        ordering = ['order']


class BankBalance(models.Model):
    submission = models.ForeignKey(TaxSubmission, on_delete=models.CASCADE, related_name='bank_balances')
    bank_name = EncryptedCharField(max_length=200, blank=True)
    account_no = EncryptedCharField(max_length=100, blank=True)
    amount_invested = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    interest = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    balance = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    order = models.IntegerField(default=1)

    class Meta:
        db_table = 'bank_balances'
        ordering = ['order']


class SharesStocks(models.Model):
    submission = models.ForeignKey(TaxSubmission, on_delete=models.CASCADE, related_name='shares_stocks')
    description = models.CharField(max_length=200, blank=True)
    no_of_shares = models.IntegerField(default=0)
    date_of_acquisition = models.DateField(null=True, blank=True)
    cost_market_value = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    net_dividend_income = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    order = models.IntegerField(default=1)

    class Meta:
        db_table = 'shares_stocks'
        ordering = ['order']


class CashInHand(models.Model):
    submission = models.OneToOneField(TaxSubmission, on_delete=models.CASCADE, related_name='cash_in_hand')
    amount = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))

    class Meta:
        db_table = 'cash_in_hand'


class LoansGiven(models.Model):
    """Aggregate single-record loans given & amount receivable as at 31 March."""
    submission = models.OneToOneField(TaxSubmission, on_delete=models.CASCADE, related_name='loans_given')
    opening_balance           = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    given_during_year         = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    cash_received_from_debtors = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    amount = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))  # closing balance

    class Meta:
        db_table = 'loans_given'


class GoldSilverJewellery(models.Model):
    submission = models.OneToOneField(TaxSubmission, on_delete=models.CASCADE, related_name='gold_jewellery')
    description = models.TextField(blank=True, null=True)
    value = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))

    class Meta:
        db_table = 'gold_silver_jewellery'


class BusinessProperty(models.Model):
    submission = models.ForeignKey(TaxSubmission, on_delete=models.CASCADE, related_name='business_properties')
    name_of_business = EncryptedCharField(max_length=200, blank=True)
    current_account_balance = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    capital_account_balance = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    order = models.IntegerField(default=1)

    class Meta:
        db_table = 'business_properties'
        ordering = ['order']


class OtherAsset(models.Model):
    ACQUISITION_TYPE = [
        ('purchase', 'Purchase'),
        ('gift', 'Gift'),
        ('exchange', 'Exchange'),
    ]
    submission = models.ForeignKey(TaxSubmission, on_delete=models.CASCADE, related_name='other_assets')
    description = models.CharField(max_length=300, blank=True)
    acquisition_type = models.CharField(max_length=20, choices=ACQUISITION_TYPE, default='purchase')
    date_of_acquisition = models.DateField(null=True, blank=True)
    cost_value = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    order = models.IntegerField(default=1)

    class Meta:
        db_table = 'other_assets'
        ordering = ['order']


class DisposalOfAsset(models.Model):
    CATEGORY_CHOICES = [
        ('land_building', 'Land / Building'),
        ('motor_vehicle', 'Motor Vehicle'),
        ('shares', 'Shares / Securities'),
        ('other', 'Other'),
    ]
    submission = models.ForeignKey(TaxSubmission, on_delete=models.CASCADE, related_name='disposals')
    description = models.CharField(max_length=300, blank=True)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='other')
    date_of_disposal = models.DateField(null=True, blank=True)
    sales_proceed = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    date_acquired = models.DateField(null=True, blank=True)
    cost = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    order = models.IntegerField(default=1)
    # True only for entries added via the Income section's "Capital Gain" table.
    # Entries added via the Assets section's "Disposal of Assets" table (section 10)
    # are reporting-only and never taxed as a capital gain.
    is_capital_gain = models.BooleanField(default=False)

    class Meta:
        db_table = 'disposal_of_assets'
        ordering = ['order']


# ─── LIABILITIES ─────────────────────────────────────────────────────────────

class Liability(models.Model):
    submission = models.ForeignKey(TaxSubmission, on_delete=models.CASCADE, related_name='liabilities')
    description = models.CharField(max_length=300, blank=True)
    security_on_liability = models.CharField(max_length=300, blank=True, null=True)
    date_of_commencement = models.DateField(null=True, blank=True)
    original_amount = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    amount_as_at_date = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    amount_repaid_during_year = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    order = models.IntegerField(default=1)

    class Meta:
        db_table = 'liabilities'
        ordering = ['order']


# ─── DECLARANT DETAILS ───────────────────────────────────────────────────────

class DeclarantDetails(models.Model):
    submission = models.OneToOneField(TaxSubmission, on_delete=models.CASCADE, related_name='declarant_details')
    full_name = EncryptedCharField(max_length=200)
    telephone = EncryptedCharField(max_length=20, blank=True, null=True)
    mobile = EncryptedCharField(max_length=20, blank=True, null=True)
    email = EncryptedCharField(max_length=254)
    nic_passport = EncryptedCharField(max_length=50)
    tin = EncryptedCharField(max_length=50, blank=True, null=True)
    pin = EncryptedCharField(max_length=50, blank=True, null=True)

    class Meta:
        db_table = 'declarant_details'


# ─── WHT CERTIFICATES ────────────────────────────────────────────────────────

class WHTCertificate(models.Model):
    WHT_CATEGORY_CHOICES = [
        ('rent', 'Rent'),
        ('interest', 'Interest'),
        ('service_fees', 'Service Fees'),
        ('employment', 'Employment (APIT)'),
        ('other', 'Other'),
    ]
    submission = models.ForeignKey(TaxSubmission, on_delete=models.CASCADE, related_name='wht_certificates')
    category = models.CharField(max_length=20, choices=WHT_CATEGORY_CHOICES, default='other')
    certificate_file = models.FileField(upload_to=_wht_cert_upload_path, null=True, blank=True)
    original_filename = models.CharField(max_length=255, blank=True)
    amount = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'),
                                 help_text='WHT amount shown on certificate')
    uploaded_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True, null=True)

    class Meta:
        db_table = 'wht_certificates'
        ordering = ['category', '-uploaded_at']

    def __str__(self):
        return f"{self.get_category_display()} — Rs. {self.amount}"


# ─── PREVIOUS YEAR ACCESS REQUESTS ───────────────────────────────────────────

class PreviousYearAccessRequest(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('denied', 'Denied'),
    ]
    client = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='access_requests',
    )
    tax_year = models.ForeignKey(TaxYear, on_delete=models.CASCADE, related_name='access_requests')
    requested_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='approved_access_requests',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, null=True)

    class Meta:
        db_table = 'previous_year_access_requests'
        unique_together = ['client', 'tax_year']
        ordering = ['-requested_at']

    def __str__(self):
        return f"{self.client.email} → {self.tax_year.label} ({self.status})"


# ─── SYSTEM SETTINGS ─────────────────────────────────────────────────────────

class SystemSettings(models.Model):
    """Singleton model for global configuration (company name, logo, etc.)."""
    company_name = models.CharField(max_length=200, default='TAX AUTOMATION PORTAL')
    company_tagline = models.CharField(max_length=300, blank=True, default='PERSONAL INCOME TAX RETURN')
    company_logo = models.ImageField(upload_to=_logo_upload_path, null=True, blank=True)
    footer_text = models.CharField(max_length=300, blank=True, default='Tax Automation Portal | Confidential')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'system_settings'
        verbose_name = 'System Settings'
        verbose_name_plural = 'System Settings'

    def save(self, *args, **kwargs):
        self.pk = 1  # enforce singleton
        super().save(*args, **kwargs)

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return self.company_name


# ─── CASH FLOW / RECEIPTS & PAYMENTS STATEMENT ───────────────────────────────

class CashFlowStatement(models.Model):
    """Receipts & Payments (cash flow) statement for the assessment year."""
    submission = models.OneToOneField(
        TaxSubmission, on_delete=models.CASCADE, related_name='cash_flow'
    )

    # Opening balances (1 April)
    opening_cash_in_hand = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    opening_favourable_banks = EncryptedJSONField(default=list, blank=True)   # [{bank_name, account_no, amount}]
    opening_overdraft_banks  = EncryptedJSONField(default=list, blank=True)   # [{bank_name, account_no, amount}]

    # Receipts during the year
    receipt_employment_income       = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    receipt_interest_fds            = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    receipt_interest_savings        = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    receipt_rent_income             = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    receipt_tb_securities           = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    receipt_sale_shares             = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    receipt_dividend_income         = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    receipt_drawings_sole_partner   = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    receipt_bank_loan               = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    receipt_other_loans             = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    receipt_debtor_received         = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    receipt_sale_land_building      = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    receipt_sale_motor_vehicle      = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    receipt_sale_other_assets       = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    receipt_other_items             = EncryptedJSONField(default=list, blank=True)  # [{description, amount}]

    # Payments during the year
    payment_purchase_land_building  = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    payment_purchase_motor_vehicle  = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    payment_purchase_other_assets   = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    payment_repayment_bank_loan     = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    payment_lease_rentals           = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    payment_jewellery_gems          = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    payment_other_loans             = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    payment_wht                     = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    payment_income_tax              = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    payment_apit                    = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    payment_investment_shares       = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    payment_loans_given_others      = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    payment_other_items             = EncryptedJSONField(default=list, blank=True)  # [{description, amount}]

    # Closing balances (31 March)
    closing_cash_in_hand    = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    closing_favourable_banks = EncryptedJSONField(default=list, blank=True)
    closing_overdraft_banks  = EncryptedJSONField(default=list, blank=True)

    # Living expenses
    living_expenses_year    = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))

    class Meta:
        db_table = 'cash_flow_statements'


# ─── AUDIT / EDIT LOG ────────────────────────────────────────────────────────

class SubmissionEditLog(models.Model):
    ACTION_CHOICES = [
        ('update', 'Updated'),
        ('add', 'Added Row'),
        ('delete', 'Deleted Row'),
    ]

    submission = models.ForeignKey(
        TaxSubmission, on_delete=models.CASCADE, related_name='edit_logs'
    )
    edited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='form_edits',
    )
    section = models.CharField(max_length=100)
    action = models.CharField(max_length=20, choices=ACTION_CHOICES, default='update')
    description = models.TextField(blank=True)
    old_data = models.JSONField(null=True, blank=True)
    new_data = models.JSONField(null=True, blank=True)
    edited_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'submission_edit_logs'
        ordering = ['-edited_at']

    def __str__(self):
        return f"{self.edited_by} — {self.section} ({self.action})"
