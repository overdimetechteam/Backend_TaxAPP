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
CAPITAL_GAIN_TAX_RATE = Decimal('0.15')    # flat rate on net gain from disposal of assets

SLAB_LABELS = [
    'First Rs. 1,000,000 @ 6%',
    'Next Rs. 500,000 @ 18%',
    'Next Rs. 500,000 @ 24%',
    'Next Rs. 500,000 @ 30%',
    'Balance @ 36%',
]


def calculate_wht_credit_and_carry_forward(gross_tax, other_tax_credits, wht_total):
    """
    Two-step credit application:
      1. Gross tax is reduced by 'other' credits (APIT, Self-Assessment, Partnership,
         Tax Refund Claim) — floored at zero. Any excess here is NOT carried forward;
         only WHT is eligible to carry forward.
      2. What's left is then reduced by WHT (this year's WHT + last year's brought-forward
         WHT). If WHT more than covers it, the excess becomes this year's carry-forward,
         which becomes next year's wht_brought_forward.

    Returns (net_tax_payable, wht_carried_forward) — both Decimal, both >= 0.
    """
    net_after_other_credits = max(Decimal('0.00'), gross_tax - other_tax_credits)
    net_after_wht = net_after_other_credits - wht_total

    if net_after_wht < 0:
        net_tax_payable = Decimal('0.00')
        wht_carried_forward = -net_after_wht
    else:
        net_tax_payable = net_after_wht
        wht_carried_forward = Decimal('0.00')

    return net_tax_payable, wht_carried_forward


