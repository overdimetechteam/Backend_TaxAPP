from django.urls import path
from .views import (
    TaxYearListView, TaxSubmissionListCreateView, TaxSubmissionDetailView,
    SubmitTaxFormView, RequestInfoView, ConfirmCalculationView,
    ClientConfirmView, GeneratePDFView, ConsultantUpdateCalculationView,
    ArchiveTreeView, SubmissionEditLogsView,
    AccountsQueueView, FinalSubmitView, ClientFinalConfirmView, ArchiveSubmissionView,
    # Income sections
    LocalEmploymentListView, LocalEmploymentItemView, ForeignIncomeView, TerminalBenefitView,
    RentIncomeView, InterestIncomeView, DividendIncomeView,
    SoleProprietorshipListView, SoleProprietorshipItemView, OtherIncomeView,
    TBSecuritiesIncomeView, CashFlowSuggestedView,
    # Qualifying / Credits
    QualifyingPaymentsView, TaxCreditsView,
    SelfAssessmentListView, SelfAssessmentItemView,
    # Assets
    ImmovablePropertyListView, ImmovablePropertyItemView,
    MotorVehicleListView, MotorVehicleItemView,
    BankBalanceListView, BankBalanceItemView,
    SharesListView, SharesItemView,
    CashInHandView, LoansGivenView,
    GoldJewelleryView,
    BusinessPropertyListView, BusinessPropertyItemView,
    OtherAssetListView, OtherAssetItemView,
    DisposalListView, DisposalItemView,
    # Liabilities
    LiabilityListView, LiabilityItemView,
    # Declarant
    DeclarantDetailsView,
    # Cash Flow
    CashFlowStatementView,
    # New views (Phase 2)
    LiveCalculateView, PaymentStatusView, IRDSubmissionUploadView,
    WHTCertificateListView, WHTCertificateItemView, WHTCategoryListView,
    AccessRequestListView, AccessRequestDetailView,
    DashboardStatusDetailView, PortfolioDashboardView,
    SystemSettingsView,
    SendAssessmentFormView,
    SendAssessmentFormsBulkView,
)

