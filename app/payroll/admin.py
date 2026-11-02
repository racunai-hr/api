from django.contrib import admin

from tenants.mixins import TenantAdminMixin

from .models import (
    Employee,
    EmploymentContract,
    MunicipalityIncomeTaxRate,
    Payslip,
    PayslipAdjustmentLine,
    PayslipContributionLine,
    PayslipEarningLine,
    PayrollPeriod,
    PayrollTaxYear,
)


class EmploymentContractInline(admin.TabularInline):
    model = EmploymentContract
    extra = 0


@admin.register(Employee)
class EmployeeAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('full_name', 'oib', 'municipality_code', 'is_active')
    search_fields = ('first_name', 'last_name', 'oib', 'code')
    list_filter = ('is_active',)
    inlines = [EmploymentContractInline]


@admin.register(EmploymentContract)
class EmploymentContractAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('employee', 'start_date', 'end_date', 'contract_type', 'base_gross_salary', 'is_active')
    list_filter = ('contract_type', 'is_active')
    search_fields = ('employee__first_name', 'employee__last_name', 'employee__oib')


class PayslipInline(admin.TabularInline):
    model = Payslip
    extra = 0
    fields = ('employee', 'gross_salary', 'net_salary', 'status')
    readonly_fields = fields


@admin.register(PayrollPeriod)
class PayrollPeriodAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('year', 'month', 'calculation_date', 'payment_date', 'status')
    list_filter = ('status', 'year')
    inlines = [PayslipInline]


class _PostedPayslipInline(admin.TabularInline):
    extra = 0

    def has_change_permission(self, request, obj=None):
        if obj is not None and getattr(obj, 'status', None) == Payslip.POSTED:
            return False
        return super().has_change_permission(request, obj)

    def has_add_permission(self, request, obj=None):
        if obj is not None and getattr(obj, 'status', None) == Payslip.POSTED:
            return False
        return super().has_add_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and getattr(obj, 'status', None) == Payslip.POSTED:
            return False
        return super().has_delete_permission(request, obj)


class PayslipEarningLineInline(_PostedPayslipInline):
    model = PayslipEarningLine


class PayslipContributionLineInline(_PostedPayslipInline):
    model = PayslipContributionLine


class PayslipAdjustmentLineInline(_PostedPayslipInline):
    model = PayslipAdjustmentLine


@admin.register(Payslip)
class PayslipAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('employee', 'period', 'gross_salary', 'net_salary', 'total_labor_cost', 'status')
    list_filter = ('status', 'period')
    search_fields = ('employee__first_name', 'employee__last_name', 'employee__oib')
    inlines = [PayslipEarningLineInline, PayslipContributionLineInline, PayslipAdjustmentLineInline]

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.status == Payslip.POSTED:
            return False
        return super().has_delete_permission(request, obj)

    def get_readonly_fields(self, request, obj=None):
        if obj is not None and obj.status == Payslip.POSTED:
            return [field.name for field in obj._meta.fields]
        return list(super().get_readonly_fields(request, obj))


@admin.register(PayrollTaxYear)
class PayrollTaxYearAdmin(admin.ModelAdmin):
    list_display = ('year', 'basic_personal_allowance', 'mio1_rate', 'mio2_rate', 'zo_rate', 'minimum_wage_gross')


@admin.register(MunicipalityIncomeTaxRate)
class MunicipalityIncomeTaxRateAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'year', 'lower_rate', 'higher_rate')
    search_fields = ('code', 'name')
    list_filter = ('year',)
