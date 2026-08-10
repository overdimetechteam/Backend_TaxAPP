"""
Auto-recalculate TaxSubmission whenever any income or deduction model is saved or deleted.
This ensures the consultant always sees up-to-date calculations without clicking a button.
"""
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from .tax_calculator import calculate_full_tax


def _recalculate_for_submission(submission_id):
    """Fetch a fresh TaxSubmission and recalculate all tax fields."""
    try:
        from .models import TaxSubmission
        sub = TaxSubmission.objects.select_related(
            'local_employment', 'foreign_income', 'terminal_benefit',
            'rent_income', 'interest_income', 'dividend_income',
            'other_income', 'qualifying_payments', 'tax_credits',
            'tb_securities',
        ).prefetch_related(
            'sole_proprietorships', 'self_assessment_payments', 'wht_certificates',
        ).get(pk=submission_id)

        result = calculate_full_tax(sub)

        TaxSubmission.objects.filter(pk=submission_id).update(
            total_assessable_income=result['total_assessable_income'],
            exempt_dividend_income=result['exempt_dividend_income'],
            total_qualifying_payments=result['total_qualifying_payments'],
            personal_relief=result['personal_relief'],
            rent_relief=result['rent_relief'],
            net_taxable_income=result['net_taxable_income'],
            gross_tax=result['gross_tax'],
            total_tax_credits=result['total_tax_credits'],
            foreign_income_tax=result['foreign_income_tax'],
            net_tax_payable=result['net_tax_payable'],
            slab_breakdown=result['slab_breakdown'],
        )
    except Exception:
        pass  # never break client form saves


def _income_changed(sender, instance, **kwargs):
    submission_id = getattr(instance, 'submission_id', None)
    if submission_id is not None:
        _recalculate_for_submission(submission_id)


def connect_signals():
    """Connect post_save and post_delete signals for all income-related models."""
    from .models import (
        LocalEmploymentIncome, ForeignIncome, TerminalBenefit,
        RentIncome, InterestIncome, DividendIncome,
        SoleProprietorshipIncome, OtherIncome, TBSecuritiesIncome,
        QualifyingPayments, TaxCredits,
        SelfAssessmentPayment, WHTCertificate,
    )
    trigger_models = [
        LocalEmploymentIncome, ForeignIncome, TerminalBenefit,
        RentIncome, InterestIncome, DividendIncome,
        SoleProprietorshipIncome, OtherIncome, TBSecuritiesIncome,
        QualifyingPayments, TaxCredits,
        SelfAssessmentPayment, WHTCertificate,
    ]
    for model in trigger_models:
        post_save.connect(_income_changed, sender=model, weak=False)
        post_delete.connect(_income_changed, sender=model, weak=False)
