from django.core.management.base import BaseCommand

from fiscal_gateway.config_utils import FiscalConfigError, get_active_fiscal_config
from fiscal_gateway.services.outbound import submit_outgoing_invoice
from fiscal_gateway.signing.builders import demo_payload_from_options
from integrations.audit import log_audit_step, new_correlation_id
from integrations.constants import IntegrationProvider, IntegrationType
from integrations.models import IntegrationAuditLog
from tenants.models import Tenant


class Command(BaseCommand):
    help = 'Pošalji demo EvidentirajERacunZahtjev na CIS (testni podaci / PTS parametri)'

    def add_arguments(self, parser):
        parser.add_argument('--tenant', type=str, required=True, help='Tenant slug (npr. finestar)')
        parser.add_argument('--dry-run', action='store_true', help='Potpiši i validiraj, ne šalji na CIS')
        parser.add_argument('--document-number', type=str, default='')
        parser.add_argument('--issue-date', type=str, default='')
        parser.add_argument('--due-date', type=str, default='')
        parser.add_argument('--issuer-oib', type=str, default='')
        parser.add_argument('--operator-oib', type=str, default='')
        parser.add_argument('--recipient-oib', type=str, default='')
        parser.add_argument('--recipient-name', type=str, default='')
        parser.add_argument('--business-process', type=str, default='')
        parser.add_argument('--invoice-kind', type=str, default='I', help='I=izlazni, U=ulazni')
        parser.add_argument('--issuer-name', type=str, default='')
        parser.add_argument(
            '--cis-env',
            type=str,
            default='',
            help='demo | prod | pts (PTS upute koriste port 8511)',
        )

    def handle(self, *args, **options):
        tenant = Tenant.objects.get(slug=options['tenant'], is_active=True)
        try:
            config = get_active_fiscal_config(tenant)
        except FiscalConfigError as exc:
            self.stderr.write(self.style.ERROR(str(exc)))
            return

        if options['cis_env']:
            config.cis_env = options['cis_env']

        payload_options = {
            key: options[key.replace('-', '_')]
            for key in (
                'document_number',
                'issue_date',
                'due_date',
                'issuer_oib',
                'issuer_name',
                'operator_oib',
                'recipient_oib',
                'recipient_name',
                'business_process',
                'invoice_kind',
            )
            if options.get(key.replace('-', '_'))
        }
        payload = demo_payload_from_options(payload_options)
        correlation_id = new_correlation_id()

        try:
            log = submit_outgoing_invoice(
                config,
                payload,
                dry_run=options['dry_run'],
                correlation_id=correlation_id,
            )
        except Exception as exc:
            log_audit_step(
                tenant=tenant,
                step=IntegrationAuditLog.STEP_FISCAL_FAILED,
                status=IntegrationAuditLog.STATUS_FAILED,
                correlation_id=correlation_id,
                integration_type=IntegrationType.FISCALIZATION,
                provider=IntegrationProvider.CIS,
                detail={'messages': [str(exc)], 'cis_env': config.cis_env},
            )
            self.stderr.write(self.style.ERROR(str(exc)))
            self.stdout.write(f'correlation_id: {correlation_id}')
            return

        if log.status == log.STATUS_SUCCESS:
            log_audit_step(
                tenant=tenant,
                step=IntegrationAuditLog.STEP_FISCALIZED,
                status=IntegrationAuditLog.STATUS_SUCCESS,
                correlation_id=correlation_id,
                integration_type=IntegrationType.FISCALIZATION,
                provider=IntegrationProvider.CIS,
                fiscal_log=log,
                detail={'cis_env': config.cis_env, 'dry_run': options['dry_run']},
            )
        else:
            log_audit_step(
                tenant=tenant,
                step=IntegrationAuditLog.STEP_FISCAL_FAILED,
                status=IntegrationAuditLog.STATUS_FAILED,
                correlation_id=correlation_id,
                integration_type=IntegrationType.FISCALIZATION,
                provider=IntegrationProvider.CIS,
                fiscal_log=log,
                detail={
                    'cis_env': config.cis_env,
                    'error_code': log.error_code,
                    'error_message': log.error_message,
                },
            )

        if log.status == log.STATUS_SUCCESS:
            self.stdout.write(self.style.SUCCESS(f'Status: {log.status}'))
        else:
            self.stdout.write(self.style.WARNING(f'Status: {log.status}'))

        if log.jir:
            self.stdout.write(f'JIR: {log.jir}')
        if log.cis_request_id:
            self.stdout.write(f'idZahtjeva: {log.cis_request_id}')
        if log.error_code:
            self.stdout.write(f'Greška: {log.error_code} — {log.error_message}')
        self.stdout.write(f'Log ID: {log.pk}')
        self.stdout.write(f'correlation_id: {correlation_id}')
