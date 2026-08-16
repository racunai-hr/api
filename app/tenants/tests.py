from django.contrib import admin
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.http import Http404
from django.test import RequestFactory, TestCase, override_settings

from tenants.branding import INDEX_TITLE, SITE_HEADER, SITE_TITLE, admin_branding_context
from tenants.context_processors import admin_branding
from tenants.middleware import DynamicAllowedHostsMiddleware, TenantMiddleware
from tenants.models import Tenant, TenantInvitation, TenantMembership
from tenants.permissions import get_role, role_can_write, role_can_write_model
from tenants.resolution import (
    get_cached_custom_domains,
    invalidate_custom_domain_cache,
    is_platform_admin_host,
    resolve_platform_tenant,
    resolve_tenant_from_host,
)


@override_settings(
    TENANT_PLATFORM_DOMAIN='racunai.hr',
    TENANT_LEGACY_HOST_MAP={'erp.finestar.hr': 'finestar'},
    TENANT_RESERVED_SLUGS=['app', 'admin', 'www', 'api', 'mail', 'static'],
    ALLOWED_HOSTS=['erp.finestar.hr', 'missing.racunai.hr', 'demo.racunai.hr', 'erp.customco.hr', 'admin.racunai.hr'],
)
class TenantResolutionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant, _ = Tenant.objects.get_or_create(
            slug='demo',
            defaults={'name': 'Demo d.o.o.'},
        )
        cls.finestar, _ = Tenant.objects.get_or_create(
            slug='finestar',
            defaults={'name': 'Fine Star d.o.o.'},
        )
        cls.inactive = Tenant.objects.create(
            slug='inactive',
            name='Inactive d.o.o.',
            is_active=False,
        )
        cls.custom = Tenant.objects.create(
            slug='customco',
            name='Custom Co',
            custom_domain='erp.customco.hr',
        )

    def test_resolve_subdomain(self):
        tenant = resolve_tenant_from_host('demo.racunai.hr')
        self.assertEqual(tenant, self.tenant)

    def test_resolve_legacy_host(self):
        tenant = resolve_tenant_from_host('erp.finestar.hr')
        self.assertEqual(tenant, self.finestar)

    def test_resolve_custom_domain(self):
        tenant = resolve_tenant_from_host('erp.customco.hr')
        self.assertEqual(tenant, self.custom)

    def test_inactive_tenant_not_resolved(self):
        tenant = resolve_tenant_from_host('inactive.racunai.hr')
        self.assertIsNone(tenant)

    def test_unknown_host_returns_none(self):
        tenant = resolve_tenant_from_host('unknown.example.com')
        self.assertIsNone(tenant)

    def test_reserved_slug_not_resolved(self):
        self.assertIsNone(resolve_tenant_from_host('admin.racunai.hr'))
        self.assertIsNone(resolve_tenant_from_host('app.racunai.hr'))

    def test_is_platform_admin_host(self):
        self.assertTrue(is_platform_admin_host('admin.racunai.hr'))
        self.assertFalse(is_platform_admin_host('demo.racunai.hr'))


@override_settings(
    TENANT_PLATFORM_DOMAIN='racunai.hr',
    TENANT_LEGACY_HOST_MAP={'erp.finestar.hr': 'finestar'},
    ALLOWED_HOSTS=['erp.finestar.hr', 'missing.racunai.hr', 'admin.racunai.hr'],
)
class TenantMiddlewareTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.finestar, _ = Tenant.objects.get_or_create(
            slug='finestar',
            defaults={'name': 'Fine Star d.o.o.'},
        )

    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = TenantMiddleware(lambda request: request)

    def test_middleware_sets_request_tenant(self):
        request = self.factory.get('/', HTTP_HOST='erp.finestar.hr')
        response = self.middleware(request)
        self.assertEqual(response.tenant, self.finestar)

    def test_middleware_raises_404_for_unknown_host(self):
        request = self.factory.get('/', HTTP_HOST='missing.racunai.hr')
        with self.assertRaises(Http404):
            self.middleware(request)

    def test_middleware_allows_platform_admin_host(self):
        request = self.factory.get('/', HTTP_HOST='admin.racunai.hr')
        response = self.middleware(request)
        self.assertTrue(response.is_platform_admin)
        self.assertIsNone(response.tenant)


@override_settings(TENANT_RESERVED_SLUGS=['app', 'admin', 'www', 'api'])
class TenantSlugValidationTests(TestCase):
    def test_reserved_slug_rejected(self):
        tenant = Tenant(slug='admin', name='Admin Corp')
        with self.assertRaises(ValidationError):
            tenant.full_clean()

    def test_valid_slug_accepted(self):
        tenant = Tenant(slug='mycompany', name='My Company')
        tenant.full_clean()


