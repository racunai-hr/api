"""IBAN normalization — no MOD-97 validation yet."""


def normalize_iban(value: str) -> str:
    """Remove spaces and uppercase an IBAN string."""
    return (value or '').replace(' ', '').upper()


def format_iban_display(value: str | None) -> str:
    """Group a normalized IBAN into 4-character blocks for display."""
    compact = normalize_iban(value)
    if not compact:
        return ''
    return ' '.join(compact[i : i + 4] for i in range(0, len(compact), 4))


__all__ = ['format_iban_display', 'normalize_iban']
