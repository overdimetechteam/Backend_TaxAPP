"""
Sri Lanka Personal Income Tax Calculator - Y/A 2025/2026
Tax slabs per the Inland Revenue Act amendments.
"""
from decimal import Decimal, ROUND_HALF_UP


TAX_SLABS = [
    (Decimal('1000000'), Decimal('0.06')),   # First Rs. 1,000,000 @ 6%
    (Decimal('500000'),  Decimal('0.18')),   # Next Rs. 500,000 @ 18%
    (Decimal('500000'),  Decimal('0.24')),   # Next Rs. 500,000 @ 24%
    (Decimal('500000'),  Decimal('0.30')),   # Next Rs. 500,000 @ 30%
    (None,               Decimal('0.36')),   # Balance @ 36%
]

PERSONAL_RELIEF = Decimal('1800000.00')
SOLAR_MAX = Decimal('600000.00')
RENT_RELIEF_RATE = Decimal('0.25')
FOREIGN_INCOME_MAX_RATE = Decimal('0.15')  # foreign income's slab rate is capped at 15%

SLAB_LABELS = [
    'First Rs. 1,000,000 @ 6%',
    'Next Rs. 500,000 @ 18%',
    'Next Rs. 500,000 @ 24%',
    'Next Rs. 500,000 @ 30%',
    'Balance @ 36%',
]


def calculate_tax_on_income(taxable_income: Decimal) -> tuple[Decimal, list[dict]]:
    """
    Calculate gross tax based on Sri Lanka tax slabs (local/non-foreign income only).
    Returns (gross_tax, slab_breakdown) where slab_breakdown is a list of dicts
    showing each slab's taxable amount and tax computed.
    """
    local_tax, _, local_breakdown, _ = calculate_mixed_tax(taxable_income, Decimal('0.00'))
    return local_tax, local_breakdown


def calculate_mixed_tax(taxable_local: Decimal, taxable_foreign: Decimal):
    """
    Apply the progressive slabs to local and foreign taxable income together.

    Local income fills each slab first at the normal rate. Any slab capacity left
    over in that bracket is then filled by foreign income, but the rate applied to
    foreign income is capped at 15% (so the first Rs. 1,000,000 of foreign income,
    net of whatever bracket space local income already used, is taxed at 6%, and any
    excess is taxed at 15% rather than the higher local progressive rates).

    foreign_breakdown is grouped by effective tax percentage rather than by the
    underlying slab — every bracket above the first caps to the same 15%, so this
    collapses to at most two rows (6% and 15%) instead of one row per slab.

    Returns (local_tax, foreign_tax, local_breakdown, foreign_breakdown).
    """
    local_remaining = taxable_local if taxable_local > 0 else Decimal('0.00')
    foreign_remaining = taxable_foreign if taxable_foreign > 0 else Decimal('0.00')

    local_tax = Decimal('0.00')
    foreign_tax = Decimal('0.00')
    local_breakdown = []
    foreign_by_rate = {}  # rate -> {'taxable_amount': Decimal, 'tax': Decimal}
    foreign_rate_order = []

    for idx, (slab_amount, rate) in enumerate(TAX_SLABS):
        if local_remaining <= 0 and foreign_remaining <= 0:
            break

        capacity = slab_amount  # None means unlimited (final "balance" slab)

        local_used = local_remaining if capacity is None else min(local_remaining, capacity)
        if local_used > 0:
            slab_tax = (local_used * rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            local_tax += slab_tax
            local_breakdown.append({
                'label': SLAB_LABELS[idx],
                'rate': str(rate),
                'taxable_amount': str(local_used.quantize(Decimal('0.01'))),
                'tax': str(slab_tax),
            })
            local_remaining -= local_used
            if capacity is not None:
                capacity -= local_used

        foreign_used = foreign_remaining if capacity is None else min(foreign_remaining, capacity)
        if foreign_used > 0:
            effective_rate = min(rate, FOREIGN_INCOME_MAX_RATE)
            slab_tax = (foreign_used * effective_rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            foreign_tax += slab_tax
            if effective_rate in foreign_by_rate:
                bucket = foreign_by_rate[effective_rate]
                bucket['taxable_amount'] += foreign_used
                bucket['tax'] += slab_tax
            else:
                foreign_by_rate[effective_rate] = {'taxable_amount': foreign_used, 'tax': slab_tax}
                foreign_rate_order.append(effective_rate)
            foreign_remaining -= foreign_used

    foreign_breakdown = [
        {
            'rate': str(rate),
            'taxable_amount': str(foreign_by_rate[rate]['taxable_amount'].quantize(Decimal('0.01'))),
            'tax': str(foreign_by_rate[rate]['tax'].quantize(Decimal('0.01'))),
        }
        for rate in foreign_rate_order
    ]

    return (
        local_tax.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP),
        foreign_tax.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP),
        local_breakdown,
        foreign_breakdown,
    )


