from unittest.mock import MagicMock, patch

import pytest

from fiscal_gateway.client.mps_client import MpsClient, MpsClientError


def test_mps_client_health(settings):
    settings.MPS_SERVICE_URL = 'http://mps.test'
    with patch('fiscal_gateway.client.mps_client.requests.request') as mock_request:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'status': 'ok'}
        mock_request.return_value = mock_response

        health = MpsClient().health()
        assert health.status == 'ok'


def test_mps_client_missing_base_url(settings):
    settings.MPS_SERVICE_URL = ''
    with pytest.raises(MpsClientError, match='MPS_SERVICE_URL'):
        MpsClient().health()
