"""
RAMIS Individual Income Tax Return PDF Generator
Replicates the official Sri Lanka IRD "Individual income tax - Confirmation" form.
"""
import os
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph,
    Spacer, HRFlowable, KeepTogether, PageBreak,
)
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT

# ── Page geometry ─────────────────────────────────────────────────────────────
PW, PH = A4
LM = RM = 1.5 * cm
TM = BM = 1.8 * cm
UW = PW - LM - RM          # usable width ≈ 178 mm

_LW = UW * 0.64             # label col  (no border)
_CW = UW * 0.09             # cage# col  (bordered)
_AW = UW * 0.27             # amount col (bordered)

BK = colors.black
LG = colors.HexColor('#EFEFEF')
MG = colors.HexColor('#CCCCCC')
DG = colors.HexColor('#555555')


# ── Helpers ───────────────────────────────────────────────────────────────────
def _D(v):
    if v is None:
        return Decimal('0')
    try:
        return Decimal(str(v))
    except Exception:
        return Decimal('0')


def _fmt(v):
    """Format amount as 1,234,567.00"""
    try:
        return f'{float(_D(v)):,.2f}'
    except Exception:
        return '0.00'


def _get_system_settings():
    try:
        from .models import SystemSettings
        return SystemSettings.get()
    except Exception:
        return None


def _ps(name, **kw):
    return ParagraphStyle(name, parent=getSampleStyleSheet()['Normal'], **kw)


def _build_styles():
    return {
        'form_title':  _ps('FT',   fontSize=10,  fontName='Helvetica-Bold',   alignment=TA_LEFT,   textColor=BK),
        'sec_hdr':     _ps('SH',   fontSize=8,   fontName='Helvetica-Bold',   alignment=TA_LEFT,   textColor=BK),
        'cage_lbl':    _ps('CL',   fontSize=8,   fontName='Helvetica-Bold',   alignment=TA_RIGHT,  textColor=BK),
        'cage_num':    _ps('CN',   fontSize=7,   fontName='Helvetica-Bold',   alignment=TA_CENTER, textColor=DG),
        'cage_amt':    _ps('CA',   fontSize=8,   fontName='Helvetica',        alignment=TA_RIGHT,  textColor=BK),
        'cage_amt_b':  _ps('CAB',  fontSize=8,   fontName='Helvetica-Bold',   alignment=TA_RIGHT,  textColor=BK),
        'tbl_hdr':     _ps('TH',   fontSize=7.5, fontName='Helvetica-Bold',   alignment=TA_CENTER, textColor=BK),
        'tbl_hdr_l':   _ps('THL',  fontSize=7.5, fontName='Helvetica-Bold',   alignment=TA_LEFT,   textColor=BK),
        'tbl_cell':    _ps('TC',   fontSize=8,   fontName='Helvetica',        alignment=TA_LEFT,   textColor=BK),
        'tbl_cell_r':  _ps('TCR',  fontSize=8,   fontName='Helvetica',        alignment=TA_RIGHT,  textColor=BK),
        'tbl_cell_b':  _ps('TCB',  fontSize=8,   fontName='Helvetica-Bold',   alignment=TA_LEFT,   textColor=BK),
        'tbl_cell_rb': _ps('TCRB', fontSize=8,   fontName='Helvetica-Bold',   alignment=TA_RIGHT,  textColor=BK),
        'info_lbl':    _ps('IL',   fontSize=8,   fontName='Helvetica-Bold',   alignment=TA_RIGHT,  textColor=DG),
        'info_val':    _ps('IV',   fontSize=8.5, fontName='Helvetica',        alignment=TA_LEFT,   textColor=BK),
        'decl':        _ps('DC',   fontSize=7.5, fontName='Helvetica-Oblique',alignment=TA_LEFT,   textColor=DG),
        'footer':      _ps('FO',   fontSize=7,   fontName='Helvetica',        alignment=TA_CENTER, textColor=DG),
        'small_gray':  _ps('SG',   fontSize=7.5, fontName='Helvetica',        alignment=TA_LEFT,   textColor=DG),
    }


def _P(text, st):
    return Paragraph(str(text), st)


def _sec(els, st, text):
    """RAMIS section header: bold text + thin HR."""
    els.append(Spacer(1, 5))
    els.append(_P(text, st['sec_hdr']))
    els.append(HRFlowable(width='100%', thickness=0.8, color=BK, spaceAfter=2, spaceBefore=1))


def _cr(st, label, cage, val, bold=False):
    """Cage row: [right-bold label | cage# | amount]"""
    return [
        _P(label, st['cage_lbl']),
        _P(str(cage) if cage is not None else '', st['cage_num']),
        _P(_fmt(val), st['cage_amt_b'] if bold else st['cage_amt']),
    ]