def calculate_full_tax(submission) -> dict:
    """
    Calculate full tax liability for a submission.
    Returns a dict with all calculated values and a detailed slab breakdown.

    The tax-free personal relief is applied to local (non-foreign) income first; any
    unused balance then offsets foreign income. Local income fills the progressive
    slabs first at normal rates, and foreign income fills the remaining slab space at
    a rate capped at 15% (so foreign income is effectively 6% on its first bracket and
    at most 15% beyond that, regardless of how high the local progressive rate climbs).
    Exempt dividends are excluded from TAI and tracked separately.
    Rent relief is auto-calculated at 25% of gross rent.
    Foreign tax paid is treated as a direct tax credit against the foreign income tax.
    Returns slab_breakdown for detailed display (local portion only).
    """
    # ── 1. Income sources ────────────────────────────────────────────────────

    local_emp = Decimal('0.00')
    if hasattr(submission, 'local_employment'):
        local_emp = submission.local_employment.amount or Decimal('0.00')

    # Foreign income (Change 18)
    foreign = Decimal('0.00')
    foreign_tax_paid = Decimal('0.00')
    if hasattr(submission, 'foreign_income'):
        fi = submission.foreign_income
        foreign = (
            (fi.employment_service_fee or Decimal('0.00')) +
            (fi.foreign_business_income or Decimal('0.00')) +
            (fi.other_foreign_income or Decimal('0.00'))
        )
        foreign_tax_paid = fi.foreign_tax_paid or Decimal('0.00')

    terminal = Decimal('0.00')
    if hasattr(submission, 'terminal_benefit'):
        terminal = submission.terminal_benefit.amount or Decimal('0.00')

    rent_gross = Decimal('0.00')
    rent_wht = Decimal('0.00')
    if hasattr(submission, 'rent_income'):
        rent_gross = submission.rent_income.gross_amount or Decimal('0.00')
        rent_wht   = submission.rent_income.wht_deducted or Decimal('0.00')

    interest = Decimal('0.00')
    interest_wht = Decimal('0.00')
    if hasattr(submission, 'interest_income'):
        interest     = submission.interest_income.amount or Decimal('0.00')
        interest_wht = submission.interest_income.wht_deducted or Decimal('0.00')

    # Dividend income — separate taxable vs exempt (Change 16)
    dividend_taxable = Decimal('0.00')
    dividend_exempt = Decimal('0.00')
    if hasattr(submission, 'dividend_income'):
        di = submission.dividend_income
        dividend_taxable = di.amount or Decimal('0.00')
        dividend_exempt = di.exempt_amount or Decimal('0.00')

    sole_prop = Decimal('0.00')
    sole_prop_wht = Decimal('0.00')
    for sp in submission.sole_proprietorships.all():
        sole_prop += sp.amount or Decimal('0.00')
        sole_prop_wht += sp.wht_deducted or Decimal('0.00')

    other_inc = Decimal('0.00')
    if hasattr(submission, 'other_income'):
        other_inc = submission.other_income.amount or Decimal('0.00')

    tb_securities = Decimal('0.00')
    tb_securities_wht = Decimal('0.00')
    if hasattr(submission, 'tb_securities'):
        tb_securities     = submission.tb_securities.gross_amount or Decimal('0.00')
        tb_securities_wht = submission.tb_securities.wht_deducted  or Decimal('0.00')

    # Total Assessable Income — includes all income sources including foreign.
    # Exempt dividends excluded per Change 16.
    total_assessable = (
        local_emp + foreign + terminal + rent_gross +
        interest + dividend_taxable + sole_prop + other_inc + tb_securities
    )

    # ── 2. Qualifying Payments & Reliefs ────────────────────────────────────

    donation_charitable = Decimal('0.00')
    donation_govt = Decimal('0.00')
    solar = Decimal('0.00')

    if hasattr(submission, 'qualifying_payments'):
        qp = submission.qualifying_payments
        donation_charitable = qp.donation_charitable or Decimal('0.00')
        donation_govt = qp.donation_government or Decimal('0.00')
        solar = min(qp.solar_panels_expenditure or Decimal('0.00'), SOLAR_MAX)

    total_qualifying = donation_charitable + donation_govt + solar

    # Reliefs
    personal_relief = PERSONAL_RELIEF
    # Auto rent relief: 25% of gross rent (Change 17)
    rent_relief = (rent_gross * RENT_RELIEF_RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    # ── 3. Taxable Income ────────────────────────────────────────────────────
    # Qualifying payments and rent relief offset local (non-foreign) income only.
    # The personal relief (tax-free allowance) is applied to local income first;
    # any unused balance then offsets foreign income.

    non_foreign_income = total_assessable - foreign
    local_base = max(Decimal('0.00'), non_foreign_income - total_qualifying - rent_relief)

    local_relief_used = min(personal_relief, local_base)
    taxable_local = local_base - local_relief_used
    remaining_relief = personal_relief - local_relief_used
    taxable_foreign = max(Decimal('0.00'), foreign - remaining_relief)

    net_taxable = taxable_local + taxable_foreign

    # ── 4. Tax Computation with slab breakdown ──────────────────────────────
    # Local income fills the progressive slabs first at normal rates; foreign
    # income fills any remaining slab space at a rate capped at 15%.
    gross_tax, foreign_tax_gross, slab_breakdown, foreign_slab_breakdown = calculate_mixed_tax(
        taxable_local, taxable_foreign
    )

    # ── 5. Tax Credits ───────────────────────────────────────────────────────

    apit = Decimal('0.00')
    partnership_credit = Decimal('0.00')
    self_assessment_total = Decimal('0.00')

    if hasattr(submission, 'tax_credits'):
        tc = submission.tax_credits
        apit          = tc.apit_on_salary or Decimal('0.00')
        partnership_credit = tc.partnership_tax_credit or Decimal('0.00')

    for sap in submission.self_assessment_payments.all():
        self_assessment_total += sap.amount or Decimal('0.00')

    # WHT actually withheld at source on rent / interest / sole-proprietorship /
    # TB-securities income — computed live from the income records themselves so
    # it always feeds the total, regardless of whether the (separately saved)
    # Tax Credits form was ever submitted.
    wht_from_income = rent_wht + interest_wht + sole_prop_wht + tb_securities_wht

    # WHT certificates cover categories not captured by the income-section WHT
    # fields above (service fees, employment, other). Rent/Interest certificates
    # are supporting evidence for wht_from_income and are excluded here to avoid
    # double-counting the same withholding twice.
    wht_from_certs = Decimal('0.00')
    for cert in submission.wht_certificates.exclude(category__in=('rent', 'interest')):
        wht_from_certs += cert.amount or Decimal('0.00')

    total_credits = apit + wht_from_income + wht_from_certs + partnership_credit + self_assessment_total

    # ── 6. Foreign income tax (Schedule 9 cage 901) ─────────────────────────
    # foreign_tax_gross was computed in step 4 (capped at 15% per slab).
    # Foreign tax paid abroad offsets this liability.
    foreign_tax_net = max(Decimal('0.00'), foreign_tax_gross - foreign_tax_paid)

    # ── 7. Net Tax Payable ───────────────────────────────────────────────────

    normal_tax = max(Decimal('0.00'), gross_tax - total_credits)
    net_tax = normal_tax + foreign_tax_net

    return {
        'total_assessable_income': total_assessable,
        'exempt_dividend_income': dividend_exempt,
        'total_qualifying_payments': total_qualifying,
        'personal_relief': personal_relief,
        'rent_relief': rent_relief,
        'net_taxable_income': net_taxable,
        'gross_tax': gross_tax,
        'total_tax_credits': total_credits,
        'wht_rent': rent_wht,
        'wht_interest': interest_wht,
        'wht_sole_prop': sole_prop_wht,
        'wht_tb_securities': tb_securities_wht,
        'foreign_income': foreign,
        'foreign_income_tax': foreign_tax_net,      # net tax after foreign tax credit
        'net_tax_payable': net_tax,
        'slab_breakdown': slab_breakdown,
        'foreign_slab_breakdown': foreign_slab_breakdown,
        'breakdown': {
            'local_employment': local_emp,
            'foreign_income': foreign,
            'taxable_local': taxable_local,
            'taxable_foreign': taxable_foreign,
            'foreign_tax_paid': foreign_tax_paid,
            'foreign_tax_gross': foreign_tax_gross,   # tax on taxable_foreign, capped at 15% per slab
            'foreign_tax_net': foreign_tax_net,       # after deducting foreign_tax_paid
            'terminal_benefit': terminal,
            'rent_income': rent_gross,
            'interest_income': interest,
            'dividend_income': dividend_taxable,
            'dividend_exempt': dividend_exempt,
            'sole_proprietorship': sole_prop,
            'wht_sole_prop': sole_prop_wht,
            'other_income': other_inc,
            'donation_charitable': donation_charitable,
            'donation_government': donation_govt,
            'solar_panels': solar,
            'apit': apit,
            'wht_from_income': wht_from_income,
            'wht_from_certs': wht_from_certs,
            'partnership_credit': partnership_credit,
            'self_assessment': self_assessment_total,
        }
    }
