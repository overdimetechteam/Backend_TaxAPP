from django.db import models
from django.conf import settings


class Document(models.Model):
    DOCUMENT_TYPE_CHOICES = [
        # Income documents
        ('t10_salary_slip', 'T10 / Salary Slips'),
        ('monthly_salary_slip', 'Monthly Salary Slips'),
        ('tax_direction_letter', 'Tax Direction Letter / Terminal Benefit Confirmation'),
        ('rent_agreement', 'Rent Agreement / WHT Deduction Certificates'),
        ('bank_balance_confirmation', 'WHT Certificates'),
        ('dividend_certificate', 'Dividend Certificate (Dividend Warrant)'),
        ('tb_securities_certificate', 'T-Bill / Securities Income Certificates'),
        ('partnership_accounts', 'Receipt & Payment Details / Partnership Accounts'),
        ('other_income_proof', 'Other Income Proof Documents'),
        # Qualifying payment documents
        ('donation_proof', 'Donation Proof Document'),
        ('solar_invoice', 'Solar Panel Invoice & Grid Agreement'),
        # Tax credit documents
        ('self_assessment_receipt', 'Self Assessment Pay-in Slips / Online Receipt'),
        ('t10_certificate', 'T10 Certificate (APIT)'),
        ('wht_certificate', 'WHT Certificate'),
        # Liability documents
        ('bank_confirmation_letter', 'Bank / Financial Institute Confirmation Letter'),
        # General
        ('supporting_document', 'Supporting Document'),
        ('final_submission', 'Final Tax Submission Document'),
        ('ird_return', 'IRD Return'),
        ('ird_acknowledgement', 'IRD Acknowledgement'),
        ('other', 'Other'),
    ]

    SECTION_CHOICES = [
        ('income', 'Income & Expenses'),
        ('qualifying_payments', 'Qualifying Payments'),
        ('tax_credits', 'Tax Credits'),
        ('assets', 'Assets'),
        ('liabilities', 'Liabilities'),
        ('declarant', 'Declarant Details'),
        ('general', 'General'),
    ]

    submission = models.ForeignKey(
        'tax_forms.TaxSubmission',
        on_delete=models.CASCADE,
        related_name='documents'
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='uploaded_documents'
    )
    document_type = models.CharField(max_length=50, choices=DOCUMENT_TYPE_CHOICES, default='supporting_document')
    section = models.CharField(max_length=30, choices=SECTION_CHOICES, default='general')
    file = models.FileField(upload_to='documents/%Y/%m/')
    original_filename = models.CharField(max_length=255)
    file_size = models.IntegerField(default=0)
    description = models.CharField(max_length=300, blank=True, null=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    is_verified = models.BooleanField(default=False)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='verified_documents'
    )

    class Meta:
        db_table = 'documents'
        ordering = ['-uploaded_at']

    def __str__(self):
        return f"{self.get_document_type_display()} - {self.original_filename}"

    @property
    def file_size_kb(self):
        return round(self.file_size / 1024, 1)
