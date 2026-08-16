from django.core.management.base import BaseCommand, CommandError

from fiscal_gateway.client.mps_client import MpsClient, MpsClientError


class Command(BaseCommand):
    help = (
        'PTS MPS/AMS — Create, Delete ili List identifikatora u demo AMS-u. '
        'Koristi MpsClient wrapper prema racunAI MPS servisu (MPS_SERVICE_URL). '
        'Primjer: python manage.py pts_mps_ams --action list'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--action',
            choices=['create', 'delete', 'list'],
            required=True,
            help='AMS operacija',
        )
        parser.add_argument(
            '--oib',
            type=str,
            action='append',
            help='OIB poreznog obveznika (obavezno za create/delete; može više puta)',
        )

    def handle(self, *args, **options):
        client = MpsClient()
        action = options['action']
        oibs = [o.strip() for o in (options.get('oib') or []) if o and o.strip()]

        if action in {'create', 'delete'} and not oibs:
            raise CommandError(f'--oib je obavezan za action={action}')

        if action == 'list':
            self._run_list(client)
            return

        failures = 0
        for oib in oibs:
            try:
                if action == 'create':
                    self._run_create(client, oib)
                else:
                    self._run_delete(client, oib)
            except CommandError as exc:
                failures += 1
                self.stderr.write(self.style.ERROR(str(exc)))
        if failures:
            raise CommandError(f'{failures}/{len(oibs)} AMS {action} nije uspjelo')

    def _run_list(self, client: MpsClient):
        try:
            data = client.ams_list()
        except MpsClientError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS('AMS list OK'))
        self.stdout.write(f'Publisher: {data.get("publisher_id")}')
        participants = data.get('participants') or []
        if not participants:
            self.stdout.write('  (nema registriranih identifikatora)')
        for item in participants:
            self.stdout.write(f'  - {item.get("full")}')

    def _run_create(self, client: MpsClient, oib: str):
        try:
            data = client.ams_create(oib)
        except MpsClientError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS('AMS create OK'))
        self.stdout.write(f'OIB: {data.get("oib")}')
        self.stdout.write(f'Odgovor: {data.get("response")}')

    def _run_delete(self, client: MpsClient, oib: str):
        try:
            data = client.ams_delete(oib)
        except MpsClientError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS('AMS delete OK'))
        self.stdout.write(f'OIB: {data.get("oib")}')
        self.stdout.write(f'Odgovor: {data.get("response")}')
