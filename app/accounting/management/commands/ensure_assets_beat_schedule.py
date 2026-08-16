from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Registrira Celery beat raspored za mjesečnu amortizaciju osnovnih sredstava.'

    def handle(self, *args, **options):
        from django_celery_beat.models import CrontabSchedule, PeriodicTask

        schedule, _ = CrontabSchedule.objects.get_or_create(
            minute='0',
            hour='6',
            day_of_month='1',
            month_of_year='*',
            day_of_week='*',
            timezone='Europe/Zagreb',
        )
        PeriodicTask.objects.update_or_create(
            name='accounting.run_monthly_depreciation',
            defaults={
                'task': 'accounting.run_monthly_depreciation',
                'crontab': schedule,
                'enabled': True,
                'kwargs': '{}',
            },
        )
        self.stdout.write(self.style.SUCCESS('Assets depreciation beat schedule ensured.'))