def calculate_capital_gain_tax(disposals):
    """
    Net capital gain = sum of (sales_proceed - cost) across disposal entries
    flagged is_capital_gain=True (added via the Income section's "Capital Gain"
    table) only. Entries added via the Assets section's "Disposal of Assets"
    table (section 10) are reporting-only and never taxed. A net loss floors
    the taxable gain at zero (no CGT refund for an overall loss). Flat 15%
    rate applied to the net gain.

    Returns (capital_gain, capital_gain_tax) — both Decimal, both >= 0.
    """
    net_gain = Decimal('0.00')
    for d in disposals:
        if not d.is_capital_gain:
            continue
        net_gain += (d.sales_proceed or Decimal('0.00')) - (d.cost or Decimal('0.00'))
    capital_gain = max(Decimal('0.00'), net_gain)
    capital_gain_tax = (capital_gain * CAPITAL_GAIN_TAX_RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return capital_gain, capital_gain_tax


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
    for lei in submission.local_employments.all():
        local_emp += lei.amount or Decimal('0.00')

    # Foreign income (Change 18)
    foreign = Decimal('0.00')
    foreign_tax_paid = Decimal('0.00')
    # Foreign Interest is exempt from tax (conceptually an Interest Income line, not
    # taxable Foreign Income) — excluded from the taxable `foreign` total entirely.
    foreign_interest_exempt = Decimal('0.00')
    if hasattr(submission, 'foreign_income'):
        fi = submission.foreign_income
        foreign = (
            (fi.employment_service_fee or Decimal('0.00')) +
            (fi.foreign_business_income or Decimal('0.00')) +
            (fi.other_foreign_income or Decimal('0.00'))
        )
        foreign_interest_exempt = fi.foreign_interest_income or Decimal('0.00')
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

    # Dividend income — separate taxable vs exempt (Change 16). exempt_amount is the
    # GROSS dividend subject to final WHT (di.final_wht) — fully excluded from
    # assessable income below; final_wht is disclosure-only (Schedule 6A), never a
    # credit, since it already fully settles that dividend's own tax liability.
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

    # Capital gains — flat 15% on net gain from disposal of assets during the year.
    capital_gain, capital_gain_tax = calculate_capital_gain_tax(submission.disposals.all())

    # Total Assessable Income — includes all income sources including foreign.
    # Exempt dividends excluded per Change 16.
    total_assessable = (
        local_emp + foreign + terminal + rent_gross +
        interest + dividend_taxable + sole_prop + other_inc + tb_securities +
        capital_gain
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
    # Rent relief offsets local (non-foreign) income only — it is 25% of gross
    # rent, a locally-sourced income component, so it never exceeds local income.
    # Qualifying payments (donations, solar) and the personal relief both offset
    # local income first; any unused balance from either then spills over to
    # offset foreign income. Without this spillover, clients whose income is
    # mostly/entirely foreign employment income (little or no local income to
    # absorb the deduction) would see qualifying payments like the solar relief
    # have no effect on their tax at all.

    # Capital gain is excluded here — it's taxed separately at a flat 15% via
    # capital_gain_tax, not at progressive slab rates, so it must not enter
    # taxable_local/taxable_foreign (it's still counted in total_assessable above
    # for reporting purposes only).
    non_foreign_income = total_assessable - foreign - capital_gain
    local_after_rent = max(Decimal('0.00'), non_foreign_income - rent_relief)

    local_reliefs = total_qualifying + personal_relief
    local_relief_used = min(local_reliefs, local_after_rent)
    taxable_local = local_after_rent - local_relief_used
    remaining_relief = local_reliefs - local_relief_used
    taxable_foreign = max(Decimal('0.00'), foreign - remaining_relief)

    # Capital gain is added back in here — it's excluded from the progressive
    # slab computation below (calculate_mixed_tax only ever sees taxable_local/
    # taxable_foreign), but the reported "Taxable Income" figure itself (cage
    # 120) does include it.
    net_taxable = taxable_local + taxable_foreign + capital_gain

    # ── 4. Tax Computation with slab breakdown ──────────────────────────────
    # Local income fills the progressive slabs first at normal rates; foreign
    # income fills any remaining slab space at a rate capped at 15%.
    gross_tax, foreign_tax_gross, slab_breakdown, foreign_slab_breakdown = calculate_mixed_tax(
        taxable_local, taxable_foreign
    )

    # ── 5. Tax Credits ───────────────────────────────────────────────────────

    apit = Decimal('0.00')
    partnership_credit = Decimal('0.00')
    wht_brought_forward = Decimal('0.00')
    refund_brought_forward = Decimal('0.00')
    self_assessment_total = Decimal('0.00')

    if hasattr(submission, 'tax_credits'):
        tc = submission.tax_credits
        apit          = tc.apit_on_salary or Decimal('0.00')
        partnership_credit = tc.partnership_tax_credit or Decimal('0.00')
        # NOTE: TaxCredits.tax_refund_claim (the old free-typed "Tax Refund
        # Claim" field) is retired from the calculation and from every form —
        # "Tax Refund Claim" is now just the display name for
        # refund_brought_forward (60% of last Y/A's refund_carried_forward),
        # so it is not read here at all any more.
        wht_brought_forward = tc.wht_brought_forward or Decimal('0.00')
        refund_brought_forward = tc.refund_brought_forward or Decimal('0.00')

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

    # ── 6. Foreign income tax (Schedule 9 cage 901) ─────────────────────────
    # foreign_tax_gross was computed in step 4 (capped at 15% per slab).
    # Foreign tax paid abroad offsets this liability first, before combining
    # with local tax below so that local-source credits (APIT/WHT/self-
    # assessment/etc.) can also offset it — see Step 1/2 below. foreign_tax_net
    # is still returned separately (as 'foreign_income_tax') for schedule-level
    # display, independent of whether credits later reduce the combined bill.
    foreign_tax_net = max(Decimal('0.00'), foreign_tax_gross - foreign_tax_paid)

    # ── 7. Net Tax Payable ───────────────────────────────────────────────────
    # Combined tax base = local gross tax + foreign tax (net of tax paid
    # abroad) + Capital Gains Tax. Capital Gains Tax is added to the gross
    # payable here, then also listed as a Step-1 credit below (per DPR
    # instruction) so it is deducted back out — the two cancel, meaning CGT's
    # net contribution to Net Tax Payable is Rs. 0 once this combined figure is
    # run through the credit steps. It is still disclosed on its own (flat 15%
    # of Capital Gain) via 'capital_gain_tax' in the return dict.
    # Credits are applied against this combined figure so that a client whose
    # income is mostly/entirely foreign still benefits from their APIT/WHT/
    # self-assessment credits, instead of those credits being stranded against
    # a small/zero local gross tax.
    # Step 1: combined tax is reduced by non-WHT credits (APIT, Self-Assessment,
    # Partnership, Capital Gains Tax, and "Tax Refund Claim" — which is now
    # just the display name for refund_brought_forward, the 60% figure brought
    # forward from last year; the old free-typed tax_refund_claim field has
    # been retired from the form and from this calculation entirely), floored
    # at zero.
    # refund_brought_forward is ALREADY the 60% slice (only 60% of last year's
    # refund_carried_forward is ever copied into it by _prefill_refund_brought_
    # forward in views.py — the other 40% is never transferred at all, so it is
    # inherently never claimable or carried forward any further).
    # refund_carried_forward (THIS year's new carry-forward figure, which will
    # itself be reduced to 60% for next year) is deliberately computed from
    # step1_credits_base only — EXCLUDING refund_brought_forward — so that any
    # portion of this year's 60% claim that goes unused does NOT itself spawn a
    # further carry-forward next year. It is a one-time, use-it-or-lose-it claim:
    # claimable once at 60%, and whatever isn't used this year is gone for good.
    # Step 2: what's left is reduced by WHT (this year's + brought-forward from last
    # year). Any WHT left over after that becomes this year's carry-forward, which
    # flows into next year's wht_brought_forward when that submission is created.
    combined_gross_tax = gross_tax + foreign_tax_net + capital_gain_tax
    step1_credits_base = apit + partnership_credit + self_assessment_total + capital_gain_tax
    other_tax_credits = step1_credits_base + refund_brought_forward
    wht_total = wht_from_income + wht_from_certs + wht_brought_forward

    refund_carried_forward = max(Decimal('0.00'), step1_credits_base - combined_gross_tax)

    normal_tax, wht_carried_forward = calculate_wht_credit_and_carry_forward(
        combined_gross_tax, other_tax_credits, wht_total
    )
    wht_used = wht_total - wht_carried_forward
    total_credits = other_tax_credits + wht_used

    net_tax = normal_tax

    return {
        'total_assessable_income': total_assessable,
        'exempt_dividend_income': dividend_exempt,
        'total_qualifying_payments': total_qualifying,
        'personal_relief': personal_relief,
        'rent_relief': rent_relief,
        'net_taxable_income': net_taxable,
        'gross_tax': gross_tax,
        'total_tax_credits': total_credits,
        'wht_brought_forward': wht_brought_forward,
        'wht_carried_forward': wht_carried_forward,
        'refund_brought_forward': refund_brought_forward,
        'refund_carried_forward': refund_carried_forward,
        'capital_gain': capital_gain,
        'capital_gain_tax': capital_gain_tax,
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
            'foreign_interest_exempt': foreign_interest_exempt,
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
            'refund_brought_forward': refund_brought_forward,
            'refund_carried_forward': refund_carried_forward,
        }
    }
