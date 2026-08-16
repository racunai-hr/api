from django.db import models


class IntegrationType(models.TextChoices):
    ERACUN = 'eracun', 'eRačun'
    FISCALIZATION = 'fiscalization', 'Fiskalizacija'
    PAYMENT = 'payment', 'Plaćanje'
    SHIPPING = 'shipping', 'Dostava'


class IntegrationProvider(models.TextChoices):
    SUPER = 'super', 'SUPER'
    DIRECT = 'direct', 'Direktno (racunAI)'
    MER = 'mer', 'MER'
    CIS = 'cis', 'CIS (Porezna)'
    FISKAL_PLATFORM = 'fiskal_platform', 'Fiskal Platform'
    OTP = 'otp', 'OTP banka'


class IntegrationEnvironment(models.TextChoices):
    PRODUCTION = 'production', 'Produkcija'
    TEST = 'test', 'Test'
    STAGING = 'staging', 'Staging'
    SANDBOX = 'sandbox', 'Sandbox'
