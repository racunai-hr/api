from rest_framework import serializers


class ExpenseCloseLineSerializer(serializers.Serializer):
    position = serializers.IntegerField()
    description = serializers.CharField(allow_blank=True)
    account_id = serializers.IntegerField(allow_null=True)
    account_code = serializers.CharField(allow_blank=True)
    account_name = serializers.CharField(allow_blank=True)
    source = serializers.CharField()


class ExpenseClosePaymentSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    transaction_date = serializers.DateField()
    amount = serializers.CharField()
    currency = serializers.CharField()
    counterparty_name = serializers.CharField(allow_blank=True)
    description = serializers.CharField(allow_blank=True)
    reference = serializers.CharField(allow_blank=True)
    match_score = serializers.IntegerField()
    match_reasons = serializers.ListField(child=serializers.CharField())
    recommended = serializers.BooleanField()


class ExpenseClosePreviewSerializer(serializers.Serializer):
    remembered = serializers.BooleanField()
    lines = ExpenseCloseLineSerializer(many=True)
    payments = ExpenseClosePaymentSerializer(many=True)


class ExpenseCloseLineInputSerializer(serializers.Serializer):
    position = serializers.IntegerField()
    account_id = serializers.IntegerField()


class ExpenseCloseRequestSerializer(serializers.Serializer):
    bank_transaction_id = serializers.IntegerField()
    remember = serializers.BooleanField(required=False, default=False)
    lines = ExpenseCloseLineInputSerializer(many=True, required=False)
    answers = serializers.DictField(required=False)


class ExpenseCloseResultSerializer(serializers.Serializer):
    expense_id = serializers.IntegerField()
    status = serializers.CharField()
    bank_transaction_id = serializers.IntegerField()
    match_status = serializers.CharField()
    subledger_status = serializers.CharField()