class TenantMembershipTests(TestCase):
    def test_only_one_default_membership_per_user(self):
        tenant_a = Tenant.objects.create(slug='a', name='Tenant A')
        tenant_b = Tenant.objects.create(slug='b', name='Tenant B')
        user = User.objects.create_user(username='member', password='test-pass')

        TenantMembership.objects.create(
            user=user,
            tenant=tenant_a,
            role='owner',
            is_default=True,
        )
        TenantMembership.objects.create(
            user=user,
            tenant=tenant_b,
            role='viewer',
            is_default=True,
        )

        defaults = TenantMembership.objects.filter(user=user, is_default=True)
        self.assertEqual(defaults.count(), 1)
        self.assertEqual(defaults.get().tenant, tenant_b)


@override_settings(TENANT_SESSION_KEY='active_tenant_id')
class PlatformTenantResolutionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant, _ = Tenant.objects.get_or_create(
            slug='demo',
            defaults={'name': 'Demo d.o.o.'},
        )
        cls.user = User.objects.create_user(username='member', password='test-pass')
        TenantMembership.objects.create(
            user=cls.user,
            tenant=cls.tenant,
            role='owner',
            is_default=True,
        )

    def setUp(self):
        self.factory = RequestFactory()

    def test_default_membership_resolved_for_authenticated_user(self):
        request = self.factory.get('/')
        request.user = self.user
        request.session = {}
        tenant = resolve_platform_tenant(request)
        self.assertEqual(tenant, self.tenant)

    def test_session_tenant_takes_precedence(self):
        other = Tenant.objects.create(slug='other', name='Other Co')
        request = self.factory.get('/')
        request.user = self.user
        request.session = {'active_tenant_id': other.pk}
        tenant = resolve_platform_tenant(request)
        self.assertEqual(tenant, other)


@override_settings(ALLOWED_HOSTS=['.racunai.hr', 'erp.finestar.hr'])
class DynamicAllowedHostsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.custom = Tenant.objects.create(
            slug='customco',
            name='Custom Co',
            custom_domain='erp.customco.hr',
        )

    def setUp(self):
        invalidate_custom_domain_cache()
        cache.clear()

    def test_custom_domain_cached(self):
        domains = get_cached_custom_domains()
        self.assertIn('erp.customco.hr', domains)

    def test_middleware_extends_allowed_hosts(self):
        from django.conf import settings

        factory = RequestFactory()
        middleware = DynamicAllowedHostsMiddleware(lambda r: r)
        request = factory.get('/', HTTP_HOST='erp.customco.hr')
        original = list(settings.ALLOWED_HOSTS)
        middleware(request)
        self.assertIn('erp.customco.hr', settings.ALLOWED_HOSTS)
        settings.ALLOWED_HOSTS = original


class TenantInvitationTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(slug='inv', name='Invite Co')
        self.user = User.objects.create_user(
            username='invitee',
            email='invitee@example.com',
            password='test-pass',
        )

    def test_accept_creates_membership(self):
        invitation = TenantInvitation.objects.create(
            tenant=self.tenant,
            email='invitee@example.com',
            role='accountant',
        )
        membership = invitation.accept(self.user)
        self.assertEqual(membership.role, 'accountant')
        self.assertEqual(membership.tenant, self.tenant)
        invitation.refresh_from_db()
        self.assertEqual(invitation.status, 'accepted')

    def test_expired_invitation_rejected(self):
        from django.utils import timezone
        from datetime import timedelta

        invitation = TenantInvitation.objects.create(
            tenant=self.tenant,
            email='invitee@example.com',
            role='viewer',
            expires_at=timezone.now() - timedelta(days=1),
        )
        with self.assertRaises(ValidationError):
            invitation.accept(self.user)


class TenantPermissionsTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(slug='perm', name='Perm Co')
        self.viewer = User.objects.create_user(username='viewer', password='x')
        TenantMembership.objects.create(
            user=self.viewer, tenant=self.tenant, role='viewer',
        )

    def test_viewer_cannot_write(self):
        self.assertFalse(role_can_write(get_role(self.viewer, self.tenant)))

    def test_accountant_can_write_accounting(self):
        self.assertTrue(role_can_write_model('accountant', 'accounting'))
        self.assertFalse(role_can_write_model('accountant', 'tenants'))


