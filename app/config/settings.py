import os
from pathlib import Path
import environ

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Initialize environment variables
env = environ.Env(
    DEBUG=(bool, False)
)

# Take environment variables from .env file
environ.Env.read_env(os.path.join(BASE_DIR, '.env'))

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = env('SECRET_KEY')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = env('DEBUG')

ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=[])

CSRF_TRUSTED_ORIGINS = env.list('CSRF_TRUSTED_ORIGINS', default=[])

# Multi-tenancy
TENANT_PLATFORM_DOMAIN = env('TENANT_PLATFORM_DOMAIN', default='racunai.hr')
TENANT_PLATFORM_ADMIN_HOSTS = ['admin.racunai.hr']
TENANT_RESERVED_SLUGS = ['app', 'admin', 'www', 'api', 'mail', 'static', 'otp', 'otp-sbx']
TENANT_LEGACY_HOST_MAP = {
    'erp.finestar.hr': 'finestar',
}
TENANT_DEFAULT_SLUG = env('TENANT_DEFAULT_SLUG', default='')
TENANT_SESSION_KEY = 'active_tenant_id'
TENANT_INVITATION_EXPIRY_DAYS = 7
TENANT_TRAEFIK_DYNAMIC_PATH = env(
    'TENANT_TRAEFIK_DYNAMIC_PATH',
    default='/opt/stacks/traefik/dynamic/racunai-erp-custom.yml',
)
TENANT_CUSTOM_DOMAIN_CERT_RESOLVER = env('TENANT_CUSTOM_DOMAIN_CERT_RESOLVER', default='cloudflare')

# Application definition
DJANGO_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',

]

THIRD_PARTY_APPS = [
    'rest_framework',
    'corsheaders',
    'django_celery_beat',
    'django_celery_results',
    'django_extensions',
    'django_filters',
]

LOCAL_APPS = [

    # Multi-tenancy
    'tenants',

    # Main apps
     'accounts',
     'dashboard', 
     'settings',
     'partners',

     # Financial apps
    'invoices',
    'payments', 
    'expenses',
    'accounting',
    'banking',
    'integrations',
    'ubl',
    'super_integration',
    'fiscal_gateway',
]

# Također zakomentaj:

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    'tenants.middleware.DynamicAllowedHostsMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'tenants.middleware.TenantMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'tenants.middleware.UserContextMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'tenants.context_processors.admin_branding',
                'tenants.context_processors.tenant_switcher',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'

# Database
DATABASES = {
    'default': {
        'ENGINE': 'django.contrib.gis.db.backends.postgis',
        'NAME': env('DATABASE_NAME'),
        'USER': env('DATABASE_USER'),
        'PASSWORD': env('DATABASE_PASSWORD'),
        'HOST': env('DATABASE_HOST'),
        'PORT': env('DATABASE_PORT'),
    }
}

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Internationalization
LANGUAGE_CODE = env('LANGUAGE_CODE', default='en-us')
TIME_ZONE = env('TIME_ZONE', default='UTC')
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = env('STATIC_URL', default='/static/')
STATIC_ROOT = BASE_DIR / 'staticfiles'


# Media files
MEDIA_URL = env('MEDIA_URL', default='/media/')
MEDIA_ROOT = BASE_DIR / 'media'

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Django REST Framework
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.SessionAuthentication',
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 20,
}

from datetime import timedelta

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(hours=8),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True,
}

# CORS settings
CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "https://erp.finestar.hr",
    "http://erp.finestar.hr",
    "https://admin.racunai.hr",
    "https://app.racunai.hr",
]
CORS_ALLOWED_ORIGIN_REGEXES = [
    r"^https://[\w-]+\.racunai\.hr$",
]

# Cloudflare Turnstile (login CAPTCHA)
TURNSTILE_VERIFY_ENABLED = env.bool('TURNSTILE_VERIFY_ENABLED', default=False)
TURNSTILE_SECRET_KEY = env('TURNSTILE_SECRET_KEY', default='')
TURNSTILE_SITE_KEY = env('TURNSTILE_SITE_KEY', default='')
TURNSTILE_ADMIN_HOSTS = env.list(
    'TURNSTILE_ADMIN_HOSTS',
    default=['admin.racunai.hr'],
)

# Celery Configuration
CELERY_BROKER_URL = env('CELERY_BROKER_URL', default='')
CELERY_RESULT_BACKEND = env('CELERY_RESULT_BACKEND', default='')
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 30 * 60
CELERY_TASK_SOFT_TIME_LIMIT = 60
CELERY_BEAT_SCHEDULER = 'django_celery_beat.schedulers:DatabaseScheduler'

SUPER_DEFAULT_API_BASE_URL = env('SUPER_DEFAULT_API_BASE_URL', default='https://apitest.super.hr')
# Rollback flag: allow SUPER eRačun when DIRECT config is missing (M1.7 deprecation).
USE_SUPER_ERACUN_FALLBACK = env.bool('USE_SUPER_ERACUN_FALLBACK', default=True)

# Fiskalizacija 2.0 (CIS) — defaulti za Docker mount /run/secrets/fiscal-cert
FISCAL_CERT_P12_PATH = env(
    'FISCAL_CERT_P12_PATH',
    default='/run/secrets/fiscal-cert/36619131370.F2.2.p12',
)
FISCAL_CERT_P12_PASSWORD = env('FISCAL_CERT_P12_PASSWORD', default='')
MPS_SERVICE_URL = env('MPS_SERVICE_URL', default='http://racunai_mps:8000')
FISCAL_CIS_ENV = env('FISCAL_CIS_ENV', default='demo')
FISCAL_CIS_ENDPOINT = env('FISCAL_CIS_ENDPOINT', default='')
FISCAL_CIS_VERIFY_SSL = env.bool('FISCAL_CIS_VERIFY_SSL', default=False)

