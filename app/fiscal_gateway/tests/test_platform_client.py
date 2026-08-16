"""Tests for FiscalPlatformClient."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from fiscal_gateway.client.platform_client import FiscalPlatformClient, FiscalPlatformConfig


@override_settings(
    FISKAL_PLATFORM_URL='http://fiskal-api:8000',
    FISKAL_PLATFORM_API_TOKEN='test-token',
    FISKAL_PLATFORM_PROFILE_SLUG='finestar',
)
class FiscalPlatformClientTests(TestCase):
    def setUp(self):
        self.config = FiscalPlatformConfig(
            tenant=MagicMock(slug='finestar'),
            organization_oib='36619131370',
            fiscal_profile_slug='finestar',
            api_base_url='http://fiskal-api:8000',
            api_token='test-token',
            poll_timeout_seconds=5,
            poll_interval_seconds=0.01,
        )
        self.client = FiscalPlatformClient(self.config)

    @patch('fiscal_gateway.client.platform_client.requests.Session')
    def test_submit_fiscalization_posts_payload(self, mock_session_cls):
        session = MagicMock()
        mock_session_cls.return_value = session
        response = MagicMock(status_code=202)
        response.json.return_value = {
            'request_id': 'req-1',
            'correlation_id': 'corr-1',
            'status': 'received',
        }
        session.post.return_value = response

        client = FiscalPlatformClient(self.config)
        result = client.submit_fiscalization(
            payload={'document_number': '1-P1-1'},
            correlation_id='corr-1',
            external_reference='1-P1-1',
        )

        self.assertEqual(result['request_id'], 'req-1')
        session.post.assert_called_once()
        posted_json = session.post.call_args.kwargs['json']
        self.assertEqual(posted_json['fiscal_profile_slug'], 'finestar')
        self.assertEqual(posted_json['organization_oib'], '36619131370')

    @patch('fiscal_gateway.client.platform_client.requests.Session')
    def test_submit_and_wait_polls_until_accepted(self, mock_session_cls):
        session = MagicMock()
        mock_session_cls.return_value = session

        accepted = MagicMock(status_code=202)
        accepted.json.return_value = {'request_id': 'req-2', 'status': 'received'}

        pending = MagicMock(status_code=200)
        pending.json.return_value = {'status': 'sent', 'jir': None}

        done = MagicMock(status_code=200)
        done.json.return_value = {'status': 'accepted', 'jir': 'JIR-123'}

        session.post.return_value = accepted
        session.get.side_effect = [pending, done]

        client = FiscalPlatformClient(self.config)
        result = client.submit_and_wait(payload={'document_number': '2-P1-2'})

        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(result['jir'], 'JIR-123')
        self.assertEqual(session.get.call_count, 2)

    def test_dry_run_skips_http(self):
        result = self.client.submit_and_wait(
            payload={'document_number': 'dry'},
            dry_run=True,
        )
        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(result['jir'], 'DRY-RUN-JIR')
        self.assertTrue(result['dry_run'])
