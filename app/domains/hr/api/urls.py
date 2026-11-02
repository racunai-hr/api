from django.urls import path

from domains.hr.api.views import (
    EmployeeContractDetailView,
    EmployeeContractListView,
    EmployeeDetailView,
    EmployeeListView,
    PayrollPeriodCalculateView,
    PayrollPeriodCalculationRecapPdfView,
    PayrollPeriodDetailView,
    PayrollPeriodGenerateView,
    PayrollPeriodJournalPdfView,
    PayrollPeriodListView,
    PayrollPeriodPaymentRecapPdfView,
    PayrollPeriodPostView,
    PayslipAdjustmentListView,
    PayslipDetailView,
    PayslipEarningListView,
    PayslipPdfView,
    WorkTimeListView,
    WorkTimeLockView,
)

urlpatterns = [
    path('hr/employees/', EmployeeListView.as_view(), name='hr-employee-list'),
    path('hr/employees/<int:pk>/', EmployeeDetailView.as_view(), name='hr-employee-detail'),
    path(
        'hr/employees/<int:pk>/contracts/',
        EmployeeContractListView.as_view(),
        name='hr-employee-contracts',
    ),
    path(
        'hr/employees/<int:pk>/contracts/<int:contract_id>/',
        EmployeeContractDetailView.as_view(),
        name='hr-employee-contract-detail',
    ),
    path('hr/payroll-periods/', PayrollPeriodListView.as_view(), name='hr-payroll-period-list'),
    path(
        'hr/payroll-periods/<int:pk>/',
        PayrollPeriodDetailView.as_view(),
        name='hr-payroll-period-detail',
    ),
    path(
        'hr/payroll-periods/<int:pk>/generate/',
        PayrollPeriodGenerateView.as_view(),
        name='hr-payroll-period-generate',
    ),
    path(
        'hr/payroll-periods/<int:pk>/calculate/',
        PayrollPeriodCalculateView.as_view(),
        name='hr-payroll-period-calculate',
    ),
    path(
        'hr/payroll-periods/<int:pk>/post/',
        PayrollPeriodPostView.as_view(),
        name='hr-payroll-period-post',
    ),
    path(
        'hr/payroll-periods/<int:pk>/journal-pdf/',
        PayrollPeriodJournalPdfView.as_view(),
        name='hr-payroll-period-journal-pdf',
    ),
    path(
        'hr/payroll-periods/<int:pk>/payment-recap-pdf/',
        PayrollPeriodPaymentRecapPdfView.as_view(),
        name='hr-payroll-period-payment-recap-pdf',
    ),
    path(
        'hr/payroll-periods/<int:pk>/calculation-recap-pdf/',
        PayrollPeriodCalculationRecapPdfView.as_view(),
        name='hr-payroll-period-calculation-recap-pdf',
    ),
    path('hr/worktime/', WorkTimeListView.as_view(), name='hr-worktime'),
    path('hr/worktime/lock/', WorkTimeLockView.as_view(), name='hr-worktime-lock'),
    path('hr/payslips/<int:pk>/earnings/', PayslipEarningListView.as_view(), name='hr-payslip-earnings'),
    path('hr/payslips/<int:pk>/adjustments/', PayslipAdjustmentListView.as_view(), name='hr-payslip-adjustments'),
    path('hr/payslips/<int:pk>/', PayslipDetailView.as_view(), name='hr-payslip-detail'),
    path('hr/payslips/<int:pk>/pdf/', PayslipPdfView.as_view(), name='hr-payslip-pdf'),
]
