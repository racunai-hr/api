from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

import requests
from django.conf import settings

from domains.purchasing.ai.render import RenderedPage, page_data_url
from domains.purchasing.ai.schema import EXTRACT_PROMPT, INVOICE_JSON_SCHEMA, OCR_SCHEMA_VERSION


class ExtractionError(Exception):
    """Vision/OCR extraction failed."""


class ExtractionTimeout(ExtractionError):
    """Provider timed out."""


class InvalidExtraction(ExtractionError):
    """Structured output missing or unparseable."""


@dataclass(frozen=True)
class ExtractionResult:
    payload: dict
    provider: str
    model: str
    schema_version: str


class InvoiceExtractionProvider(Protocol):
    def extract(self, pages: list[RenderedPage], *, filename: str) -> ExtractionResult: ...


class FakeInvoiceExtractionProvider:
    provider_name = 'fake'

    def extract(self, pages: list[RenderedPage], *, filename: str) -> ExtractionResult:
        behavior = getattr(settings, 'PURCHASING_OCR_FAKE_BEHAVIOR', 'ok')
        if behavior == 'timeout':
            raise ExtractionTimeout('OpenAI timeout')
        if behavior == 'invalid':
            raise InvalidExtraction('Nevaljani Structured Output')
        payload = getattr(settings, 'PURCHASING_OCR_FAKE_PAYLOAD', None)
        if not isinstance(payload, dict) or not payload:
            raise InvalidExtraction('Fake OCR payload nije postavljen.')
        return ExtractionResult(
            payload=payload,
            provider=self.provider_name,
            model='fake',
            schema_version=OCR_SCHEMA_VERSION,
        )


class OpenAIInvoiceExtractionProvider:
    provider_name = 'openai'

    def extract(self, pages: list[RenderedPage], *, filename: str) -> ExtractionResult:
        api_key = (getattr(settings, 'OPENAI_API_KEY', '') or '').strip()
        if not api_key:
            raise ExtractionError('OPENAI_API_KEY nije postavljen.')
        model = getattr(settings, 'OPENAI_OCR_MODEL', 'gpt-4.1-mini')
        timeout = int(getattr(settings, 'OPENAI_OCR_TIMEOUT_SECONDS', 60))
        content = [{'type': 'input_text', 'text': EXTRACT_PROMPT}]
        for page in pages:
            content.append({'type': 'input_image', 'image_url': page_data_url(page)})
        body = {
            'model': model,
            'input': [{'role': 'user', 'content': content}],
            'text': {
                'format': {
                    'type': 'json_schema',
                    'name': 'incoming_invoice_v1',
                    'strict': True,
                    'schema': INVOICE_JSON_SCHEMA,
                }
            },
        }
        try:
            response = requests.post(
                'https://api.openai.com/v1/responses',
                headers={
                    'Authorization': f'Bearer {api_key}',
                    'Content-Type': 'application/json',
                },
                json=body,
                timeout=timeout,
            )
        except requests.Timeout as exc:
            raise ExtractionTimeout('OpenAI timeout') from exc
        except requests.RequestException as exc:
            raise ExtractionError('OpenAI zahtjev nije uspio.') from exc
        if response.status_code >= 400:
            raise ExtractionError(f'OpenAI HTTP {response.status_code}')
        try:
            data = response.json()
        except ValueError as exc:
            raise InvalidExtraction('OpenAI odgovor nije JSON.') from exc
        text = _output_text(data)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise InvalidExtraction('Nevaljani Structured Output') from exc
        if not isinstance(payload, dict):
            raise InvalidExtraction('Nevaljani Structured Output')
        return ExtractionResult(
            payload=payload,
            provider=self.provider_name,
            model=str(data.get('model') or model),
            schema_version=OCR_SCHEMA_VERSION,
        )


def _output_text(data: dict) -> str:
    direct = data.get('output_text')
    if isinstance(direct, str) and direct.strip():
        return direct
    chunks: list[str] = []
    for item in data.get('output') or []:
        if not isinstance(item, dict):
            continue
        for part in item.get('content') or []:
            if not isinstance(part, dict):
                continue
            text = part.get('text')
            if isinstance(text, str):
                chunks.append(text)
    if not chunks:
        raise InvalidExtraction('OpenAI odgovor nema tekst.')
    return '\n'.join(chunks)


def get_extraction_provider() -> InvoiceExtractionProvider:
    name = (getattr(settings, 'PURCHASING_OCR_PROVIDER', 'openai') or 'openai').strip().lower()
    if name == 'fake':
        return FakeInvoiceExtractionProvider()
    return OpenAIInvoiceExtractionProvider()
