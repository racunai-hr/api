"""OpenAPI schema for the HR/payroll API (kept light — payloads are plain dicts)."""

from __future__ import annotations

from rest_framework import serializers

from config.schema_common import money_field


class ContractSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    start_date = serializers.CharField()
    end_date = serializers.CharField(allow_null=True)
    contract_type = serializers.CharField()
    work_time_percentage = money_field()
    base_gross_salary = money_field()
    recipient_code = serializers.CharField()
    income_type_code = serializers.CharField()
    is_active = serializers.BooleanField()
    signed_at = serializers.CharField(allow_null=True, required=False)
    weekly_hours = money_field(required=False, allow_null=True)
    job_title = serializers.CharField(required=False, allow_blank=True)
    probation_months = serializers.IntegerField(required=False, allow_null=True)
    notice_rule = serializers.CharField(required=False, allow_blank=True)
    annual_leave_rule = serializers.CharField(required=False, allow_blank=True)
    collective_agreement_ref = serializers.CharField(required=False, allow_blank=True)
    termination_reason = serializers.CharField(required=False, allow_blank=True)


class EmployeeSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    code = serializers.CharField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    full_name = serializers.CharField()
    oib = serializers.CharField()
    date_of_birth = serializers.CharField(allow_null=True)
    street = serializers.CharField()
    city = serializers.CharField()
    municipality_code = serializers.CharField()
    work_location = serializers.CharField()
    iban = serializers.CharField()
    bank_name = serializers.CharField()
    mio_pillar = serializers.CharField()
    personal_allowance_coefficient = serializers.CharField()
    is_active = serializers.BooleanField()
    notes = serializers.CharField()
    sex = serializers.CharField(required=False, allow_blank=True)
    citizenship = serializers.CharField(required=False, allow_blank=True)
    residence_street = serializers.CharField(required=False, allow_blank=True)
    residence_city = serializers.CharField(required=False, allow_blank=True)
    residence_municipality_code = serializers.CharField(required=False, allow_blank=True)
    occupation = serializers.CharField(required=False, allow_blank=True)
    qualification = serializers.CharField(required=False, allow_blank=True)
    licence_note = serializers.CharField(required=False, allow_blank=True)
    employment_status = serializers.CharField(required=False, allow_blank=True)
    work_permit_number = serializers.CharField(required=False, allow_blank=True)
    work_permit_valid_to = serializers.CharField(allow_null=True, required=False)
    hzzo_registered_at = serializers.CharField(allow_null=True, required=False)
    hzmo_registered_at = serializers.CharField(allow_null=True, required=False)
    insurance_changed_at = serializers.CharField(allow_null=True, required=False)
    deregistered_at = serializers.CharField(allow_null=True, required=False)
    legal_evidence_gaps = serializers.ListField(child=serializers.CharField(), required=False)
    active_contract = ContractSerializer(allow_null=True)
    contracts = ContractSerializer(many=True, required=False)


class EmployeeListSerializer(serializers.Serializer):
    results = EmployeeSerializer(many=True)


class ContractListSerializer(serializers.Serializer):
    results = ContractSerializer(many=True)


class PayslipSummarySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    employee_id = serializers.IntegerField()
    employee_name = serializers.CharField()
    status = serializers.CharField()
    gross_salary = money_field()
    net_salary = money_field()
    amount_to_pay = money_field(required=False)
    non_taxable_total = money_field(required=False)
    deductions_total = money_field(required=False)
    compliance_flags = serializers.ListField(required=False)
    income_tax_amount = money_field()
    zo_amount = money_field()
    total_labor_cost = money_field()


class PayrollPeriodSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    year = serializers.IntegerField()
    month = serializers.IntegerField()
    calculation_date = serializers.CharField()
    payment_date = serializers.CharField()
    status = serializers.CharField()
    posted_at = serializers.CharField(allow_null=True)
    employee_count = serializers.IntegerField()
    gross_total = money_field()
    net_total = money_field()
    income_tax_total = money_field()
    zo_total = money_field()
    payslips = PayslipSummarySerializer(many=True, required=False)


class PayrollPeriodListSerializer(serializers.Serializer):
    results = PayrollPeriodSerializer(many=True)


class PayslipEarningLineSerializer(serializers.Serializer):
    name = serializers.CharField()
    hours = serializers.CharField()
    gross_amount = money_field()


class PayslipDetailSerializer(PayslipSummarySerializer):
    period = serializers.DictField()
    date_from = serializers.CharField()
    date_to = serializers.CharField()
    mio1_base = money_field()
    mio1_amount = money_field()
    mio2_amount = money_field()
    income_base = money_field()
    personal_allowance_amount = money_field()
    tax_base = money_field()
    income_tax_rate_used = serializers.CharField()
    earning_lines = PayslipEarningLineSerializer(many=True)


class ErrorSerializer(serializers.Serializer):
    detail = serializers.CharField()
