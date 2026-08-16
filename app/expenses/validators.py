from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator

MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024  # 10 MB

pdf_extension_validator = FileExtensionValidator(
    allowed_extensions=['pdf'],
    message='Dozvoljen je samo PDF format.',
)


def validate_pdf_file_size(value):
    if value.size > MAX_ATTACHMENT_SIZE:
        raise ValidationError(
            f'Datoteka je prevelika ({value.size} B). Maksimalno {MAX_ATTACHMENT_SIZE // (1024 * 1024)} MB.'
        )


def validate_pdf_content(value):
    header = value.read(5)
    value.seek(0)
    if header != b'%PDF-':
        raise ValidationError('Datoteka nije valjani PDF dokument.')
