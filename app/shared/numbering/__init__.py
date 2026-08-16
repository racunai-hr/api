"""Document numbering helpers — stub."""


def pad_sequence(value: int, *, width: int = 4) -> str:
    return str(value).zfill(width)


__all__ = ['pad_sequence']