class AdminBrandingTests(TestCase):
    def test_admin_site_uses_racunai_branding(self):
        self.assertEqual(admin.site.site_header, SITE_HEADER)
        self.assertEqual(admin.site.site_title, SITE_TITLE)
        self.assertEqual(admin.site.index_title, INDEX_TITLE)
        self.assertEqual(SITE_HEADER, 'racunAI')
        self.assertEqual(SITE_TITLE, 'racunAI')

    def test_tenant_host_shows_tenant_name_in_context(self):
        tenant = Tenant.objects.create(slug='brandco', name='Brand Co d.o.o.')
        request = RequestFactory().get('/admin/', HTTP_HOST='brandco.racunai.hr')
        request.is_platform_admin = False
        request.tenant = tenant

        context = admin_branding_context(request)
        self.assertEqual(context['admin_site_header'], 'racunAI')
        self.assertIn('branding/racunai-logo', context['admin_logo_url'])
        self.assertEqual(context['admin_tenant_name'], 'Brand Co d.o.o.')

    def test_platform_admin_hides_tenant_name_in_context(self):
        tenant = Tenant.objects.create(slug='brandco', name='Brand Co d.o.o.')
        request = RequestFactory().get('/admin/', HTTP_HOST='admin.racunai.hr')
        request.is_platform_admin = True
        request.tenant = tenant

        context = admin_branding(request)
        self.assertEqual(context['admin_site_header'], 'racunAI')
        self.assertIn('branding/racunai-logo', context['admin_logo_url'])
        self.assertIsNone(context['admin_tenant_name'])


@override_settings(
    TENANT_PLATFORM_DOMAIN='racunai.hr',
    ALLOWED_HOSTS=['admin.racunai.hr'],
    SECURE_SSL_REDIRECT=False,
    TURNSTILE_VERIFY_ENABLED=False,
)
class AuthApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(slug='authco', name='Auth Co')
        cls.user = User.objects.create_user(
            username='authuser',
            email='auth@example.com',
            password='test-pass',
        )
        TenantMembership.objects.create(
            user=cls.user,
            tenant=cls.tenant,
            role='owner',
            is_default=True,
        )

    def test_token_obtain_and_me(self):
        token_response = self.client.post(
            '/api/auth/token/',
            {'username': 'authuser', 'password': 'test-pass'},
            content_type='application/json',
            HTTP_HOST='admin.racunai.hr',
        )
        self.assertEqual(token_response.status_code, 200)
        access = token_response.json()['access']

        me_response = self.client.get(
            '/api/auth/me/',
            HTTP_AUTHORIZATION=f'Bearer {access}',
            HTTP_HOST='admin.racunai.hr',
        )
        self.assertEqual(me_response.status_code, 200)
        data = me_response.json()
        self.assertEqual(data['user']['username'], 'authuser')
        self.assertEqual(len(data['tenants']), 1)
        self.assertEqual(data['tenants'][0]['slug'], 'authco')
        self.assertIn('authco.racunai.hr', data['tenants'][0]['admin_url'])

    def test_invalid_credentials_rejected(self):
        response = self.client.post(
            '/api/auth/token/',
            {'username': 'authuser', 'password': 'wrong'},
            content_type='application/json',
            HTTP_HOST='admin.racunai.hr',
        )
        self.assertEqual(response.status_code, 401)


@override_settings(
    ALLOWED_HOSTS=['admin.racunai.hr', 'finestar.racunai.hr'],
    SECURE_SSL_REDIRECT=False,
    TURNSTILE_VERIFY_ENABLED=True,
    TURNSTILE_SITE_KEY='test-site-key',
    TURNSTILE_SECRET_KEY='test-secret-key',
    TURNSTILE_ADMIN_HOSTS=['admin.racunai.hr'],
)
class AdminLoginTurnstileTests(TestCase):
    def test_login_page_renders_turnstile(self):
        response = self.client.get('/admin/login/', HTTP_HOST='admin.racunai.hr')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'challenges.cloudflare.com')
        self.assertContains(response, 'id="turnstile-container"')
        self.assertContains(response, 'sitekey:')
        self.assertContains(response, 'disabled')

    def test_tenant_admin_login_skips_turnstile(self):
        response = self.client.get('/admin/login/', HTTP_HOST='finestar.racunai.hr')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="turnstile-container"')
        self.assertNotContains(response, 'challenges.cloudflare.com')

    def test_login_without_turnstile_rejected(self):
        User.objects.create_user(username='staff', password='pass', is_staff=True)
        response = self.client.post(
            '/admin/login/',
            {'username': 'staff', 'password': 'pass'},
            HTTP_HOST='admin.racunai.hr',
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'CAPTCHA je obavezna')
