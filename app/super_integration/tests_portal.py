from django.test import SimpleTestCase, override_settings

from super_integration.portal import build_super_external_view_url


class SuperPortalUrlTests(SimpleTestCase):
    @override_settings(SUPER_PORTAL_BASE_URL='https://moj.super.hr')
    def test_builds_view_details_url(self):
        url = build_super_external_view_url('7a8e295b-5859-4a39-a289-3b92b1ae7469')
        self.assertEqual(
            url,
            'https://moj.super.hr/hr/Client/Invoice/ViewDetails/7a8e295b-5859-4a39-a289-3b92b1ae7469',
        )

    @override_settings(SUPER_PORTAL_BASE_URL='')
    def test_fail_closed_without_base(self):
        self.assertIsNone(build_super_external_view_url('abc'))

    @override_settings(SUPER_PORTAL_BASE_URL='https://moj.super.hr/')
    def test_fail_closed_without_guid(self):
        self.assertIsNone(build_super_external_view_url(''))
        self.assertIsNone(build_super_external_view_url(None))
