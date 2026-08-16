from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Registrira Celery beat raspored za OTP banking sync i watchdog taskove.'

    def handle(self, *args, **options):
        from django_celery_beat.models import CrontabSchedule, PeriodicTask

        sync_schedule, _ = CrontabSchedule.objects.get_or_create(
            minute='*/15',
            hour='*',
            day_of_week='*',
            day_of_month='*',
            month_of_year='*',
            timezone='Europe/Zagreb',
        )
        PeriodicTask.objects.update_or_create(
            name='banking.sync_all_active_connections',
            defaults={
                'task': 'banking.sync_all_active_connections',
                'crontab': sync_schedule,
                'enabled': True,
            },
        )

        hourly_schedule, _ = CrontabSchedule.objects.get_or_create(
            minute='0',
            hour='*',
            day_of_week='*',
            day_of_month='*',
            month_of_year='*',
            timezone='Europe/Zagreb',
        )
        PeriodicTask.objects.update_or_create(
            name='banking.check_stale_connections',
            defaults={
                'task': 'banking.check_stale_connections',
                'crontab': hourly_schedule,
                'enabled': True,
            },
        )

        daily_schedule, _ = CrontabSchedule.objects.get_or_create(
            minute='30',
            hour='7',
            day_of_week='*',
            day_of_month='*',
            month_of_year='*',
            timezone='Europe/Zagreb',
        )
        PeriodicTask.objects.update_or_create(
            name='banking.check_consent_expiry',
            defaults={
                'task': 'banking.check_consent_expiry',
                'crontab': daily_schedule,
                'enabled': True,
            },
        )
        self.stdout.write(self.style.SUCCESS('Banking beat schedule ensured.'))