# Fiskal Platform (async REST fiscalization service)
USE_FISKAL_PLATFORM = env.bool('USE_FISKAL_PLATFORM', default=False)
FISKAL_PLATFORM_URL = env('FISKAL_PLATFORM_URL', default='http://fiskal-api:8000')
FISKAL_PLATFORM_API_TOKEN = env('FISKAL_PLATFORM_API_TOKEN', default='')
FISKAL_PLATFORM_PROFILE_SLUG = env('FISKAL_PLATFORM_PROFILE_SLUG', default='finestar')
FISKAL_PLATFORM_POLL_TIMEOUT_SECONDS = env.int('FISKAL_PLATFORM_POLL_TIMEOUT_SECONDS', default=120)
FISKAL_PLATFORM_POLL_INTERVAL_SECONDS = env.float('FISKAL_PLATFORM_POLL_INTERVAL_SECONDS', default=2.0)

DOMIBUS_WS_URL = env(
    'DOMIBUS_WS_URL',
    default='http://192.168.16.21:8080/EracunAS4/',
)
DOMIBUS_AP_OIB = env('DOMIBUS_AP_OIB', default='36619131370')
DOMIBUS_AP_PARTY_ID = env('DOMIBUS_AP_PARTY_ID', default='FISKAL 2')
DOMIBUS_ADMIN_USER = env('DOMIBUS_ADMIN_USER', default='admin')
DOMIBUS_ADMIN_PASS = env('DOMIBUS_ADMIN_PASS', default='')

INTEGRATION_ALERT_OUTBOUND_FAILED_THRESHOLD = env.int(
    'INTEGRATION_ALERT_OUTBOUND_FAILED_THRESHOLD',
    default=5,
)
INTEGRATION_ALERT_WINDOW_HOURS = env.int('INTEGRATION_ALERT_WINDOW_HOURS', default=1)

# OTP banka PSD2 (sandbox/produkcija — jedan set po deployu)
OTP_CLIENT_ID = env('OTP_CLIENT_ID', default='')
OTP_CLIENT_SECRET = env('OTP_CLIENT_SECRET', default='')
OTP_CERT_PATH = env('OTP_CERT_PATH', default='/run/secrets/otp-cert/client.p12')
OTP_CERT_PASSWORD = env('OTP_CERT_PASSWORD', default='')
OTP_SIGNATURE_CERT_PATH = env('OTP_SIGNATURE_CERT_PATH', default='/run/secrets/otp-cert/signature.p12')
OTP_SIGNATURE_CERT_PASSWORD = env('OTP_SIGNATURE_CERT_PASSWORD', default='')
OTP_PSU_ID_TYPE = env('OTP_PSU_ID_TYPE', default='222')
OTP_ENV = env('OTP_ENV', default='sandbox')
OTP_IAM_BASE = env('OTP_IAM_BASE', default='https://iam.sandbox.otpbanka.hr')
OTP_API_BASE = env('OTP_API_BASE', default='https://api.sandbox.otpbanka.hr')
OTP_REDIRECT_URI = env('OTP_REDIRECT_URI', default='https://otp-sbx.racunai.hr/oauth/callback/')
OTP_SERVICE_HOST = env('OTP_SERVICE_HOST', default='otp-sbx.racunai.hr')

# Sandbox-only: emit PaymentExecuted (i knjiženje) na status authorised umjesto executed
PIS_SANDBOX_ALLOW_POSTING_ON_AUTHORISED = env.bool(
    'PIS_SANDBOX_ALLOW_POSTING_ON_AUTHORISED',
    default=False,
)
PLATFORM_ADMIN_LOGIN_URL = env(
    'PLATFORM_ADMIN_LOGIN_URL',
    default='https://admin.racunai.hr/admin/login/',
)
SESSION_COOKIE_DOMAIN = env('SESSION_COOKIE_DOMAIN', default='.racunai.hr')
FISCAL_AS4_SERVICE_HOSTS = env.list('FISCAL_AS4_SERVICE_HOSTS', default=[])

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_SSL_REDIRECT = env.bool('SECURE_SSL_REDIRECT', default=True)
    SECURE_REDIRECT_EXEMPT = [
        r'^api/fiscal/as4/inbound/',
        r'^api/ready/',
        r'^oauth/callback/',
    ]
    SESSION_COOKIE_SECURE = env.bool('SESSION_COOKIE_SECURE', default=True)
    CSRF_COOKIE_SECURE = env.bool('CSRF_COOKIE_SECURE', default=True)
    SECURE_HSTS_SECONDS = env.int('SECURE_HSTS_SECONDS', default=31536000)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool('SECURE_HSTS_INCLUDE_SUBDOMAINS', default=True)
    SECURE_HSTS_PRELOAD = env.bool('SECURE_HSTS_PRELOAD', default=True)
    SECURE_CONTENT_TYPE_NOSNIFF = env.bool('SECURE_CONTENT_TYPE_NOSNIFF', default=True)
    X_FRAME_OPTIONS = env('X_FRAME_OPTIONS', default='DENY')

# Static files storage
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

# Logging
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'file': {
            'level': 'INFO',
            'class': 'logging.FileHandler',
            'filename': 'logs/django.log',
        },
        'console': {
            'class': 'logging.StreamHandler',
        },
    },
    'root': {
        'handlers': ['console', 'file'],
        'level': 'INFO',
    },
}
