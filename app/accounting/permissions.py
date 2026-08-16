"""Permisije za računovodstvene uloge."""

from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType

ACCOUNTING_MODELS = [
    ('accounting', 'chartofaccounts'),
    ('accounting', 'journalentry'),
    ('accounting', 'journalentryline'),
    ('accounting', 'postingrule'),
    ('accounting', 'vatperiod'),
    ('accounting', 'vatledgerentry'),
    ('accounting', 'fiscalperiod'),
    ('accounting', 'analyticaccount'),
    ('banking', 'bankstatement'),
    ('banking', 'banktransaction'),
]


def ensure_accountant_permissions(group):
    perms = []
    for app_label, model in ACCOUNTING_MODELS:
        ct = ContentType.objects.filter(app_label=app_label, model=model).first()
        if not ct:
            continue
        for codename in ('view', 'add', 'change', 'delete'):
            perm = Permission.objects.filter(content_type=ct, codename=f'{codename}_{model}').first()
            if perm:
                perms.append(perm)
    group.permissions.set(perms)
