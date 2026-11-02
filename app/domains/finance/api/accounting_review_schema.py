from rest_framework import serializers


class AccountingReviewRequestSerializer(serializers.Serializer):
    answers = serializers.DictField(required=False)


class AccountingReviewOptionSerializer(serializers.Serializer):
    value = serializers.CharField()
    label = serializers.CharField()


class AccountingReviewQuestionSerializer(serializers.Serializer):
    key = serializers.CharField()
    type = serializers.CharField()
    label = serializers.CharField()
    required = serializers.BooleanField()
    options = AccountingReviewOptionSerializer(many=True)


class AccountingReviewAccountSerializer(serializers.Serializer):
    position = serializers.IntegerField()
    account_id = serializers.IntegerField()
    account_code = serializers.CharField()
    account_name = serializers.CharField()
    source = serializers.CharField()


class AccountingReviewLineSerializer(serializers.Serializer):
    account_code = serializers.CharField()
    account_name = serializers.CharField()
    debit = serializers.CharField()
    credit = serializers.CharField()


class AccountingReviewVehicleSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    vin = serializers.CharField(allow_blank=True)
    registration_plate = serializers.CharField(allow_blank=True)
    accounting_role = serializers.CharField(allow_blank=True)


class AccountingReviewForeignVatSerializer(serializers.Serializer):
    country = serializers.CharField(allow_blank=True)
    amount = serializers.CharField()
    net_amount = serializers.CharField(allow_blank=True)
    gross_amount = serializers.CharField(allow_blank=True)
    currency = serializers.CharField(allow_blank=True)
    treatment = serializers.CharField(allow_blank=True)
    fx_rate = serializers.CharField(allow_blank=True)
    fx_rate_source = serializers.CharField(allow_blank=True)
    fx_rate_date = serializers.CharField(allow_blank=True)


class AccountingReviewSerializer(serializers.Serializer):
    status = serializers.CharField()
    purchase_nature = serializers.CharField(allow_null=True)
    posting_profile = serializers.CharField(allow_null=True)
    profile_label = serializers.CharField(allow_blank=True)
    vehicle = AccountingReviewVehicleSerializer(allow_null=True)
    decision_source = serializers.CharField()
    reason = serializers.CharField(allow_blank=True)
    explanation = serializers.CharField(allow_blank=True)
    questions = AccountingReviewQuestionSerializer(many=True)
    preview = AccountingReviewLineSerializer(many=True, allow_null=True)
    line_accounts = AccountingReviewAccountSerializer(many=True)
    document_date = serializers.CharField(allow_blank=True)
    foreign_vat = AccountingReviewForeignVatSerializer(allow_null=True, required=False)
