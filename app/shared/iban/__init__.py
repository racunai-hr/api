"""IBAN normalization — no MOD-97 validation yet."""


def normalize_iban(value: str) -> str:
    """Remove spaces and uppercase an IBAN string."""
    return (value or '').replace(' ', '').upper()


__all__ = ['normalize_iban']
