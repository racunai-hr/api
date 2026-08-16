"""CLI entry point for domain import lint."""

from __future__ import annotations

import argparse
import sys

from import_lint.scanner import scan_for_import_violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Scan for forbidden cross-domain imports.')
    parser.add_argument(
        '--warn',
        action='store_true',
        help='Warning mode — print violations but exit 0 (CI default until enforced).',
    )
    args = parser.parse_args(argv)

    violations = scan_for_import_violations()
    if not violations:
        print('import_lint: no cross-domain violations found')
        return 0

    print(f'import_lint: {len(violations)} cross-domain violation(s):')
    for violation in violations:
        print(
            f'  {violation.path}:{violation.line}: '
            f'{violation.message}\n'
            f'    {violation.snippet}',
        )

    if args.warn:
        print('import_lint: warning mode — not failing the build')
        return 0

    return 1


if __name__ == '__main__':
    sys.exit(main())
