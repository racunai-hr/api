from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from banking.importers.base import ImportResultBuilder
from banking.models import BankStatement, BankTransaction
from banking.parsers.types import ParsedBankStatement, ParsedBankTransaction
from payments.models import BankAccount


class Camt053Importer:
    format_key = 'camt053'

    def import_parsed(
        self,
        *,
        tenant,
        user,
        statements: list[ParsedBankStatement],
        import_id: str,
    ) -> ImportResultBuilder:
        builder = ImportResultBuilder()

        for parsed in statements:
            builder.statements_processed += 1
            try:
                with transaction.atomic():
                    bank_account = self._resolve_account(tenant, parsed.iban)
                    if bank_account is None:
                        raise ValueError(
                            f'Nepoznat IBAN {parsed.iban or "(prazan)"} za tenant {tenant.slug}'
                        )

                    statement, created = BankStatement.all_objects.get_or_create(
                        tenant=tenant,
                        bank_account=bank_account,
                        statement_number=parsed.statement_number,
                        defaults={
                            'statement_date': parsed.statement_date,
                            'opening_balance': parsed.opening_balance,
                            'closing_balance': parsed.closing_balance,
                            'status': 'imported',
                            'imported_by': user,
                        },
                    )

                    if created:
                        builder.statements_created += 1
                    else:
                        builder.statements_updated += 1
                        self._apply_balance_update(statement, parsed, builder)

                    for tx in parsed.transactions:
                        self._persist_transaction(
                            tenant=tenant,
                            statement=statement,
                            parsed_statement=parsed,
                            tx=tx,
                            builder=builder,
                        )
            except Exception as exc:
                builder.add_error(f'Izvod {parsed.statement_number}: {exc}')

        return builder

    def _resolve_account(self, tenant, iban: str) -> BankAccount | None:
        if not iban:
            return None
        return BankAccount.all_objects.filter(tenant=tenant, iban=iban).first()

    def _apply_balance_update(
        self,
        statement: BankStatement,
        parsed: ParsedBankStatement,
        builder: ImportResultBuilder,
    ) -> None:
        if statement.status in ('reconciled', 'archived'):
            if self._parsed_differs(statement, parsed):
                builder.add_warning(
                    f'Izvod {parsed.statement_number}: status {statement.status}, salda nisu ažurirani'
                )
            return

        update_fields: list[str] = []

        if self._is_empty_balance(statement.opening_balance):
            if statement.opening_balance != parsed.opening_balance:
                statement.opening_balance = parsed.opening_balance
                update_fields.append('opening_balance')
        elif statement.opening_balance != parsed.opening_balance:
            builder.add_warning(
                f'Izvod {parsed.statement_number}: početno stanje se razlikuje '
                f'({statement.opening_balance} vs {parsed.opening_balance}), nije ažurirano'
            )

        if self._is_empty_balance(statement.closing_balance):
            if statement.closing_balance != parsed.closing_balance:
                statement.closing_balance = parsed.closing_balance
                update_fields.append('closing_balance')
        elif statement.closing_balance != parsed.closing_balance:
            builder.add_warning(
                f'Izvod {parsed.statement_number}: završno stanje se razlikuje '
                f'({statement.closing_balance} vs {parsed.closing_balance}), nije ažurirano'
            )

        if update_fields:
            statement.save(update_fields=update_fields)

    def _parsed_differs(self, statement: BankStatement, parsed: ParsedBankStatement) -> bool:
        return (
            statement.opening_balance != parsed.opening_balance
            or statement.closing_balance != parsed.closing_balance
            or statement.statement_date != parsed.statement_date
        )

    @staticmethod
    def _is_empty_balance(value: Decimal) -> bool:
        return value is None or value == Decimal('0')

    def _persist_transaction(
        self,
        *,
        tenant,
        statement: BankStatement,
        parsed_statement: ParsedBankStatement,
        tx: ParsedBankTransaction,
        builder: ImportResultBuilder,
    ) -> None:
        builder.transactions_processed += 1

        if tx.used_fallback_external_id:
            builder.add_warning(
                f'Izvod {parsed_statement.statement_number}: transakcija bez AcctSvcrRef koristi fallback hash'
            )

        _, created = BankTransaction.all_objects.get_or_create(
            tenant=tenant,
            bank_statement=statement,
            external_id=tx.external_id,
            defaults={
                'transaction_date': tx.transaction_date,
                'value_date': tx.value_date,
                'amount': tx.amount,
                'currency': parsed_statement.currency,
                'transaction_type': tx.transaction_type,
                'description': tx.description,
                'reference': tx.reference,
                'counterparty_name': tx.counterparty_name,
                'counterparty_iban': tx.counterparty_iban,
            },
        )
        if created:
            builder.transactions_created += 1
        else:
            builder.transactions_skipped += 1
