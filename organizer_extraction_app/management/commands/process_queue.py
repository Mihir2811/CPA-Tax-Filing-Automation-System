from django.core.management.base import BaseCommand
from django.db import transaction
from organizer_extraction_app.models import TaxDocument
from organizer_extraction_app.views import process_single_document
import time

class Command(BaseCommand):
    help = 'Process pending documents in queue'

    def add_arguments(self, parser):
        parser.add_argument(
            '--continuous',
            action='store_true',
            help='Run continuously, processing documents as they arrive',
        )
        parser.add_argument(
            '--delay',
            type=int,
            default=5,
            help='Delay between checks in continuous mode (seconds)',
        )

    def handle(self, *args, **options):
        continuous = options['continuous']
        delay = options['delay']
        
        self.stdout.write(
            self.style.SUCCESS('Starting document queue processor...')
        )
        
        if continuous:
            self.stdout.write('Running in continuous mode. Press Ctrl+C to stop.')
            try:
                while True:
                    processed = self.process_next_document()
                    if not processed:
                        time.sleep(delay)
            except KeyboardInterrupt:
                self.stdout.write('\nStopping queue processor...')
        else:
            # Process all pending documents once
            total_processed = 0
            while self.process_next_document():
                total_processed += 1
            
            self.stdout.write(
                self.style.SUCCESS(f'Processed {total_processed} documents.')
            )

    def process_next_document(self):
        """Process the next pending document. Returns True if a document was processed."""
        with transaction.atomic():
            document = TaxDocument.objects.select_for_update().filter(
                status='pending'
            ).order_by('uploaded_at').first()
            
            if not document:
                return False
            
            # Mark as processing
            document.status = 'processing'
            document.save()
            
            self.stdout.write(f'Processing document: {document.file_name}')
        
        # Process the document
        try:
            process_single_document(document)
            self.stdout.write(
                self.style.SUCCESS(f'✓ Completed: {document.file_name}')
            )
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'✗ Failed: {document.file_name} - {str(e)}')
            )
        
        return True