from unittest.mock import patch

import pytest
from django.test import Client


@pytest.mark.django_db
def test_ready_endpoint_ok():
    client = Client()
    with patch('config.health._check_celery_broker', return_value=(True, 'ok')):
        response = client.get('/api/ready/')
    assert response.status_code == 200
    payload = response.json()
    assert payload['status'] == 'ready'
    assert payload['checks']['database'] == 'ok'


@pytest.mark.django_db
def test_ready_endpoint_fails_when_broker_down():
    client = Client()
    with patch('config.health._check_celery_broker', return_value=(False, 'connection refused')):
        response = client.get('/api/ready/')
    assert response.status_code == 503
    assert response.json()['status'] == 'not_ready'
