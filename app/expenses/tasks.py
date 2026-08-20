from celery import shared_task


@shared_task(name='expenses.execute_incoming_invoice_import')
def execute_incoming_invoice_import_task(run_id: int) -> dict:
    from domains.purchasing.services.invoice_import import execute_invoice_import

    run = execute_invoice_import(run_id)
    return {
        'import_id': run.pk,
        'status': run.status,
        'last_error': run.last_error,
    }
