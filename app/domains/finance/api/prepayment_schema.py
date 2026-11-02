from rest_framework import serializers


class PrepaymentCandidateSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    posting_date = serializers.CharField()
    original_amount = serializers.CharField()
    available_amount = serializers.CharField()
    currency = serializers.CharField()
    purchase_nature = serializers.CharField()
    vat_treatment = serializers.CharField()
    account_code = serializers.CharField()


class ChoiceSerializer(serializers.Serializer):
    value = serializers.CharField()
    label = serializers.CharField()


class PrepaymentCandidateListSerializer(serializers.Serializer):
    supplier_id = serializers.IntegerField(allow_null=True)
    currency = serializers.CharField()
    ubl_prepaid_amount = serializers.CharField(allow_null=True)
    ubl_payable_amount = serializers.CharField(allow_null=True)
    purchase_natures = ChoiceSerializer(many=True)
    vat_treatments = ChoiceSerializer(many=True)
    prepayments = PrepaymentCandidateSerializer(many=True)


class SupplierPrepaymentCreateSerializer(serializers.Serializer):
    partner_id = serializers.IntegerField()
    purchase_nature = serializers.CharField()
    vat_treatment = serializers.CharField(required=False, allow_blank=True)


class SupplierPrepaymentResultSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    bank_transaction_id = serializers.IntegerField()
    account_code = serializers.CharField()
    original_amount = serializers.CharField()
    currency = serializers.CharField()
    status = serializers.CharField()
    match_status = serializers.CharField()


class ApplyPrepaymentLineSerializer(serializers.Serializer):
    prepayment_id = serializers.IntegerField()
    amount = serializers.CharField()


class ApplyPrepaymentsSerializer(serializers.Serializer):
    applications = ApplyPrepaymentLineSerializer(many=True)
    remember = serializers.BooleanField(required=False)
    lines = serializers.ListField(child=serializers.DictField(), required=False)
    answers = serializers.DictField(required=False)


class AppliedPrepaymentSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    prepayment_id = serializers.IntegerField()
    amount = serializers.CharField()
    journal_entry_id = serializers.IntegerField(allow_null=True)


class ApplyPrepaymentsResultSerializer(serializers.Serializer):
    source_type = serializers.CharField()
    source_id = serializers.IntegerField()
    status = serializers.CharField()
    subledger_status = serializers.CharField()
    applications = AppliedPrepaymentSerializer(many=True)
