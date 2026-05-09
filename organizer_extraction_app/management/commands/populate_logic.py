from django.core.management.base import BaseCommand
from organizer_extraction_app.models import ExtractedData
from organizer_extraction_app.utils import create_document_logic_from_summary, create_sorted_folder

class Command(BaseCommand):
    help = 'Populate logic field for existing ExtractedData records'

    def handle(self, *args, **options):
        records_updated = 0
        records_skipped = 0
        
        # Get all ExtractedData records without logic
        extracted_records = ExtractedData.objects.filter(logic__isnull=True)
        
        self.stdout.write(f"Found {extracted_records.count()} records without logic data")
        
        for extracted in extracted_records:
            try:
                if not extracted.summary_data:
                    self.stdout.write(f"Skipping record {extracted.id} - no summary_data")
                    records_skipped += 1
                    continue
                    
                # Get taxpayer name from data
                if not extracted.data or not isinstance(extracted.data, list) or len(extracted.data) == 0:
                    self.stdout.write(f"Skipping record {extracted.id} - no valid data")
                    records_skipped += 1
                    continue
                    
                first_page = extracted.data[0]
                taxpayer_info = first_page.get("data", {}).get("personal_information", {}).get("taxpayer")
                
                if not taxpayer_info:
                    self.stdout.write(f"Skipping record {extracted.id} - no taxpayer info")
                    records_skipped += 1
                    continue
                    
                first_name = taxpayer_info.get("first_name", "").strip()
                last_name = taxpayer_info.get("last_name", "").strip()
                
                if not first_name or not last_name:
                    self.stdout.write(f"Skipping record {extracted.id} - incomplete taxpayer name")
                    records_skipped += 1
                    continue
                    
                taxpayer_name = f"{first_name} {last_name}"
                sorted_folder_path = create_sorted_folder(taxpayer_name)
                
                self.stdout.write(f"Processing record {extracted.id} for {taxpayer_name}")
                
                # Generate logic data
                logic_data = create_document_logic_from_summary(
                    {'summary_data': extracted.summary_data}, 
                    taxpayer_name, 
                    sorted_folder_path
                )
                
                # Save logic data
                extracted.logic = logic_data.get('logic')
                extracted.save(update_fields=['logic'])
                
                status = logic_data.get('logic', {}).get('status', 'unknown')
                total_docs = logic_data.get('logic', {}).get('total_documents', 0)
                self.stdout.write(
                    self.style.SUCCESS(f"✓ Updated logic for {taxpayer_name} - Status: {status}, Documents: {total_docs}")
                )
                records_updated += 1
                
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f"✗ Error processing record {extracted.id}: {str(e)}")
                )
                records_skipped += 1
                continue
        
        self.stdout.write(f"\nCompleted!")
        self.stdout.write(self.style.SUCCESS(f"Records updated: {records_updated}"))
        self.stdout.write(f"Records skipped: {records_skipped}")