def _cage_tbl(rows):
    """Build cage table: label col has no border; cage# and amount cols have GRID."""
    t = Table(rows, colWidths=[_LW, _CW, _AW])
    t.setStyle(TableStyle([
        ('GRID',          (1, 0), (2, -1), 0.5, MG),
        ('TOPPADDING',    (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('LEFTPADDING',   (0, 0), (-1, -1), 4),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 4),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    return t


def _sched_table(hdr, rows, widths, st, total_idxs=None):
    """Standard bordered schedule table with light-gray header."""
    empty = not rows
    all_rows = [hdr] + (rows if rows else [
        [_P('—', st['tbl_cell'])] + [_P('', st['tbl_cell']) for _ in range(len(hdr) - 1)]
    ])
    sty = [
        ('GRID',          (0, 0), (-1, -1), 0.4, MG),
        ('BACKGROUND',    (0, 0), (-1, 0),  LG),
        ('TOPPADDING',    (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('LEFTPADDING',   (0, 0), (-1, -1), 4),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 4),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTSIZE',      (0, 0), (-1, -1), 8),
    ]
    if total_idxs and not empty:
        for ri in total_idxs:
            actual = ri + 1
            if actual < len(all_rows):
                sty += [
                    ('FONTNAME',  (0, actual), (-1, actual), 'Helvetica-Bold'),
                    ('LINEABOVE', (0, actual), (-1, actual), 0.8, BK),
                ]
    t = Table(all_rows, colWidths=widths)
    t.setStyle(TableStyle(sty))
    return t


# ── Header block ──────────────────────────────────────────────────────────────
def _add_header(els, st, submission, dd, sys_settings):
    # Company logo
    if sys_settings and sys_settings.company_logo:
        try:
            from reportlab.platypus import Image as RLImage
            lp = sys_settings.company_logo.path
            if os.path.exists(lp):
                logo = RLImage(lp, width=2.5 * cm, height=1.2 * cm)
                logo.hAlign = 'LEFT'
                els.append(logo)
                els.append(Spacer(1, 3))
        except Exception:
            pass

    els.append(_P('Individual income tax - Confirmation', st['form_title']))
    els.append(HRFlowable(width='100%', thickness=1, color=BK, spaceAfter=5, spaceBefore=2))

    name   = (dd.full_name    if dd else '') or ''
    tin    = (dd.tin          if dd else '') or ''
    nic    = (dd.nic_passport if dd else '') or ''
    mobile = (dd.mobile       if dd else '') or ''
    email  = (dd.email        if dd else '') or ''
    pin    = (dd.pin          if dd else '') or ''
    year   = submission.tax_year.label if submission.tax_year else ''

    IL = st['info_lbl']
    IV = st['info_val']

    def _val_cell(v):
        return _P(v or '—', IV)

    info = [
        [_P('TIN', IL),              _val_cell(tin),   _P('Name', IL),              _val_cell(name)],
        [_P('NIC / Passport No.', IL),_val_cell(nic),  _P('PIN', IL),               _val_cell(pin)],
        [_P('Mobile', IL),           _val_cell(mobile),_P('E-mail', IL),            _val_cell(email)],
        [_P('Year of Assessment', IL),_val_cell(year), _P('Resident', IL),
         _P('● Resident  ○ Non-resident', IV)],
        ['', '', _P('Senior citizen', IL), _P('○ Yes  ● No', IV)],
    ]
    cw = [UW * 0.18, UW * 0.32, UW * 0.18, UW * 0.32]
    t = Table(info, colWidths=cw)
    t.setStyle(TableStyle([
        ('GRID',          (0, 0), (-1, -1), 0.4, MG),
        ('TOPPADDING',    (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING',   (0, 0), (-1, -1), 5),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 5),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTSIZE',      (0, 0), (-1, -1), 8),
    ]))
    els.append(t)
    els.append(Spacer(1, 6))


# ── Parts A / B / C / D ───────────────────────────────────────────────────────
def _add_main_return(els, st, cages):
    c = cages

    # Part A
    _sec(els, st, 'Part A - Income liable to tax')
    els.append(_cage_tbl([
        _cr(st, 'Employment income (Rs.)',              10,  c[10]),
        _cr(st, 'Business income (Rs.)',                20,  c[20]),
        _cr(st, 'Investment income (Rs.)',              30,  c[30]),
        _cr(st, 'Other income (Rs.)',                   40,  c[40]),
        _cr(st, 'Assessable income (10+20+30+40) (Rs.)',50,  c[50], bold=True),
    ]))

    # Part B
    _sec(els, st, 'Part B - Deductions')
    els.append(_cage_tbl([
        _cr(st, 'Rent relief (Rs.)',                                    60,  c[60]),
        _cr(st, 'Solar panels qualifying payment (Rs.)',                70,  c[70]),
        _cr(st, 'Personal relief (Rs.)',                                80,  c[80]),
        _cr(st, 'Total reliefs (60+70+80) (Rs.)',                       90,  c[90],  bold=True),
        _cr(st, 'Total qualifying payments (refer schedule 5) (Rs.)',   100, c[100]),
        _cr(st, 'Total deductions (90+100) (Rs.)',                      110, c[110], bold=True),
        _cr(st, 'Taxable income (50-110) (Rs.)',                        120, c[120], bold=True),
    ]))

    # Part C
    _sec(els, st, 'Part C - Tax payable')
    els.append(_cage_tbl([
        _cr(st, 'Tax on terminal benefits (refer schedule 1) (Rs.)',        130, c[130]),
        _cr(st, 'Tax on investment asset gains (refer schedule 8) (Rs.)',   140, c[140]),
        _cr(st, 'Tax on balance taxable income (refer schedule 8) (Rs.)',   150, c[150]),
        _cr(st, 'WHT not deducted as a final payment (Rs.)',                160, c[160]),
        _cr(st, 'Total tax payable (130+140+150+160) (Rs.)',                170, c[170], bold=True),
        _cr(st, 'Less : Tax credits (refer schedule 9) (Rs.)',              180, c[180]),
        _cr(st, 'Balance tax payable (170-180) (Rs.)',                      190, c[190], bold=True),
        _cr(st, 'Refund (if 180>170) (Rs.)',                               200, c[200]),
    ]))

    # Part D
    _sec(els, st, 'Part D - Exempt income')
    els.append(_cage_tbl([
        _cr(st, 'Exempt income (Rs.)',              210,  c[210]),
        _cr(st, 'Foreign currency remitted (Rs.)',  '210A', c.get('210A', Decimal('0'))),
    ]))
    els.append(Spacer(1, 6))


# ── Schedule 1 — Employment Income ───────────────────────────────────────────
def _add_schedule_1(els, st, lei, fi, tb):
    _sec(els, st, 'Schedule 1 - Employment income')

    local_amt   = _D(lei and lei.amount)
    foreign_amt = _D(fi  and fi.employment_service_fee)
    total_emp   = local_amt + foreign_amt

    # Part I table
    els.append(_P('Part I : Employment income (Taxable)', st['sec_hdr']))
    els.append(Spacer(1, 2))
    hdr = [
        _P('S/N',             st['tbl_hdr']),
        _P('Type',            st['tbl_hdr_l']),
        _P('Name of Employer / Source', st['tbl_hdr_l']),
        _P('Employer TIN',    st['tbl_hdr']),
        _P('Remuneration (Rs.)', st['tbl_hdr']),
    ]
    rows = []
    if local_amt > 0:
        rows.append([
            _P('1', st['tbl_cell']),
            _P('Local employment', st['tbl_cell']),
            _P((lei and lei.employer_name) or '—', st['tbl_cell']),
            _P('', st['tbl_cell']),
            _P(_fmt(local_amt), st['tbl_cell_r']),
        ])
    if foreign_amt > 0:
        rows.append([
            _P(str(len(rows) + 1), st['tbl_cell']),
            _P('Foreign employment / service fee', st['tbl_cell']),
            _P((fi and fi.source_country) or '—', st['tbl_cell']),
            _P('', st['tbl_cell']),
            _P(_fmt(foreign_amt), st['tbl_cell_r']),
        ])
    cw = [UW*0.06, UW*0.18, UW*0.36, UW*0.18, UW*0.22]
    els.append(KeepTogether(_sched_table(hdr, rows, cw, st)))
    els.append(Spacer(1, 3))

    # Cage 105 — total employment income
    els.append(_cage_tbl([
        _cr(st, 'Total employment income (Rs.)', 105, total_emp, bold=True),
    ]))
    els.append(Spacer(1, 3))

    # Terminal benefits — Part I, cage 110
    tb_amt = _D(tb and tb.amount)
    els.append(_P('Terminal benefits', st['sec_hdr']))
    els.append(Spacer(1, 2))
    hdr2 = [
        _P('S/N',             st['tbl_hdr']),
        _P('Type of Benefit', st['tbl_hdr_l']),
        _P('Amount (Rs.)',    st['tbl_hdr']),
    ]
    rows2 = []
    if tb and tb_amt > 0:
        rows2.append([
            _P('1', st['tbl_cell']),
            _P((tb.benefit_types or 'Terminal benefit'), st['tbl_cell']),
            _P(_fmt(tb_amt), st['tbl_cell_r']),
        ])
    cw2 = [UW*0.07, UW*0.68, UW*0.25]
    els.append(KeepTogether(_sched_table(hdr2, rows2, cw2, st)))
    els.append(Spacer(1, 3))
    els.append(_cage_tbl([
        _cr(st, 'Tax on terminal benefits (Rs.)', 110, Decimal('0')),
    ]))
    els.append(Spacer(1, 3))

    # Part II — Employment income from exempt sources
    els.append(_P('Part II : Employment income from exempt sources', st['sec_hdr']))
    els.append(Spacer(1, 2))
    hdr3 = [
        _P('S/N',                       st['tbl_hdr']),
        _P('Type',                      st['tbl_hdr_l']),
        _P('Name of Employer / Source', st['tbl_hdr_l']),
        _P('Employer TIN',              st['tbl_hdr']),
        _P('Remuneration (Rs.)',        st['tbl_hdr']),
    ]
    cw3 = [UW*0.06, UW*0.18, UW*0.36, UW*0.18, UW*0.22]
    els.append(KeepTogether(_sched_table(hdr3, [], cw3, st)))
    els.append(Spacer(1, 3))
    els.append(_cage_tbl([
        _cr(st, 'Total employment income from exempt sources (Rs.)', 114, Decimal('0')),
    ]))
    els.append(Spacer(1, 5))


# ── Schedule 2 — Business Income ─────────────────────────────────────────────
def _add_schedule_2(els, st, sole_props, fi):
    _sec(els, st, 'Schedule 2 - Business income')

    sp_amt  = sum(_D(sp.amount) for sp in sole_props) if sole_props else Decimal('0')
    fb_amt  = _D(fi  and fi.foreign_business_income)
    total   = sp_amt + fb_amt

    # Part I — Sole Proprietorship
    els.append(_P('Part I : Sole proprietorship', st['sec_hdr']))
    els.append(Spacer(1, 2))
    hdr = [
        _P('S/N',           st['tbl_hdr']),
        _P('Name of Business', st['tbl_hdr_l']),
        _P('Business Reg. No.', st['tbl_hdr']),
        _P('Net Profit / (Loss) (Rs.)', st['tbl_hdr']),
    ]
    rows = []
    for idx, sp in enumerate(sole_props, 1):
        if _D(sp.amount) != 0:
            rows.append([
                _P(str(idx), st['tbl_cell']),
                _P(sp.business_name or '—', st['tbl_cell']),
                _P('', st['tbl_cell']),
                _P(_fmt(_D(sp.amount)), st['tbl_cell_r']),
            ])
    cw = [UW*0.06, UW*0.40, UW*0.22, UW*0.32]
    els.append(KeepTogether(_sched_table(hdr, rows, cw, st)))
    els.append(Spacer(1, 2))
    els.append(_cage_tbl([_cr(st, 'Total from sole proprietorship (Rs.)', 204, sp_amt)]))
    els.append(Spacer(1, 3))

    # Part II — Business income from exempt sources
    els.append(_P('Part II : Business income from exempt sources', st['sec_hdr']))
    els.append(Spacer(1, 2))
    hdr2 = [
        _P('S/N',             st['tbl_hdr']),
        _P('Description',     st['tbl_hdr_l']),
        _P('Amount (Rs.)',    st['tbl_hdr']),
    ]
    cw2 = [UW*0.06, UW*0.62, UW*0.32]
    els.append(KeepTogether(_sched_table(hdr2, [], cw2, st)))
    els.append(Spacer(1, 2))
    els.append(_cage_tbl([_cr(st, 'Total business income from exempt sources (Rs.)', 216, Decimal('0'))]))
    els.append(Spacer(1, 3))

    # Cage 215 total
    els.append(_cage_tbl([
        _cr(st, 'Total business income (Rs.)', 215, total, bold=True),
    ]))
    els.append(Spacer(1, 5))


# ── Schedule 3 — Investment Income ───────────────────────────────────────────
def _add_schedule_3(els, st, ri, ii, di, rent_relief, tbs=None):
    _sec(els, st, 'Schedule 3 - Investment income')

    rent_gross  = _D(ri and ri.gross_amount)
    int_amt     = _D(ii and ii.amount)
    tb_amt      = _D(tbs and tbs.gross_amount)
    div_taxable = _D(di and di.amount)
    div_exempt  = _D(di and di.exempt_amount)
    rr          = _D(rent_relief)
    total_inv   = rent_gross + int_amt + tb_amt   # dividends excluded — reported in Part III

    # Part I — Taxable investment income (rent + interest + T-Bills)
    els.append(_P('Part I : Investment income (Taxable)', st['sec_hdr']))
    els.append(Spacer(1, 2))
    hdr = [
        _P('S/N',                        st['tbl_hdr']),
        _P('Type of Investment Income',  st['tbl_hdr_l']),
        _P('Gains and Profits (Rs.)',    st['tbl_hdr']),
    ]
    rows = []
    sno = 1
    if rent_gross > 0:
        rows.append([_P(str(sno), st['tbl_cell']),
                     _P('Rent income (gross)', st['tbl_cell']),
                     _P(_fmt(rent_gross), st['tbl_cell_r'])])
        sno += 1
    if int_amt > 0:
        rows.append([_P(str(sno), st['tbl_cell']),
                     _P('Interest income (FDs / savings)', st['tbl_cell']),
                     _P(_fmt(int_amt), st['tbl_cell_r'])])
        sno += 1
    if tb_amt > 0:
        rows.append([_P(str(sno), st['tbl_cell']),
                     _P('T-Bills & Securities income', st['tbl_cell']),
                     _P(_fmt(tb_amt), st['tbl_cell_r'])])
    cw = [UW*0.06, UW*0.64, UW*0.30]
    els.append(KeepTogether(_sched_table(hdr, rows, cw, st)))
    els.append(Spacer(1, 3))

    els.append(_cage_tbl([
        _cr(st, 'Total investment income (Rs.)',          315, total_inv, bold=True),
        _cr(st, 'Rent relief — 25% of gross rent (Rs.)',  316, rr),
    ]))
    els.append(Spacer(1, 3))

    # Part III — Dividend income
    els.append(_P('Part III : Dividend income', st['sec_hdr']))
    els.append(Spacer(1, 2))
    hdr3 = [
        _P('S/N',              st['tbl_hdr']),
        _P('Type',             st['tbl_hdr_l']),
        _P('Amount (Rs.)',     st['tbl_hdr']),
    ]
    rows3 = []
    if div_taxable > 0:
        rows3.append([_P('1', st['tbl_cell']),
                      _P('Dividend income (taxable)', st['tbl_cell']),
                      _P(_fmt(div_taxable), st['tbl_cell_r'])])
    if div_exempt > 0:
        rows3.append([_P(str(len(rows3) + 1), st['tbl_cell']),
                      _P('Dividend income (exempt)', st['tbl_cell']),
                      _P(_fmt(div_exempt), st['tbl_cell_r'])])
    cw3 = [UW*0.06, UW*0.64, UW*0.30]
    els.append(KeepTogether(_sched_table(hdr3, rows3, cw3, st)))
    els.append(Spacer(1, 3))
    els.append(_cage_tbl([
        _cr(st, 'Exempt dividend income (Rs.)', 317, div_exempt),
    ]))
    els.append(Spacer(1, 5))


# ── Schedule 4 — Other Income ─────────────────────────────────────────────────
def _add_schedule_4(els, st, oi, fi):
    _sec(els, st, 'Schedule 4 - Other income')

    local_oi   = _D(oi and oi.amount)
    foreign_oi = _D(fi and fi.other_foreign_income)
    total      = local_oi + foreign_oi

    hdr = [
        _P('S/N',          st['tbl_hdr']),
        _P('Description',  st['tbl_hdr_l']),
        _P('Amount (Rs.)', st['tbl_hdr']),
    ]
    rows = []
    if local_oi > 0:
        rows.append([_P('1', st['tbl_cell']),
                     _P((oi and oi.description) or 'Other income', st['tbl_cell']),
                     _P(_fmt(local_oi), st['tbl_cell_r'])])
    if foreign_oi > 0:
        rows.append([_P(str(len(rows) + 1), st['tbl_cell']),
                     _P('Foreign other income', st['tbl_cell']),
                     _P(_fmt(foreign_oi), st['tbl_cell_r'])])
    cw = [UW*0.07, UW*0.68, UW*0.25]
    els.append(KeepTogether(_sched_table(hdr, rows, cw, st)))
    els.append(Spacer(1, 3))
    els.append(_cage_tbl([_cr(st, 'Total other income (Rs.)', 403, total, bold=True)]))
    els.append(Spacer(1, 5))


# ── Schedule 5 — Qualifying Payments ─────────────────────────────────────────
def _add_schedule_5(els, st, qp, cage_100):
    _sec(els, st, 'Schedule 5A - Qualifying payments (donations)')

    charitable = _D(qp and qp.donation_charitable)
    govt       = _D(qp and qp.donation_government)

    hdr = [
        _P('S/N',           st['tbl_hdr']),
        _P('Name of Institution', st['tbl_hdr_l']),
        _P('Type',          st['tbl_hdr']),
        _P('Amount Paid (Rs.)', st['tbl_hdr']),
    ]
    rows = []
    if charitable > 0:
        rows.append([_P('1', st['tbl_cell']),
                     _P('Approved charitable institution', st['tbl_cell']),
                     _P('Charitable', st['tbl_cell']),
                     _P(_fmt(charitable), st['tbl_cell_r'])])
    if govt > 0:
        rows.append([_P(str(len(rows) + 1), st['tbl_cell']),
                     _P('Government of Sri Lanka', st['tbl_cell']),
                     _P('Government', st['tbl_cell']),
                     _P(_fmt(govt), st['tbl_cell_r'])])
    cw = [UW*0.06, UW*0.44, UW*0.18, UW*0.32]
    els.append(KeepTogether(_sched_table(hdr, rows, cw, st)))
    els.append(Spacer(1, 3))
    els.append(_cage_tbl([_cr(st, 'Total qualifying payments (Rs.)', 504, cage_100, bold=True)]))
    els.append(Spacer(1, 4))

    # Schedule 5C — Solar panels
    _sec(els, st, 'Schedule 5C - Solar panel expenditure')
    solar_exp   = _D(qp and qp.solar_panels_expenditure)
    solar_allow = _D(qp and qp.solar_allowed) if qp else Decimal('0')

    els.append(_cage_tbl([
        _cr(st, 'Solar panel expenditure incurred (Rs.)',    511, solar_exp),
        _cr(st, 'Allowable deduction (max Rs. 600,000) (Rs.)', 512, solar_allow, bold=True),
        _cr(st, 'Balance to carry forward (Rs.)',            513, max(Decimal('0'), solar_exp - solar_allow)),
    ]))
    els.append(Spacer(1, 5))


# ── Schedule 6 — Final WHT (Dividend) ────────────────────────────────────────
def _add_schedule_6(els, st, di, wht_certs):
    _sec(els, st, 'Schedule 6A - WHT deducted as a final payment')

    hdr = [
        _P('S/N',               st['tbl_hdr']),
        _P('Type',              st['tbl_hdr_l']),
        _P('TIN of Withholder', st['tbl_hdr']),
        _P('Cert. No.',         st['tbl_hdr']),
        _P('Amount Received (Rs.)', st['tbl_hdr']),
        _P('WHT Paid (Rs.)',    st['tbl_hdr']),
        _P('Date',              st['tbl_hdr']),
    ]
    rows = []
    total_wht = Decimal('0')
    sno = 1
    # Dividend WHT (final)
    div_exempt = _D(di and di.exempt_amount)
    if div_exempt > 0:
        wht_on_div = (div_exempt * Decimal('15') / Decimal('85')).quantize(Decimal('0.01'))
        rows.append([
            _P(str(sno), st['tbl_cell']),
            _P('Dividend (15% WHT final)', st['tbl_cell']),
            _P('', st['tbl_cell']),
            _P('', st['tbl_cell']),
            _P(_fmt(div_exempt), st['tbl_cell_r']),
            _P(_fmt(wht_on_div), st['tbl_cell_r']),
            _P('', st['tbl_cell']),
        ])
        total_wht += wht_on_div
        sno += 1
    # Other WHT certs marked as final
    for cert in wht_certs:
        if cert.category == 'other':
            rows.append([
                _P(str(sno), st['tbl_cell']),
                _P('Other (final WHT)', st['tbl_cell']),
                _P('', st['tbl_cell']),
                _P('', st['tbl_cell']),
                _P('', st['tbl_cell']),
                _P(_fmt(cert.amount), st['tbl_cell_r']),
                _P('', st['tbl_cell']),
            ])
            total_wht += _D(cert.amount)
            sno += 1
    cw = [UW*0.05, UW*0.22, UW*0.15, UW*0.10, UW*0.18, UW*0.15, UW*0.15]
    els.append(KeepTogether(_sched_table(hdr, rows, cw, st)))
    els.append(Spacer(1, 3))
    els.append(_cage_tbl([_cr(st, 'Total WHT deducted as final payment (Rs.)', 605, total_wht, bold=True)]))
    els.append(Spacer(1, 5))


# ── Schedule 7 — AIT / WHT ───────────────────────────────────────────────────
def _add_schedule_7(els, st, ri, ii, tc, wht_certs):
    _sec(els, st, 'Schedule 7A - AIT / WHT deducted at source (not final)')

    int_wht  = _D(ii and ii.wht_deducted)
    rent_wht = _D(ri and ri.wht_deducted)

    cert_int = cert_rent = cert_svc = Decimal('0')
    for cert in wht_certs:
        if cert.category == 'interest':
            cert_int  += _D(cert.amount)
        elif cert.category == 'rent':
            cert_rent += _D(cert.amount)
        elif cert.category == 'service_fees':
            cert_svc  += _D(cert.amount)

    total_int_wht  = int_wht  + cert_int
    total_rent_wht = rent_wht + cert_rent
    total_ait      = total_int_wht + total_rent_wht + cert_svc
    # No prior-year carried-forward balance is tracked, so the full amount deducted
    # this year is available for set-off (mirrors calculate_full_tax's total_credits).
    credit_used    = total_ait

    hdr = [
        _P('S/N',                   st['tbl_hdr']),
        _P('Type',                  st['tbl_hdr_l']),
        _P('TIN of Withholder',     st['tbl_hdr']),
        _P('Cert. No.',             st['tbl_hdr']),
        _P('Amount Received (Rs.)', st['tbl_hdr']),
        _P('AIT/WHT Paid (Rs.)',    st['tbl_hdr']),
        _P('Date',                  st['tbl_hdr']),
    ]
    rows = []
    sno = 1
    for label, amt in [
        ('Interest income WHT',   total_int_wht),
        ('Rent income WHT',       total_rent_wht),
        ('Service fees WHT',      cert_svc),
    ]:
        if amt > 0:
            rows.append([
                _P(str(sno), st['tbl_cell']),
                _P(label, st['tbl_cell']),
                _P('', st['tbl_cell']),
                _P('', st['tbl_cell']),
                _P('', st['tbl_cell']),
                _P(_fmt(amt), st['tbl_cell_r']),
                _P('', st['tbl_cell']),
            ])
            sno += 1
    cw = [UW*0.05, UW*0.22, UW*0.15, UW*0.10, UW*0.18, UW*0.15, UW*0.15]
    els.append(KeepTogether(_sched_table(hdr, rows, cw, st)))
    els.append(Spacer(1, 3))
    els.append(_cage_tbl([
        _cr(st, 'Total AIT/WHT deducted at source (Rs.)',       707, total_ait),
        _cr(st, 'Add : Balance brought forward from last Y/A (Rs.)', 708, Decimal('0')),
        _cr(st, 'Total (Rs.)',                                  709, total_ait, bold=True),
        _cr(st, 'Less : AIT/WHT set off against tax payable (Rs.)', 710, credit_used),
        _cr(st, 'Balance to carry forward (Rs.)',               711, max(Decimal('0'), total_ait - credit_used)),
    ]))
    els.append(Spacer(1, 5))


# ── Schedule 8 — Tax Calculation ─────────────────────────────────────────────
def _add_schedule_8(els, st, submission):
    _sec(els, st, 'Schedule 8 - Tax computation')

    taxable   = _D(submission.net_taxable_income)
    gross_tax = _D(submission.gross_tax)

    # foreign_income_tax is already net of foreign_tax_paid credit (tax_calculator.py,
    # slabs capped at 15% with personal relief spillover applied to foreign income first).
    ftax_net = _D(submission.foreign_income_tax)

    # 809.A.1 = local (non-foreign) taxable income taxed at progressive slab rates
    slab_taxable = sum(_D(row.get('taxable_amount', 0)) for row in (submission.slab_breakdown or []))
    # 809.B.1 = foreign taxable income (net of personal relief), taxed at slab rates capped at 15%
    foreign_taxable = max(Decimal('0'), taxable - slab_taxable)

    def _rate_tbl(rows):
        """6-col table: label | cage.1 | amt.1 | rate | cage.3 | amt.3"""
        cw = [UW*0.36, UW*0.08, UW*0.18, UW*0.07, UW*0.08, UW*0.23]
        t = Table(rows, colWidths=cw)
        t.setStyle(TableStyle([
            ('GRID',          (1, 0), (2, -1), 0.5, MG),
            ('GRID',          (4, 0), (5, -1), 0.5, MG),
            ('TOPPADDING',    (0, 0), (-1, -1), 2),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
            ('LEFTPADDING',   (0, 0), (-1, -1), 4),
            ('RIGHTPADDING',  (0, 0), (-1, -1), 4),
            ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        return t

    def _rr(label, c1, v1, rate, c3, v3):
        return [
            _P(label, st['cage_lbl']),
            _P(str(c1) if c1 else '', st['cage_num']),
            _P(_fmt(v1), st['cage_amt']),
            _P(rate if rate else '', st['cage_num']),
            _P(str(c3) if c3 else '', st['cage_num']),
            _P(_fmt(v3), st['cage_amt']),
        ]

    # A.
    els.append(_P('A.   Enter taxable income from cage 120 of the Return', st['sec_hdr']))
    els.append(Spacer(1, 2))
    els.append(_cage_tbl([_cr(st, 'Taxable income (Rs.)', 801, taxable, bold=True)]))
    els.append(Spacer(1, 4))

    # B. Terminal benefits
    els.append(_P('B.   Total terminal benefits from cage 110 of schedule 1', st['sec_hdr']))
    els.append(Spacer(1, 2))
    els.append(_cage_tbl([_cr(st, 'Total terminal benefits (Rs.)', 802, Decimal('0'))]))
    els.append(Spacer(1, 2))
    els.append(_rate_tbl([
        _rr('Terminal benefits under special rate', '803a.1', Decimal('0'), '0%',  '803a.3', Decimal('0')),
        _rr('',                                     '803b.1', Decimal('0'), '6%',  '803b.3', Decimal('0')),
        _rr('',                                     '803c.1', Decimal('0'), '12%', '803c.3', Decimal('0')),
        _rr('Terminal benefits under normal rate',  '804.1',  Decimal('0'), '',    '804.3',  Decimal('0')),
    ]))
    els.append(Spacer(1, 2))
    els.append(_cage_tbl([
        _cr(st, 'Total tax on terminal benefits (803a.3+803b.3+803c.3+804.3) (Rs.)', 805, Decimal('0')),
    ]))
    els.append(Spacer(1, 4))

    # C.
    els.append(_P('C.   Tax on gain on realization of investment assets from schedule 3', st['sec_hdr']))
    els.append(Spacer(1, 2))
    els.append(_rate_tbl([
        _rr('', '806.1', Decimal('0'), '10%', '806.3', Decimal('0')),
    ]))
    els.append(Spacer(1, 4))

    # D.
    els.append(_P('D.   Tax on gain on realization of investment assets from partnership from schedule 3', st['sec_hdr']))
    els.append(Spacer(1, 2))
    els.append(_rate_tbl([
        _rr('', '807.1', Decimal('0'), '10%', '807.3', Decimal('0')),
    ]))
    els.append(Spacer(1, 4))

    # E.
    els.append(_P('E.   Tax on taxable income', st['sec_hdr']))
    els.append(Spacer(1, 2))
    els.append(_rate_tbl([
        _rr('Tax on taxable income from betting & gaming, manufacture & sale or import and sale of any liquor, tobacco product',
            '808.1', Decimal('0'), '40%', '808.3', Decimal('0')),
        _rr('Tax on Taxable Income to be taxed at progressive Income Tax Rates',
            '809.A.1', slab_taxable, '', '809.A.3', gross_tax),
        _rr('Foreign income taxed at progressive rates (capped at 15%)',
            '809.B.1', foreign_taxable, 'max 15%', '809.B.3', ftax_net),
    ]))
    els.append(Spacer(1, 2))
    els.append(_cage_tbl([
        _cr(st, 'Tax on total taxable income (808.3 + 809.A.3 + 809.B.3) (Rs.)', 810, gross_tax + ftax_net, bold=True),
    ]))
    els.append(Spacer(1, 4))

    # F.
    els.append(_P('F.   Tax on final withholding payments (WHT not deducted)(Cage 614 of Schedule 6)', st['sec_hdr']))
    els.append(Spacer(1, 2))
    els.append(_cage_tbl([
        _cr(st, 'Tax on final withholding payments (WHT not deducted) (Rs.)', 811, Decimal('0')),
    ]))
    els.append(Spacer(1, 5))


# ── Schedule 9 — Tax Credits ─────────────────────────────────────────────────
def _add_schedule_9(els, st, submission, tc):
    _sec(els, st, 'Schedule 9 - Tax credits')

    apit          = _D(tc and tc.apit_on_salary)
    partner_tc    = _D(tc and tc.partnership_tax_credit)
    foreign_tax_paid = _D(getattr(getattr(submission, 'foreign_income', None), 'foreign_tax_paid', None))
    sap_total     = sum(_D(s.amount) for s in submission.self_assessment_payments.all())
    total_credits = _D(submission.total_tax_credits)
    # Advance income tax / WHT credit = total credits less the other line items below
    # (rent/interest/business/TB-securities WHT plus WHT certificates), so the printed
    # rows always sum to the Total Tax Credits figure above.
    wht_ait       = max(Decimal('0'), total_credits - apit - partner_tc - sap_total)

    els.append(_cage_tbl([
        _cr(st, 'APIT on employment income — T10 certificate (Rs.)',  '903A', apit),
        _cr(st, 'Foreign tax paid / WHT credit (Cage 901) (Rs.)',      901,  foreign_tax_paid),
        _cr(st, 'Advance income tax credit — Enter amount in Cage 710 of Schedule 7A (Rs.)', 908, wht_ait),
        _cr(st, 'Partnership tax credit (Rs.)',                        909,  partner_tc),
        _cr(st, 'Total installment payments (from Schedule 9B) (Rs.)', 911,  sap_total),
        _cr(st, 'Total tax credits (Rs.)',                             912,  total_credits, bold=True),
    ]))
    els.append(Spacer(1, 5))


# ── Schedule 9B — Installment / Self-Assessment Payments ─────────────────────
def _add_schedule_9b(els, st, submission):
    _sec(els, st, 'Schedule 9B - Self-assessment / installment payments')

    saps     = {s.installment_number: s for s in submission.self_assessment_payments.all()}
    cage_map = {1: '927b', 2: '928b', 3: '929b', 4: '930b'}
    suffixes = {1: 'st', 2: 'nd', 3: 'rd', 4: 'th'}

    hdr = [
        _P('Installment',    st['tbl_hdr']),
        _P('Cage No.',       st['tbl_hdr']),
        _P('Payment Date',   st['tbl_hdr']),
        _P('Amount (Rs.)',   st['tbl_hdr']),
    ]
    rows = []
    total_sap = Decimal('0')
    for num in (1, 2, 3, 4):
        sap = saps.get(num)
        amt = _D(sap and sap.amount)
        dt  = str(sap.payment_date) if sap and sap.payment_date else '—'
        rows.append([
            _P(f'{num}{suffixes[num]} installment', st['tbl_cell']),
            _P(cage_map[num], st['tbl_cell']),
            _P(dt, st['tbl_cell']),
            _P(_fmt(amt), st['tbl_cell_r']),
        ])
        total_sap += amt
    rows.append([
        _P('Final payment', st['tbl_cell']),
        _P('931b', st['tbl_cell']),
        _P('—', st['tbl_cell']),
        _P(_fmt(Decimal('0')), st['tbl_cell_r']),
    ])
    cw = [UW*0.32, UW*0.14, UW*0.24, UW*0.30]
    els.append(KeepTogether(_sched_table(hdr, rows, cw, st)))
    els.append(Spacer(1, 3))
    els.append(_cage_tbl([
        _cr(st, 'Total self-assessment payments (Rs.)',  932, total_sap, bold=True),
        _cr(st, 'AIT/WHT paid by withholdee — Enter amount in Cage 614 of Schedule 6B and 721 of Schedule 7B (Rs.)', 933, Decimal('0')),
        _cr(st, 'Total tax paid (932+933) (Rs.)',        934, total_sap, bold=True),
    ]))
    els.append(Spacer(1, 5))


# ── Schedule 10 — Loss Adjustment ────────────────────────────────────────────
def _add_schedule_10(els, st):
    _sec(els, st, 'Schedule 10 - Loss adjustment')

    H  = st['tbl_hdr']
    HL = st['tbl_hdr_l']
    S  = st['tbl_cell']

    def _empty_loss_tbl(hdr, cw):
        return KeepTogether(_sched_table(hdr, [], cw, st))

    # Part IA — Business Losses (40% rate)
    els.append(_P('Part IA - Business Losses (Applicable tax rate for profit - 40%)', st['sec_hdr']))
    els.append(Spacer(1, 2))
    hdr_ia = [
        _P('S/N', H), _P('Y/A', H),
        _P('Loss (Rs.) (B/F & current year)', H),
        _P('Business Income (Rs.) 40% Rate', H),
        _P('Business Income (Rs.) Progressive Rates', H),
        _P('Investment Income (Rs.)', H),
        _P('Capital Gain (Rs.)', H),
        _P('Exempt Income (Rs.)', H),
        _P('Total Deduction (Rs.)', H),
        _P('C/F Loss (Rs.)', H),
    ]
    cw_ia = [UW*0.04, UW*0.08, UW*0.11, UW*0.10, UW*0.10, UW*0.10, UW*0.10, UW*0.10, UW*0.11, UW*0.16]
    els.append(_empty_loss_tbl(hdr_ia, cw_ia))
    els.append(Spacer(1, 4))

    # Part IB — Business Losses (Progressive)
    els.append(_P('Part IB - Business Losses (Applicable tax rate for profit - Progressive)', st['sec_hdr']))
    els.append(Spacer(1, 2))
    hdr_ib = [
        _P('S/N', H), _P('Y/A', H),
        _P('Loss (Rs.) (B/F & current year)', H),
        _P('Business Income (Rs.) Progressive Rates', H),
        _P('Investment Income (Rs.)', H),
        _P('Capital Gain (Rs.)', H),
        _P('Exempt Income (Rs.)', H),
        _P('Total Deduction Progressive (Rs.)', H),
        _P('C/F Loss (Rs.)', H),
    ]
    cw_ib = [UW*0.04, UW*0.08, UW*0.12, UW*0.13, UW*0.12, UW*0.12, UW*0.12, UW*0.13, UW*0.14]
    els.append(_empty_loss_tbl(hdr_ib, cw_ib))
    els.append(Spacer(1, 4))

    # Part II — Investment Losses
    els.append(_P('Part II - Investment Losses', st['sec_hdr']))
    els.append(Spacer(1, 2))
    hdr_ii = [
        _P('S/N', H), _P('Y/A', H),
        _P('Loss (Rs.) (B/F & current year)', H),
        _P('Investment Income (Rs.)', H),
        _P('Capital Gain (Rs.)', H),
        _P('Exempt Income (Rs.)', H),
        _P('Total Deduction (Rs.)', H),
        _P('C/F Loss (Rs.)', H),
    ]
    cw_ii = [UW*0.05, UW*0.09, UW*0.15, UW*0.15, UW*0.14, UW*0.14, UW*0.14, UW*0.14]
    els.append(_empty_loss_tbl(hdr_ii, cw_ii))
    els.append(Spacer(1, 4))

    # Part III — Exempt Losses
    els.append(_P('Part III - Exempt Losses', st['sec_hdr']))
    els.append(Spacer(1, 2))
    hdr_iii = [
        _P('S/N', H), _P('Y/A', H),
        _P('Loss (Rs.) (B/F & current year)', H),
        _P('Exempt Income (Rs.)', H),
        _P('Total Deduction (Rs.)', H),
        _P('C/F Loss (Rs.)', H),
    ]
    cw_iii = [UW*0.06, UW*0.10, UW*0.21, UW*0.21, UW*0.21, UW*0.21]
    els.append(_empty_loss_tbl(hdr_iii, cw_iii))
    els.append(Spacer(1, 5))


# ── Tax Computation Summary ───────────────────────────────────────────────────
def _add_tax_computation_summary(els, st, submission):
    """Client-facing tax computation summary — shown first in the PDF."""
    lei  = getattr(submission, 'local_employment',    None)
    fi   = getattr(submission, 'foreign_income',      None)
    tb   = getattr(submission, 'terminal_benefit',    None)
    ri   = getattr(submission, 'rent_income',         None)
    ii   = getattr(submission, 'interest_income',     None)
    di   = getattr(submission, 'dividend_income',     None)
    sole_props = list(submission.sole_proprietorships.all())
    sole_prop_total = sum(sp.amount or Decimal('0') for sp in sole_props)
    oi   = getattr(submission, 'other_income',        None)
    qp   = getattr(submission, 'qualifying_payments', None)
    tc   = getattr(submission, 'tax_credits',         None)
    dd   = getattr(submission, 'declarant_details',   None)

    S   = st['tbl_cell']
    SR  = st['tbl_cell_r']
    SB  = st['tbl_cell_b']
    SBR = st['tbl_cell_rb']
    H   = st['tbl_hdr']
    HL  = st['tbl_hdr_l']

    # ── Title ──────────────────────────────────────────────────────────────────
    year = submission.tax_year.label if submission.tax_year else ''
    els.append(_P(f'Tax Computation Summary — Y/A {year}', st['form_title']))
    els.append(HRFlowable(width='100%', thickness=1, color=BK, spaceAfter=4, spaceBefore=2))

    # Client info row
    name = (dd.full_name if dd else '') or ''
    tin  = (dd.tin       if dd else '') or ''
    IL, IV = st['info_lbl'], st['info_val']
    info = [
        [_P('Name', IL), _P(name or '—', IV), _P('TIN', IL), _P(tin or '—', IV)],
        [_P('Year of Assessment', IL), _P(year, IV), _P('', IL), _P('', IV)],
    ]
    t = Table(info, colWidths=[UW*0.18, UW*0.32, UW*0.18, UW*0.32])
    t.setStyle(TableStyle([
        ('GRID',          (0, 0), (-1, -1), 0.4, MG),
        ('TOPPADDING',    (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING',   (0, 0), (-1, -1), 5),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 5),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTSIZE',      (0, 0), (-1, -1), 8),
    ]))
    els.append(t)
    els.append(Spacer(1, 6))

    # ── Income Sources ─────────────────────────────────────────────────────────
    _sec(els, st, 'A.  Income Sources')
    inc_rows = []
    sno = 1
    def _inc(label, val):
        nonlocal sno
        if _D(val) > 0:
            inc_rows.append([_P(str(sno), S), _P(label, S), _P(_fmt(val), SR)])
            sno += 1

    _inc('Local Employment Income',                  lei and lei.amount)
    _inc('Foreign Employment / Service Fee',         fi  and fi.employment_service_fee)
    _inc('Foreign Business Income',                  fi  and fi.foreign_business_income)
    _inc('Foreign Other Income',                     fi  and fi.other_foreign_income)
    _inc('Terminal Benefit',                         tb  and tb.amount)
    _inc('Gross Rent Income',                        ri  and ri.gross_amount)
    _inc('Interest Income',                          ii  and ii.amount)
    _inc('Dividend Income (taxable)',                di  and di.amount)
    if sole_props:
        for sp in sole_props:
            _inc(f'Business Income ({sp.business_name or "Sole Proprietorship"})', sp.amount)
    else:
        _inc('Sole Proprietorship / Business Income', None)
    _inc('Other Income',                             oi  and oi.amount)

    tai = _D(submission.total_assessable_income)
    inc_rows.append([
        _P('', SB), _P('Total Assessable Income', SB), _P(_fmt(tai), SBR),
    ])

    # Exempt dividend (informational, shown green-ish via italic)
    exempt_div = _D(submission.exempt_dividend_income)
    if exempt_div > 0:
        inc_rows.append([
            _P('', S),
            _P('  Exempt Dividend Income (15% WHT — not in TAI)', st['small_gray']),
            _P(_fmt(exempt_div), st['small_gray']),
        ])

    # Total row is the last non-exempt row; find its index
    total_row_idx = len(inc_rows) - (2 if exempt_div > 0 else 1)
    inc_tbl = Table(inc_rows, colWidths=[UW*0.06, UW*0.66, UW*0.28])
    inc_tbl.setStyle(TableStyle([
        ('GRID',          (0, 0), (-1, -1), 0.4, MG),
        ('LINEABOVE',     (0, total_row_idx), (-1, total_row_idx), 0.8, BK),
        ('TOPPADDING',    (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('LEFTPADDING',   (0, 0), (-1, -1), 4),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 4),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTSIZE',      (0, 0), (-1, -1), 8),
    ]))
    els.append(KeepTogether(inc_tbl))
    els.append(Spacer(1, 4))

    # ── Deductions ─────────────────────────────────────────────────────────────
    _sec(els, st, 'B.  Qualifying Payments and Reliefs')

    rent_relief   = _D(submission.rent_relief)
    personal_rlf  = _D(submission.personal_relief)
    total_qp      = _D(submission.total_qualifying_payments)
    solar         = _D(qp and qp.solar_panels_expenditure) if qp else Decimal('0')
    don_char      = _D(qp and qp.donation_charitable)      if qp else Decimal('0')
    don_govt      = _D(qp and qp.donation_government)      if qp else Decimal('0')
    solar_allowed = min(solar, Decimal('600000'))

    ded_rows = []
    def _ded(label, val, bold=False):
        ded_rows.append([
            _P(label, SB if bold else S),
            _P(_fmt(val), SBR if bold else SR),
        ])

    if don_char > 0:   _ded(f'  Charitable Donations', don_char)
    if don_govt > 0:   _ded(f'  Government Donations', don_govt)
    if solar_allowed > 0: _ded(f'  Solar Panel Expenditure (max Rs. 600,000)', solar_allowed)
    if total_qp > 0:   _ded('Total Qualifying Payments', total_qp, bold=True)
    _ded('Personal Relief', personal_rlf)
    if rent_relief > 0: _ded('Rent Relief (25% of Gross Rent)', rent_relief)

    total_ded = total_qp + personal_rlf + rent_relief
    _ded('Total Deductions', total_ded, bold=True)

    ded_tbl = Table(ded_rows, colWidths=[UW*0.72, UW*0.28])
    ded_tbl.setStyle(TableStyle([
        ('GRID',          (0, 0), (-1, -1), 0.4, MG),
        ('TOPPADDING',    (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('LEFTPADDING',   (0, 0), (-1, -1), 4),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 4),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTSIZE',      (0, 0), (-1, -1), 8),
    ]))
    els.append(ded_tbl)
    els.append(Spacer(1, 4))

    # ── Foreign Income Tax (progressive slabs, capped at 15%) ─────────────────
    foreign_total = (
        _D(fi and fi.employment_service_fee) +
        _D(fi and fi.foreign_business_income) +
        _D(fi and fi.other_foreign_income)
    )
    foreign_tax_paid = _D(fi and fi.foreign_tax_paid)
    if foreign_total > 0:
        _sec(els, st, 'C.  Foreign Income Tax (progressive rates, capped at 15%)')
        # foreign_income_tax is already net of the foreign_tax_paid credit
        # (tax_calculator.py: personal relief applied to local income first, then
        # any balance to foreign income; foreign income taxed at slab rates capped at 15%).
        ftax_net   = _D(submission.foreign_income_tax)
        ftax_gross = ftax_net + foreign_tax_paid
        ft_rows = [
            [_P('Foreign Income', S),                     _P(_fmt(foreign_total), SR)],
            [_P('Tax (progressive, max 15%)', S),         _P(_fmt(ftax_gross), SR)],
        ]
        if foreign_tax_paid > 0:
            ft_rows.append([_P('Less: Foreign Tax Paid (Cage 901)', S), _P(f'({_fmt(foreign_tax_paid)})', SR)])
        ft_rows.append([_P('Net Foreign Income Tax', SB),               _P(_fmt(ftax_net), SBR)])
        ft_tbl = Table(ft_rows, colWidths=[UW*0.72, UW*0.28])
        ft_tbl.setStyle(TableStyle([
            ('GRID',          (0, 0), (-1, -1), 0.4, MG),
            ('LINEABOVE',     (0, -1), (-1, -1), 0.8, BK),
            ('TOPPADDING',    (0, 0), (-1, -1), 2),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
            ('LEFTPADDING',   (0, 0), (-1, -1), 4),
            ('RIGHTPADDING',  (0, 0), (-1, -1), 4),
            ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
            ('FONTSIZE',      (0, 0), (-1, -1), 8),
        ]))
        els.append(ft_tbl)
        els.append(Spacer(1, 4))
        sec_label = 'D.'
    else:
        ftax_net  = Decimal('0')
        sec_label = 'C.'

    # ── Progressive Tax Computation ────────────────────────────────────────────
    _sec(els, st, f'{sec_label}  Tax Computation on Taxable Income')
    net_taxable = _D(submission.net_taxable_income)
    gross_tax   = _D(submission.gross_tax)

    els.append(_cage_tbl([_cr(st, 'Net Taxable Income (Rs.)', 120, net_taxable, bold=True)]))
    els.append(Spacer(1, 3))

    # Slab breakdown
    slabs = submission.slab_breakdown or []
    if slabs:
        slab_hdr = [_P('Tax Band', HL), _P('Taxable Amount (Rs.)', H), _P('Tax (Rs.)', H)]
        slab_rows = [
            [_P(row.get('label', ''), S),
             _P(_fmt(row.get('taxable_amount', 0)), SR),
             _P(_fmt(row.get('tax', 0)), SR)]
            for row in slabs
        ]
        total_slab = sum(_D(r.get('tax', 0)) for r in slabs)
        slab_rows.append([_P('Gross Tax', SB), _P('', SBR), _P(_fmt(total_slab), SBR)])
        slab_tbl = Table([slab_hdr] + slab_rows, colWidths=[UW*0.52, UW*0.24, UW*0.24])
        slab_tbl.setStyle(TableStyle([
            ('GRID',          (0, 0), (-1, -1), 0.4, MG),
            ('BACKGROUND',    (0, 0), (-1, 0),  LG),
            ('LINEABOVE',     (0, -1), (-1, -1), 0.8, BK),
            ('TOPPADDING',    (0, 0), (-1, -1), 2),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
            ('LEFTPADDING',   (0, 0), (-1, -1), 4),
            ('RIGHTPADDING',  (0, 0), (-1, -1), 4),
            ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
            ('FONTSIZE',      (0, 0), (-1, -1), 8),
        ]))
        els.append(KeepTogether(slab_tbl))
    else:
        els.append(_cage_tbl([_cr(st, 'Gross Tax (Rs.)', 150, gross_tax, bold=True)]))
    els.append(Spacer(1, 4))

    # ── Tax Credits ────────────────────────────────────────────────────────────
    next_sec = chr(ord(sec_label[0]) + 1) + '.'
    _sec(els, st, f'{next_sec}  Tax Credits')

    apit         = _D(tc and tc.apit_on_salary)
    partner_tc   = _D(tc and tc.partnership_tax_credit)
    rent_wht     = _D(ri and ri.wht_deducted)
    interest_wht = _D(ii and ii.wht_deducted)
    sole_wht     = sum(_D(sp.wht_deducted) for sp in sole_props)
    tbs          = getattr(submission, 'tb_securities', None)
    tb_wht       = _D(tbs and tbs.wht_deducted)
    other_certs  = sum(
        _D(c.amount) for c in submission.wht_certificates.exclude(category__in=('rent', 'interest'))
    )
    sap_total    = sum(_D(s.amount) for s in submission.self_assessment_payments.all())
    total_credits = _D(submission.total_tax_credits)

    cr_rows = []
    def _cred(label, val):
        if _D(val) > 0:
            cr_rows.append([_P(label, S), _P(f'({_fmt(val)})', SR)])

    _cred('APIT on Salary',                       apit)
    _cred('WHT on Rent Income (deducted at source)', rent_wht)
    _cred('WHT on Interest Income (deducted at source)', interest_wht)
    _cred('WHT on Business Income (deducted at source)', sole_wht)
    _cred('WHT on T-Bills / Securities (deducted at source)', tb_wht)
    _cred('WHT Certificates (Service Fees / Employment / Other)', other_certs)
    _cred('Partnership Tax Credit',                partner_tc)
    _cred('Self-Assessment Installments',          sap_total)
    cr_rows.append([_P('Total Tax Credits', SB), _P(f'({_fmt(total_credits)})', SBR)])

    cr_tbl = Table(cr_rows, colWidths=[UW*0.72, UW*0.28])
    cr_tbl.setStyle(TableStyle([
        ('GRID',          (0, 0), (-1, -1), 0.4, MG),
        ('LINEABOVE',     (0, -1), (-1, -1), 0.8, BK),
        ('TOPPADDING',    (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('LEFTPADDING',   (0, 0), (-1, -1), 4),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 4),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTSIZE',      (0, 0), (-1, -1), 8),
    ]))
    els.append(cr_tbl)
    els.append(Spacer(1, 4))

    # ── Net Tax Payable ────────────────────────────────────────────────────────
    net_tax = _D(submission.net_tax_payable)
    npay_rows = [
        [_P('Gross Tax on Taxable Income', S),  _P(_fmt(gross_tax),   SR)],
        [_P('Net Foreign Income Tax (max 15%)', S), _P(_fmt(ftax_net),    SR)],
        [_P('Less: Total Tax Credits', S),       _P(f'({_fmt(total_credits)})', SR)],
        [_P('NET TAX PAYABLE (Rs.)', SB),        _P(_fmt(net_tax),     SBR)],
    ]
    npay_tbl = Table(npay_rows, colWidths=[UW*0.72, UW*0.28])
    npay_tbl.setStyle(TableStyle([
        ('GRID',          (0, 0), (-1, -1), 0.5, MG),
        ('BACKGROUND',    (0, -1), (-1, -1), LG),
        ('LINEABOVE',     (0, -1), (-1, -1), 1.0, BK),
        ('TOPPADDING',    (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING',   (0, 0), (-1, -1), 4),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 4),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTSIZE',      (0, 0), (-1, -1), 8.5),
    ]))
    els.append(KeepTogether(npay_tbl))
    els.append(Spacer(1, 5))


# ── Statement of Assets & Liabilities ────────────────────────────────────────
def _add_assets_liabilities(els, st, submission):
    _sec(els, st, 'Statement of assets and liabilities as at 31st March')
    els.append(Spacer(1, 3))

    S = st['tbl_cell']
    SR = st['tbl_cell_r']
    SB = st['tbl_cell_b']
    SBR = st['tbl_cell_rb']
    H  = st['tbl_hdr']
    HL = st['tbl_hdr_l']

    # Immovable Properties
    props = submission.immovable_properties.all()
    if props.exists():
        hdr = [_P('S/N', H), _P('Situation of Property / Location', HL),
               _P('Date Acquired', H), _P('Cost (Rs.)', H), _P('Market Value (Rs.)', H)]
        rows = [[_P(str(i+1), S), _P(p.situation_of_property or '—', S),
                 _P(str(p.date_of_acquisition or '—'), S),
                 _P(_fmt(p.cost), SR), _P(_fmt(p.market_value), SR)]
                for i, p in enumerate(props)]
        cw = [UW*0.05, UW*0.38, UW*0.17, UW*0.20, UW*0.20]
        els.append(KeepTogether(_sched_table(hdr, rows, cw, st)))
        els.append(Spacer(1, 3))

    # Motor Vehicles
    vehicles = submission.motor_vehicles.all()
    if vehicles.exists():
        hdr = [_P('S/N', H), _P('Description', HL), _P('Reg. No.', H),
               _P('Date Acquired', H), _P('Cost / Market Value (Rs.)', H)]
        rows = [[_P(str(i+1), S), _P(v.description or '—', S), _P(v.registration_no or '—', S),
                 _P(str(v.date_of_acquisition or '—'), S), _P(_fmt(v.cost_market_value), SR)]
                for i, v in enumerate(vehicles)]
        cw = [UW*0.05, UW*0.35, UW*0.18, UW*0.17, UW*0.25]
        els.append(KeepTogether(_sched_table(hdr, rows, cw, st)))
        els.append(Spacer(1, 3))

    # Bank Balances
    banks = submission.bank_balances.all()
    if banks.exists():
        hdr = [_P('S/N', H), _P('Bank Name', HL), _P('Account No.', H),
               _P('Amount Invested (Rs.)', H), _P('Interest (Rs.)', H), _P('Balance (Rs.)', H)]
        rows = [[_P(str(i+1), S), _P(b.bank_name or '—', S), _P(b.account_no or '—', S),
                 _P(_fmt(b.amount_invested), SR), _P(_fmt(b.interest), SR), _P(_fmt(b.balance), SR)]
                for i, b in enumerate(banks)]
        cw = [UW*0.05, UW*0.25, UW*0.20, UW*0.17, UW*0.15, UW*0.18]
        els.append(KeepTogether(_sched_table(hdr, rows, cw, st)))
        els.append(Spacer(1, 3))

    # Shares & Stocks
    shares = submission.shares_stocks.all()
    if shares.exists():
        hdr = [_P('S/N', H), _P('Description', HL), _P('No. of Shares', H),
               _P('Cost / Market Value (Rs.)', H), _P('Net Dividend (Rs.)', H)]
        rows = [[_P(str(i+1), S), _P(s.description or '—', S), _P(str(s.no_of_shares or '—'), S),
                 _P(_fmt(s.cost_market_value), SR), _P(_fmt(s.net_dividend_income), SR)]
                for i, s in enumerate(shares)]
        cw = [UW*0.05, UW*0.38, UW*0.14, UW*0.23, UW*0.20]
        els.append(KeepTogether(_sched_table(hdr, rows, cw, st)))
        els.append(Spacer(1, 3))

    # Cash, Gold, Loans given (cage rows)
    cih  = getattr(submission, 'cash_in_hand',   None)
    gold = getattr(submission, 'gold_jewellery', None)
    lg   = getattr(submission, 'loans_given',    None)  # OneToOneField — single aggregate record

    cih_amt  = _D(cih  and cih.amount)
    gold_val = _D(gold and gold.value)
    lg_amt   = _D(lg   and lg.amount)

    if cih_amt > 0 or gold_val > 0 or lg_amt > 0:
        cage_rows = []
        if cih_amt > 0:
            cage_rows.append(_cr(st, 'Cash in hand (Rs.)', 1019, cih_amt))
        if lg_amt > 0:
            cage_rows.append(_cr(st, 'Loans given / receivable as at 31 March (Rs.)', 1020, lg_amt))
        if gold_val > 0:
            cage_rows.append(_cr(st,
                f'Gold / silver / jewellery — {gold.description or ""} (Rs.)', 1021, gold_val))
        if cage_rows:
            els.append(_cage_tbl(cage_rows))
            els.append(Spacer(1, 3))

    # Business Properties
    biz = submission.business_properties.all()
    if biz.exists():
        hdr = [_P('S/N', H), _P('Name of Business', HL),
               _P('Current Account Balance (Rs.)', H), _P('Capital Account Balance (Rs.)', H)]
        rows = [[_P(str(i+1), S), _P(b.name_of_business or '—', S),
                 _P(_fmt(b.current_account_balance), SR), _P(_fmt(b.capital_account_balance), SR)]
                for i, b in enumerate(biz)]
        cw = [UW*0.05, UW*0.43, UW*0.26, UW*0.26]
        els.append(KeepTogether(_sched_table(hdr, rows, cw, st)))
        els.append(Spacer(1, 3))

    # Other Assets
    others = submission.other_assets.all()
    if others.exists():
        hdr = [_P('S/N', H), _P('Description', HL), _P('Type', H),
               _P('Date Acquired', H), _P('Cost / Value (Rs.)', H)]
        rows = [[_P(str(i+1), S), _P(o.description or '—', S),
                 _P(o.get_acquisition_type_display(), S),
                 _P(str(o.date_of_acquisition or '—'), S), _P(_fmt(o.cost_value), SR)]
                for i, o in enumerate(others)]
        cw = [UW*0.05, UW*0.38, UW*0.15, UW*0.17, UW*0.25]
        els.append(KeepTogether(_sched_table(hdr, rows, cw, st)))
        els.append(Spacer(1, 3))

    # Disposals
    disposals = submission.disposals.all()
    if disposals.exists():
        hdr = [_P('S/N', H), _P('Description', HL), _P('Date Disposed', H),
               _P('Sale Proceeds (Rs.)', H), _P('Date Acquired', H), _P('Original Cost (Rs.)', H)]
        rows = [[_P(str(i+1), S), _P(d.description or '—', S), _P(str(d.date_of_disposal or '—'), S),
                 _P(_fmt(d.sales_proceed), SR), _P(str(d.date_acquired or '—'), S), _P(_fmt(d.cost), SR)]
                for i, d in enumerate(disposals)]
        cw = [UW*0.05, UW*0.28, UW*0.14, UW*0.17, UW*0.14, UW*0.22]
        els.append(KeepTogether(_sched_table(hdr, rows, cw, st)))
        els.append(Spacer(1, 3))

    # Liabilities
    liabs = submission.liabilities.all()
    if liabs.exists():
        hdr = [_P('S/N', H), _P('Description', HL), _P('Security', H),
               _P('Date Commenced', H), _P('Original Amt (Rs.)', H),
               _P('Balance (Rs.)', H), _P('Repaid Y/A (Rs.)', H)]
        rows = [[_P(str(i+1), S), _P(l.description or '—', S),
                 _P(l.security_on_liability or '—', S),
                 _P(str(l.date_of_commencement or '—'), S),
                 _P(_fmt(l.original_amount), SR),
                 _P(_fmt(l.amount_as_at_date), SR),
                 _P(_fmt(l.amount_repaid_during_year), SR)]
                for i, l in enumerate(liabs)]
        cw = [UW*0.05, UW*0.24, UW*0.15, UW*0.13, UW*0.15, UW*0.14, UW*0.14]
        els.append(KeepTogether(_sched_table(hdr, rows, cw, st)))
        els.append(Spacer(1, 3))


# ── Cash Flow / Receipts & Payments ──────────────────────────────────────────
def _add_cash_flow(els, st, submission):
    cf = getattr(submission, 'cash_flow', None)
    if not cf:
        return

    _sec(els, st, 'Receipts and payments statement')
    els.append(Spacer(1, 3))

    def _bt(entries):
        return sum(_D(r.get('amount', 0)) for r in (entries or []))

    opening_fav = _bt(cf.opening_favourable_banks)
    opening_od  = _bt(cf.opening_overdraft_banks)
    opening_ttl = _D(cf.opening_cash_in_hand) + opening_fav - opening_od

    receipts = sum([
        _D(cf.receipt_employment_income),  _D(cf.receipt_interest_fds),
        _D(cf.receipt_interest_savings),   _D(cf.receipt_rent_income),
        _D(cf.receipt_tb_securities),      _D(cf.receipt_sale_shares),
        _D(cf.receipt_dividend_income),    _D(cf.receipt_drawings_sole_partner),
        _D(cf.receipt_bank_loan),          _D(cf.receipt_other_loans),
        _D(cf.receipt_sale_land_building), _D(cf.receipt_sale_motor_vehicle),
        _D(cf.receipt_sale_other_assets),
    ] + [_D(r.get('amount', 0)) for r in (cf.receipt_other_items or [])])
    payments = sum([
        _D(cf.payment_purchase_land_building), _D(cf.payment_purchase_motor_vehicle),
        _D(cf.payment_purchase_other_assets),  _D(cf.payment_repayment_bank_loan),
        _D(cf.payment_lease_rentals),          _D(cf.payment_jewellery_gems),
        _D(cf.payment_other_loans),            _D(cf.payment_wht),
        _D(cf.payment_income_tax),             _D(cf.payment_apit),
        _D(cf.payment_investment_shares),      _D(cf.payment_loans_given_others),
    ] + [_D(r.get('amount', 0)) for r in (cf.payment_other_items or [])])
    closing_fav = _bt(cf.closing_favourable_banks)
    closing_od  = _bt(cf.closing_overdraft_banks)
    closing_ttl = _D(cf.closing_cash_in_hand) + closing_fav - closing_od

    living_yr  = _D(cf.living_expenses_year)
    living_mo  = (living_yr / 12).quantize(Decimal('0.01')) if living_yr else Decimal('0')

    CW2 = [UW * 0.72, UW * 0.28]
    S   = st['tbl_cell']
    SR  = st['tbl_cell_r']
    SB  = st['tbl_cell_b']
    SBR = st['tbl_cell_rb']

    def _row(label, val, bold=False):
        return [_P(label, SB if bold else S), _P(_fmt(val), SBR if bold else SR)]

    cf_rows = [
        [_P('Description', st['tbl_hdr_l']), _P('Amount (Rs.)', st['tbl_hdr'])],
        _row('Cash in hand — opening (1st April)',            cf.opening_cash_in_hand),
        _row('Cash at bank — favourable balances (opening)',  opening_fav),
        _row('Less : Cash at bank — overdraft (opening)',     opening_od),
        _row('Opening cash balance (1st April)',              opening_ttl,  bold=True),
        _row('Employment income received',                    cf.receipt_employment_income),
        _row('Interest income on fixed deposits',             cf.receipt_interest_fds),
        _row('Interest income on savings accounts',           cf.receipt_interest_savings),
        _row('Rent income received',                          cf.receipt_rent_income),
        _row('Income on sale of T/B and securities',          cf.receipt_tb_securities),
        _row('Sale of shares',                                cf.receipt_sale_shares),
        _row('Dividend income received',                      cf.receipt_dividend_income),
        _row('Drawings from sole / partnership businesses',   cf.receipt_drawings_sole_partner),
        _row('Bank loan received',                            cf.receipt_bank_loan),
        _row('Other loans received',                          cf.receipt_other_loans),
        _row('Sale of land or building',                      cf.receipt_sale_land_building),
        _row('Sale of motor vehicle',                         cf.receipt_sale_motor_vehicle),
        _row('Sale of other assets',                          cf.receipt_sale_other_assets),
    ] + [
        _row(r.get('description') or 'Other receipt', r.get('amount', 0))
        for r in (cf.receipt_other_items or [])
    ] + [
        _row('Total receipts',                                receipts, bold=True),
        _row('Purchase of land or building',                  cf.payment_purchase_land_building),
        _row('Purchase of motor vehicle',                     cf.payment_purchase_motor_vehicle),
        _row('Purchase of other assets',                      cf.payment_purchase_other_assets),
        _row('Repayment of bank loan',                        cf.payment_repayment_bank_loan),
        _row('Payment of lease rentals',                      cf.payment_lease_rentals),
        _row('Purchase of jewellery / gems / silver',         cf.payment_jewellery_gems),
        _row('Payment of other loans',                        cf.payment_other_loans),
        _row('WHT paid',                                      cf.payment_wht),
        _row('Income tax payments',                           cf.payment_income_tax),
        _row('APIT paid',                                     cf.payment_apit),
        _row('Investment in shares',                          cf.payment_investment_shares),
        _row('Loans given to others',                         cf.payment_loans_given_others),
    ] + [
        _row(r.get('description') or 'Other payment', r.get('amount', 0))
        for r in (cf.payment_other_items or [])
    ] + [
        _row('Total payments',                                payments, bold=True),
        _row('Cash in hand — closing (31st March)',           cf.closing_cash_in_hand),
        _row('Cash at bank — favourable balances (closing)',  closing_fav),
        _row('Less : Cash at bank — overdraft (closing)',     closing_od),
        _row('Closing cash balance (31st March)',             closing_ttl, bold=True),
        _row('Living expenses for the year (Rs.)',            living_yr),
        _row('Living expenses per month (÷ 12) (Rs.)',        living_mo),
    ]

    _n_ro = len(cf.receipt_other_items or [])
    _n_po = len(cf.payment_other_items  or [])
    total_row_idxs = [3, 18 + _n_ro, 31 + _n_ro + _n_po, 35 + _n_ro + _n_po]
    sty = [
        ('GRID',          (0, 0), (-1, -1), 0.4, MG),
        ('BACKGROUND',    (0, 0), (-1, 0),  LG),
        ('TOPPADDING',    (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('LEFTPADDING',   (0, 0), (-1, -1), 4),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 4),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTSIZE',      (0, 0), (-1, -1), 8),
    ]
    for ri in total_row_idxs:
        if ri < len(cf_rows):
            sty += [
                ('LINEABOVE', (0, ri), (-1, ri), 0.8, BK),
            ]
    t = Table(cf_rows, colWidths=CW2)
    t.setStyle(TableStyle(sty))
    els.append(t)
    els.append(Spacer(1, 5))


# ── Declarant Details ─────────────────────────────────────────────────────────
def _add_declarant_details(els, st, dd):
    _sec(els, st, 'Declaration')
    els.append(Spacer(1, 3))
    els.append(_P(
        'I declare that the particulars given in this return are true and complete '
        'to the best of my knowledge and belief.',
        st['decl'],
    ))
    els.append(Spacer(1, 8))

    if dd:
        IL = st['info_lbl']
        IV = st['info_val']
        info = [
            [_P('Full Name',     IL), _P(dd.full_name    or '—', IV),
             _P('TIN',           IL), _P(dd.tin          or '—', IV)],
            [_P('NIC / Passport',IL), _P(dd.nic_passport or '—', IV),
             _P('Telephone',     IL), _P(dd.telephone    or '—', IV)],
            [_P('Mobile',        IL), _P(dd.mobile       or '—', IV),
             _P('E-mail',        IL), _P(dd.email        or '—', IV)],
            [_P('Date',          IL), _P(datetime.now().strftime('%d %B %Y'), IV),
             '', ''],
        ]
        cw = [UW*0.18, UW*0.32, UW*0.18, UW*0.32]
        t = Table(info, colWidths=cw)
        t.setStyle(TableStyle([
            ('GRID',          (0, 0), (-1, -1), 0.4, MG),
            ('TOPPADDING',    (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ('LEFTPADDING',   (0, 0), (-1, -1), 5),
            ('RIGHTPADDING',  (0, 0), (-1, -1), 5),
            ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
            ('FONTSIZE',      (0, 0), (-1, -1), 8),
        ]))
        els.append(t)

    els.append(Spacer(1, 10))
    # Signature box
    sig = Table(
        [[_P('Signature:', st['small_gray']), ''],
         ['', ''],
         ['', '']],
        colWidths=[UW * 0.5, UW * 0.5],
    )
    sig.setStyle(TableStyle([
        ('BOX',           (0, 0), (0, -1), 0.8, BK),
        ('TOPPADDING',    (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING',   (0, 0), (-1, -1), 8),
        ('FONTSIZE',      (0, 0), (-1, -1), 8),
    ]))
    els.append(sig)


# ── Main entry point ──────────────────────────────────────────────────────────
def generate_tax_submission_pdf(submission, include_assets_liabilities=True) -> BytesIO:
    """
    PDF structure:
      1. Tax Computation Summary
      2. Statement of Assets & Liabilities
      3. Receipts & Payments (Cash Flow)
      4. IIT Return (Header + Parts A-D + Schedules 1-10 + Declaration)
    """
    buffer = BytesIO()
    sys_settings = _get_system_settings()

    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        rightMargin=RM, leftMargin=LM,
        topMargin=TM, bottomMargin=BM,
        title=f'Individual income tax - Confirmation — {submission.tax_year.label}',
    )

    st  = _build_styles()
    els = []

    # Related objects (shared across sections)
    lei       = getattr(submission, 'local_employment',    None)
    fi        = getattr(submission, 'foreign_income',      None)
    tb        = getattr(submission, 'terminal_benefit',    None)
    ri        = getattr(submission, 'rent_income',         None)
    ii        = getattr(submission, 'interest_income',     None)
    di        = getattr(submission, 'dividend_income',     None)
    sole_props_pdf = list(submission.sole_proprietorships.all())
    sole_prop_total_pdf = sum(sp.amount or Decimal('0') for sp in sole_props_pdf)
    oi        = getattr(submission, 'other_income',        None)
    tbs       = getattr(submission, 'tb_securities',       None)
    qp        = getattr(submission, 'qualifying_payments', None)
    tc        = getattr(submission, 'tax_credits',         None)
    dd        = getattr(submission, 'declarant_details',   None)
    wht_certs = submission.wht_certificates.all()

    # ── Section 1: Tax Computation Summary ────────────────────────────────────
    _add_tax_computation_summary(els, st, submission)

    # ── Section 2: Assets & Liabilities ───────────────────────────────────────
    els.append(PageBreak())
    _add_assets_liabilities(els, st, submission)

    # ── Section 3: Receipts & Payments (Cash Flow) ────────────────────────────
    if getattr(submission, 'cash_flow', None):
        els.append(PageBreak())
        _add_cash_flow(els, st, submission)

    # ── Section 4: IIT Return ─────────────────────────────────────────────────
    els.append(PageBreak())

    # Cage values for official return
    cage_10  = _D(lei and lei.amount) + _D(fi and fi.employment_service_fee)
    cage_20  = sole_prop_total_pdf + _D(fi and fi.foreign_business_income)
    cage_30  = _D(ri and ri.gross_amount) + _D(ii and ii.amount) + _D(di and di.amount) + _D(tbs and tbs.gross_amount)
    cage_40  = _D(oi and oi.amount) + _D(fi and fi.other_foreign_income)
    cage_50  = _D(submission.total_assessable_income)
    cage_60  = _D(submission.rent_relief)
    cage_70  = _D(qp.solar_allowed) if qp else Decimal('0')
    cage_80  = _D(submission.personal_relief)
    cage_90  = cage_60 + cage_70 + cage_80
    cage_100 = (_D(qp.donation_charitable) + _D(qp.donation_government)) if qp else Decimal('0')
    cage_110 = cage_90 + cage_100
    cage_120 = _D(submission.net_taxable_income)
    cage_130 = Decimal('0')
    cage_140 = Decimal('0')
    cage_150 = _D(submission.gross_tax)
    cage_160 = Decimal('0')
    cage_170 = cage_130 + cage_140 + cage_150 + cage_160
    cage_180 = _D(submission.total_tax_credits)
    cage_190 = _D(submission.net_tax_payable)
    cage_200 = max(Decimal('0'), cage_180 - cage_170)

    exempt_div = _D(submission.exempt_dividend_income)
    if exempt_div == 0 and di:
        exempt_div = _D(di.exempt_amount)
    cage_210 = exempt_div

    cages = {
        10: cage_10,  20: cage_20,  30: cage_30,   40: cage_40,   50: cage_50,
        60: cage_60,  70: cage_70,  80: cage_80,   90: cage_90,  100: cage_100,
        110: cage_110, 120: cage_120, 130: cage_130, 140: cage_140, 150: cage_150,
        160: cage_160, 170: cage_170, 180: cage_180, 190: cage_190, 200: cage_200,
        210: cage_210, '210A': Decimal('0'),
    }

    _add_header(els, st, submission, dd, sys_settings)
    _add_main_return(els, st, cages)

    _add_schedule_1(els, st, lei, fi, tb)
    _add_schedule_2(els, st, sole_props_pdf, fi)
    _add_schedule_3(els, st, ri, ii, di, cage_60, tbs)
    _add_schedule_4(els, st, oi, fi)
    _add_schedule_5(els, st, qp, cage_100)
    _add_schedule_6(els, st, di, wht_certs)
    _add_schedule_7(els, st, ri, ii, tc, wht_certs)
    _add_schedule_8(els, st, submission)
    _add_schedule_9(els, st, submission, tc)
    _add_schedule_9b(els, st, submission)
    _add_schedule_10(els, st)

    _add_declarant_details(els, st, dd)

    # Footer
    els.append(Spacer(1, 8))
    els.append(HRFlowable(width='100%', thickness=0.5, color=MG))
    company = (sys_settings.company_name if sys_settings else None) or 'Tax Automation Portal'
    footer  = (sys_settings.footer_text  if sys_settings else None) or 'Confidential'
    els.append(_P(
        f'Generated on {datetime.now().strftime("%d %B %Y  %H:%M")}  ·  {company}  ·  {footer}',
        st['footer'],
    ))

    doc.build(els)
    buffer.seek(0)
    return buffer
