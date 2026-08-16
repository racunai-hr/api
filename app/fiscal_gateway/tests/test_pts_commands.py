from __future__ import annotations

import subprocess
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command

FIXTURES = Path(__file__).resolve().parent.parent / 'fixtures'
INVOICE_XML = FIXTURES / 'pts_invoice.xml'


@pytest.mark.pts
class TestPtsEracunLookupCommand:
    @patch('fiscal_gateway.management.commands.pts_eracun_lookup.prepare_eracun_send')
    def test_pts_eracun_lookup_success(self, mock_prepare, settings, tmp_path):
        from fiscal_gateway.client.eracun_lookup import EracunLookupResult, EracunParticipant

        xml_copy = tmp_path / 'invoice.xml'
        xml_copy.write_bytes(INVOICE_XML.read_bytes())
        mock_prepare.return_value = EracunLookupResult(
            invoice_id='13062026-TP-5054',
            supplier=EracunParticipant(oib='11528564544', name='Supplier'),
            customer=EracunParticipant(oib='36619131370', name='Customer'),
            ams_naptr='naptr',
            mps_url='https://mps.example/services',
            as4_endpoint='https://as4.example',
            responder_ap_party_id='domieracuntest',
        )

        out = StringIO()
        call_command('pts_eracun_lookup', xml=str(xml_copy), stdout=out)
        output = out.getvalue()
        assert 'lookup OK' in output
        assert '13062026-TP-5054' in output


@pytest.mark.pts
class TestPtsEracunSendCommand:
    @patch('fiscal_gateway.management.commands.pts_eracun_send.submit_invoice_via_domibus')
    @patch('fiscal_gateway.management.commands.pts_eracun_send.prepare_eracun_send')
    def test_pts_eracun_send_lookup_only(self, mock_prepare, mock_submit, tmp_path):
        from fiscal_gateway.client.eracun_lookup import EracunLookupResult, EracunParticipant

        xml_copy = tmp_path / 'invoice.xml'
        xml_copy.write_bytes(INVOICE_XML.read_bytes())
        mock_prepare.return_value = EracunLookupResult(
            invoice_id='13062026-TP-5054',
            supplier=EracunParticipant(oib='11528564544'),
            customer=EracunParticipant(oib='36619131370'),
            ams_naptr='naptr',
            mps_url='https://mps.example/services',
            as4_endpoint='https://as4.example',
            responder_ap_party_id='domieracuntest',
        )

        out = StringIO()
        call_command('pts_eracun_send', xml=str(xml_copy), lookup_only=True, stdout=out)
        assert 'lookup OK' in out.getvalue()
        mock_submit.assert_not_called()

    @patch('fiscal_gateway.management.commands.pts_eracun_send.submit_invoice_via_domibus')
    @patch('fiscal_gateway.management.commands.pts_eracun_send.prepare_eracun_send')
    def test_pts_eracun_send_full(self, mock_prepare, mock_submit, tmp_path):
        from fiscal_gateway.client.as4_client import As4SendResult
        from fiscal_gateway.client.eracun_lookup import EracunLookupResult, EracunParticipant

        xml_copy = tmp_path / 'invoice.xml'
        xml_copy.write_bytes(INVOICE_XML.read_bytes())
        mock_prepare.return_value = EracunLookupResult(
            invoice_id='13062026-TP-5054',
            supplier=EracunParticipant(oib='11528564544'),
            customer=EracunParticipant(oib='36619131370'),
            ams_naptr='naptr',
            mps_url='https://mps.example/services',
            as4_endpoint='https://as4.example',
            responder_ap_party_id='domieracuntest',
        )
        mock_submit.return_value = As4SendResult(
            message_id='pts-msg@porezna.hr',
            invoice_id='13062026-TP-5054',
            supplier_oib='11528564544',
            customer_oib='36619131370',
            domibus_response='<ok/>',
        )

        out = StringIO()
        call_command('pts_eracun_send', xml=str(xml_copy), stdout=out)
        assert 'Domibus submit OK' in out.getvalue()
        mock_submit.assert_called_once()


