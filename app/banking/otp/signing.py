from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from django.conf import settings

from fiscal_gateway.client.certificate import CertificateError, load_p12


class OtpSigningError(Exception):
    pass


@dataclass(frozen=True)
class AuthCertMaterial:
    certificate_b64: str


@dataclass(frozen=True)
class SigningMaterial:
    private_key: object
    certificate: x509.Certificate
    certificate_b64: str


def _cert_der_b64(certificate_pem: bytes) -> str:
    cert = x509.load_pem_x509_certificate(certificate_pem)
    return base64.b64encode(cert.public_bytes(serialization.Encoding.DER)).decode()


@lru_cache(maxsize=2)
def _load_auth_material_cached(cert_path: str, password: str) -> AuthCertMaterial:
    material = load_p12(cert_path, password)
    return AuthCertMaterial(certificate_b64=_cert_der_b64(material.certificate_pem))


@lru_cache(maxsize=2)
def _load_signing_material_cached(cert_path: str, password: str) -> SigningMaterial:
    material = load_p12(cert_path, password)
    certificate = x509.load_pem_x509_certificate(material.certificate_pem)
    private_key = serialization.load_pem_private_key(material.private_key_pem, password=None)
    return SigningMaterial(
        private_key=private_key,
        certificate=certificate,
        certificate_b64=_cert_der_b64(material.certificate_pem),
    )


def load_auth_material() -> AuthCertMaterial:
    path = settings.OTP_CERT_PATH
    password = settings.OTP_CERT_PASSWORD
    try:
        return _load_auth_material_cached(path, password)
    except CertificateError as exc:
        raise OtpSigningError(str(exc)) from exc


def load_signing_material() -> SigningMaterial:
    path = getattr(settings, 'OTP_SIGNATURE_CERT_PATH', settings.OTP_CERT_PATH)
    password = getattr(settings, 'OTP_SIGNATURE_CERT_PASSWORD', settings.OTP_CERT_PASSWORD)
    try:
        return _load_signing_material_cached(path, password)
    except CertificateError as exc:
        raise OtpSigningError(str(exc)) from exc


def serialize_body(*, json_payload=None, data=None) -> bytes:
    if json_payload is not None:
        return json.dumps(json_payload, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    if data is None:
        return b''
    if isinstance(data, bytes):
        return data
    return str(data).encode('utf-8')


def build_digest(body: bytes) -> str:
    digest = hashlib.sha256(body).digest()
    return f'SHA-256={base64.b64encode(digest).decode()}'


def build_key_id(certificate: x509.Certificate) -> str:
    serial = format(certificate.serial_number, 'X')
    issuer = certificate.issuer.rfc4514_string()
    return f'SN={serial},CA={issuer}'


def _header_value(headers: dict[str, str], name: str) -> str | None:
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value
    return None


def resolve_sign_header_names(headers: dict[str, str]) -> str:
    names = ['Digest', 'X-Request-ID']
    for optional in ('PSU-ID', 'TPP-Redirect-URI', 'Consent-ID'):
        if _header_value(headers, optional):
            names.append(optional)
    return ' '.join(names)


def build_signing_string(header_names: str, headers: dict[str, str]) -> str:
    lines = []
    for name in header_names.split(' '):
        value = _header_value(headers, name)
        if value is None:
            raise OtpSigningError(f'Nedostaje header za potpis: {name}')
        lines.append(f'{name.lower()}: {value}')
    return '\n'.join(lines) + '\n'


def build_signature_header(*, header_names: str, headers: dict[str, str], signing_material: SigningMaterial) -> str:
    signing_string = build_signing_string(header_names, headers)
    signature = signing_material.private_key.sign(
        signing_string.encode('utf-8'),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    sig_b64 = base64.b64encode(signature).decode()
    key_id = build_key_id(signing_material.certificate)
    return (
        f'keyId="{key_id}",algorithm="rsa-sha256",'
        f'headers="{header_names}",signature="{sig_b64}"'
    )


def apply_api_request_signature(headers: dict[str, str], body: bytes) -> dict[str, str]:
    auth_material = load_auth_material()
    signing_material = load_signing_material()
    signed = dict(headers)
    signed['Digest'] = build_digest(body)
    signed['TPP-Authentication-Certificate'] = auth_material.certificate_b64
    signed['TPP-Signature-Certificate'] = signing_material.certificate_b64
    header_names = resolve_sign_header_names(signed)
    signed['Signature'] = build_signature_header(
        header_names=header_names,
        headers=signed,
        signing_material=signing_material,
    )
    return signed