urlpatterns = [
    path('years/', TaxYearListView.as_view(), name='tax_years'),
    path('submissions/', TaxSubmissionListCreateView.as_view(), name='submissions_list'),
    path('submissions/<int:pk>/', TaxSubmissionDetailView.as_view(), name='submission_detail'),
    path('submissions/<int:pk>/submit/', SubmitTaxFormView.as_view(), name='submit_form'),
    path('submissions/<int:pk>/request-info/', RequestInfoView.as_view(), name='request_info'),
    path('submissions/<int:pk>/confirm-calculation/', ConfirmCalculationView.as_view(), name='confirm_calculation'),
    path('submissions/<int:pk>/client-confirm/', ClientConfirmView.as_view(), name='client_confirm'),
    path('submissions/<int:pk>/pdf/', GeneratePDFView.as_view(), name='generate_pdf'),
    path('submissions/<int:pk>/update-calculation/', ConsultantUpdateCalculationView.as_view(), name='update_calculation'),

    # New: live calculation preview (no save)
    path('submissions/<int:pk>/live-calculate/', LiveCalculateView.as_view(), name='live_calculate'),
    # New: payment status (Accounts Division)
    path('submissions/<int:pk>/payment-status/', PaymentStatusView.as_view(), name='payment_status'),
    # Accounts Division: queue of submissions pending payment
    path('submissions/accounts-queue/', AccountsQueueView.as_view(), name='accounts_queue'),
    # Consultant: send tax computation to client for review (after payment confirmed)
    path('submissions/<int:pk>/final-submit/', FinalSubmitView.as_view(), name='final_submit'),
    # Client: confirm the tax computation
    path('submissions/<int:pk>/client-final-confirm/', ClientFinalConfirmView.as_view(), name='client_final_confirm'),
    # Consultant: mark as complete and archive
    path('submissions/<int:pk>/archive/', ArchiveSubmissionView.as_view(), name='archive_submission'),
    # New: IRD submission upload
    path('submissions/<int:pk>/ird-upload/', IRDSubmissionUploadView.as_view(), name='ird_upload'),

    # Income sections
    path('submissions/<int:submission_id>/income/local-employment/', LocalEmploymentListView.as_view()),
    path('income/local-employment/<int:pk>/', LocalEmploymentItemView.as_view()),
    path('submissions/<int:submission_id>/income/foreign/', ForeignIncomeView.as_view()),
    path('submissions/<int:submission_id>/income/terminal-benefit/', TerminalBenefitView.as_view()),
    path('submissions/<int:submission_id>/income/rent/', RentIncomeView.as_view()),
    path('submissions/<int:submission_id>/income/interest/', InterestIncomeView.as_view()),
    path('submissions/<int:submission_id>/income/dividend/', DividendIncomeView.as_view()),
    path('submissions/<int:submission_id>/income/sole-proprietorship/', SoleProprietorshipListView.as_view()),
    path('income/sole-proprietorship/<int:pk>/', SoleProprietorshipItemView.as_view()),
    path('submissions/<int:submission_id>/income/other/', OtherIncomeView.as_view()),
    path('submissions/<int:submission_id>/income/tb-securities/', TBSecuritiesIncomeView.as_view()),
    path('submissions/<int:submission_id>/cashflow/suggested/', CashFlowSuggestedView.as_view()),

    # Qualifying payments & tax credits
    path('submissions/<int:submission_id>/qualifying-payments/', QualifyingPaymentsView.as_view()),
    path('submissions/<int:submission_id>/tax-credits/', TaxCreditsView.as_view()),
    path('submissions/<int:submission_id>/self-assessment/', SelfAssessmentListView.as_view()),
    path('self-assessment/<int:pk>/', SelfAssessmentItemView.as_view()),

    # WHT Certificates (replaces bank balance confirmation — Change 15)
    path('submissions/<int:submission_id>/wht-certificates/', WHTCertificateListView.as_view()),
    path('wht-certificates/<int:pk>/', WHTCertificateItemView.as_view()),
    path('wht-categories/', WHTCategoryListView.as_view(), name='wht_categories'),

    # Assets
    path('submissions/<int:submission_id>/assets/immovable/', ImmovablePropertyListView.as_view()),
    path('assets/immovable/<int:pk>/', ImmovablePropertyItemView.as_view()),
    path('submissions/<int:submission_id>/assets/vehicles/', MotorVehicleListView.as_view()),
    path('assets/vehicles/<int:pk>/', MotorVehicleItemView.as_view()),
    path('submissions/<int:submission_id>/assets/bank-balances/', BankBalanceListView.as_view()),
    path('assets/bank-balances/<int:pk>/', BankBalanceItemView.as_view()),
    path('submissions/<int:submission_id>/assets/shares/', SharesListView.as_view()),
    path('assets/shares/<int:pk>/', SharesItemView.as_view()),
    path('submissions/<int:submission_id>/assets/cash/', CashInHandView.as_view()),
    path('submissions/<int:submission_id>/assets/loans-given/', LoansGivenView.as_view()),
    path('submissions/<int:submission_id>/assets/gold/', GoldJewelleryView.as_view()),
    path('submissions/<int:submission_id>/assets/business/', BusinessPropertyListView.as_view()),
    path('assets/business/<int:pk>/', BusinessPropertyItemView.as_view()),
    path('submissions/<int:submission_id>/assets/other/', OtherAssetListView.as_view()),
    path('assets/other/<int:pk>/', OtherAssetItemView.as_view()),
    path('submissions/<int:submission_id>/assets/disposals/', DisposalListView.as_view()),
    path('assets/disposals/<int:pk>/', DisposalItemView.as_view()),

    # Liabilities
    path('submissions/<int:submission_id>/liabilities/', LiabilityListView.as_view()),
    path('liabilities/<int:pk>/', LiabilityItemView.as_view()),

    # Declarant
    path('submissions/<int:submission_id>/declarant/', DeclarantDetailsView.as_view()),

    # Cash Flow / Receipts & Payments
    path('submissions/<int:submission_id>/cash-flow/', CashFlowStatementView.as_view()),

    # Consultant-only
    path('archive/', ArchiveTreeView.as_view(), name='archive_tree'),
    path('submissions/<int:pk>/edit-logs/', SubmissionEditLogsView.as_view(), name='edit_logs'),

    # Dashboard drill-down (Change 4)
    path('dashboard/status/<str:status_key>/', DashboardStatusDetailView.as_view(), name='dashboard_status_detail'),
    # Portfolio dashboard (Change 7)
    path('dashboard/portfolio/', PortfolioDashboardView.as_view(), name='portfolio_dashboard'),

    # Access requests (Change 13)
    path('access-requests/', AccessRequestListView.as_view(), name='access_requests'),
    path('access-requests/<int:pk>/', AccessRequestDetailView.as_view(), name='access_request_detail'),

    # System settings (Change 12)
    path('settings/', SystemSettingsView.as_view(), name='system_settings'),

    # Cyclic assessment year form dispatch
    path('send-form/', SendAssessmentFormView.as_view(), name='send_assessment_form'),
    path('send-forms-bulk/', SendAssessmentFormsBulkView.as_view(), name='send_assessment_forms_bulk'),
]
