from django.core.management.base import BaseCommand
import subprocess
import sys
import os

class Command(BaseCommand):
    help = 'Start Celery worker for email automation'

    def add_arguments(self, parser):
        parser.add_argument(
            '--loglevel',
            default='info',
            help='Set the logging level (default: info)'
        )

    def handle(self, *args, **options):
        loglevel = options['loglevel']
        
        self.stdout.write(
            self.style.SUCCESS(f'Starting Celery worker with log level: {loglevel}')
        )
        
        # Change to project directory
        os.chdir('/home/mihir/Desktop/DigitQt/AI-Automation/AI-Automation')
        
        try:
            # Start Celery worker
            subprocess.run([
                sys.executable, '-m', 'celery', 
                '-A', 'organizer_extraction', 
                'worker', 
                '--loglevel=' + loglevel,
                '--concurrency=2'
            ], check=True)
        except subprocess.CalledProcessError as e:
            self.stdout.write(
                self.style.ERROR(f'Failed to start Celery worker: {e}')
            )
        except KeyboardInterrupt:
            self.stdout.write(
                self.style.WARNING('Celery worker stopped by user')
            )