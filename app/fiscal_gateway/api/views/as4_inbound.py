from __future__ import annotations

import logging

from django.http import HttpResponse, HttpResponseBadRequest
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from fiscal_gateway.client.domibus_push import (
    DomibusPushParseError,
    build_push_ack_response,
    parse_domibus_push,
)
from fiscal_gateway.services.inbound_as4 import InboundAs4Error, handle_inbound_push

logger = logging.getLogger(__name__)


@method_decorator(csrf_exempt, name='dispatch')
class As4InboundPushView(View):
    """Domibus WS plugin push endpoint (RECEIVE_SUCCESS / RECEIVE_FAIL)."""

    def post(self, request, *args, **kwargs):
        raw = request.body
        if not raw:
            return HttpResponseBadRequest('Prazan SOAP zahtjev')

        try:
            message = parse_domibus_push(raw)
            result = handle_inbound_push(message)
            logger.info(
                'AS4 inbound push handled operation=%s invoice=%s accepted=%s skipped=%s',
                message.operation,
                result.invoice_id,
                result.accepted,
                result.skipped,
            )
            response_body = build_push_ack_response(
                message.operation,
                message_id=result.response_message_id or message.message_id,
            )
            return HttpResponse(
                response_body,
                content_type='application/soap+xml; charset=UTF-8',
                status=200,
            )
        except DomibusPushParseError as exc:
            logger.warning('AS4 inbound parse error: %s', exc)
            return HttpResponseBadRequest(str(exc))
        except InboundAs4Error as exc:
            logger.error('AS4 inbound handler error: %s', exc)
            return HttpResponseBadRequest(str(exc))
        except Exception:
            logger.exception('AS4 inbound unexpected error')
            raise