@pytest.mark.pts
class TestPtsMpsAmsCommand:
    @patch('fiscal_gateway.management.commands.pts_mps_ams.MpsClient')
    def test_pts_mps_ams_list(self, mock_client_cls, settings):
        settings.MPS_SERVICE_URL = 'http://mps.test'
        mock_client = MagicMock()
        mock_client.ams_list.return_value = {
            'publisher_id': 'pub-1',
            'participants': [{'full': '9934:36619131370'}],
        }
        mock_client_cls.return_value = mock_client

        out = StringIO()
        call_command('pts_mps_ams', action='list', stdout=out)
        assert 'AMS list OK' in out.getvalue()
        mock_client.ams_list.assert_called_once()

    @patch('fiscal_gateway.management.commands.pts_mps_ams.MpsClient')
    def test_pts_mps_ams_create(self, mock_client_cls, settings):
        settings.MPS_SERVICE_URL = 'http://mps.test'
        mock_client = MagicMock()
        mock_client.ams_create.return_value = {'oib': '36619131370', 'response': 'created'}
        mock_client_cls.return_value = mock_client

        out = StringIO()
        call_command('pts_mps_ams', action='create', oib=['36619131370'], stdout=out)
        assert 'AMS create OK' in out.getvalue()


@pytest.mark.pts
class TestPtsRunbookChecklist:
    """E2E checklist: svi PTS management commandi registrirani + očekivani output format."""

    EXPECTED_COMMANDS = {
        'pts_eracun_lookup': 'lookup OK',
        'pts_eracun_send': 'lookup OK',
        'pts_mps_ams': 'AMS list OK',
        'fiscal_submit_demo': 'tenant',
    }

    def test_pts_commands_registered(self):
        from django.core.management import get_commands

        commands = get_commands()
        for name in self.EXPECTED_COMMANDS:
            assert name in commands, f'Management command {name} nije registriran'

    @patch('fiscal_gateway.management.commands.pts_eracun_lookup.prepare_eracun_send')
    def test_runbook_lookup_output_format(self, mock_prepare, tmp_path):
        from fiscal_gateway.client.eracun_lookup import EracunLookupResult, EracunParticipant

        xml_copy = tmp_path / 'invoice.xml'
        xml_copy.write_bytes(INVOICE_XML.read_bytes())
        mock_prepare.return_value = EracunLookupResult(
            invoice_id='PTS-RUNBOOK-1',
            supplier=EracunParticipant(oib='11528564544'),
            customer=EracunParticipant(oib='36619131370'),
            ams_naptr='naptr',
            mps_url='https://mps.example',
            as4_endpoint='https://as4.example',
            responder_ap_party_id='domieracuntest',
        )
        out = StringIO()
        call_command('pts_eracun_lookup', xml=str(xml_copy), stdout=out)
        output = out.getvalue()
        assert 'lookup OK' in output
        assert 'AS4 endpoint' in output
        assert 'PTS-RUNBOOK-1' in output


@pytest.mark.pts_integration
class TestPtsLiveIntegration:
    """Ručno pokretanje protiv stvarnog PTS okruženja — nikad u default CI."""

    def test_mps_health(self, settings):
        import urllib.request

        base = getattr(settings, 'MPS_SERVICE_URL', '').rstrip('/') or 'http://racunai_mps:8000'
        with urllib.request.urlopen(f'{base}/health', timeout=5) as response:
            assert response.status == 200
            assert b'ok' in response.read().lower()

    def test_django_ready(self):
        import urllib.request

        with urllib.request.urlopen('http://127.0.0.1:8000/api/ready/', timeout=10) as response:
            assert response.status == 200
            body = response.read().decode()
            assert '"status": "ready"' in body

    def test_domibus_info(self, settings):
        import urllib.error
        import urllib.request

        ws_url = getattr(settings, 'DOMIBUS_WS_URL', '').rstrip('/')
        if not ws_url:
            pytest.skip('DOMIBUS_WS_URL nije postavljen')
        info_url = ws_url.replace('/EracunAS4/', '/rest/application/info')
        try:
            with urllib.request.urlopen(info_url, timeout=5) as response:
                assert response.status == 200
        except urllib.error.URLError as exc:
            pytest.skip(f'Domibus /rest/application/info nedostupan: {exc}')

    def test_cis_pts_reachable(self):
        import socket

        sock = socket.create_connection(('cis.porezna-uprava.hr', 8511), timeout=5)
        sock.close()
