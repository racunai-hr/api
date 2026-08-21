from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator

MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024  # 10 MB

pdf_extension_validator = FileExtensionValidator(
    allowed_extensions=['pdf'],
    message='Dozvoljen je samo PDF format.',
)

invoice_file_extension_validator = FileExtensionValidator(
    allowed_extensions=['pdf', 'jpg', 'jpeg', 'png'],
    message='Dozvoljeni formati: PDF, JPG, PNG.',
)


def validate_pdf_file_size(value):
    if value.size > MAX_ATTACHMENT_SIZE:
        raise ValidationError(
            f'Datoteka je prevelika ({value.size} B). Maksimalno {MAX_ATTACHMENT_SIZE // (1024 * 1024)} MB.'
        )


def validate_invoice_file_size(value):
    validate_pdf_file_size(value)


def _read_header(value, n: int) -> bytes:
    header = value.read(n)
    value.seek(0)
    return header


def validate_pdf_content(value):
    header = _read_header(value, 5)
    if header != b'%PDF-':
        raise ValidationError('Datoteka nije valjani PDF dokument.')


def detect_invoice_kind(header: bytes) -> str | None:
    if header.startswith(b'%PDF-'):
        return 'pdf'
    if header.startswith(b'\xff\xd8\xff'):
        return 'jpeg'
    if header.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png'
    return None


def validate_invoice_file_content(value):
    header = _read_header(value, 8)
    if detect_invoice_kind(header) is None:
        raise ValidationError('Datoteka mora biti PDF, JPG ili PNG.')
