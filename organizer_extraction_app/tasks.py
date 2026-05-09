import logging
import os
from celery import shared_task
from django.conf import settings
from django.db.models import F
from django.utils import timezone
from django.template.loader import render_to_string
from azure.communication.email import EmailClient
from organizer_extraction_app import models as organizer_models
from organizer_extraction_app.utils import get_remaining_unsorted_files


logger = logging.getLogger(__name__)

@shared_task(bind=True, max_retries=3)
def send_document_reminder_email(self, automation_id):
    """Send automated reminder email to client"""
    try:
        automation = organizer_models.EmailAutomation.objects.get(id=automation_id, is_active=True)
        document = automation.document
        
        # Initialize Azure Email Client
        client = EmailClient.from_connection_string(settings.EMAIL_SERVICE)

        # -----------------------------------------
        # 1. Get unsorted documents from folder scan
        # -----------------------------------------
        client_name = document.get_display_name()
        
        # Get unsorted files from folder scan
        remaining_files = get_remaining_unsorted_files(client_name)
        remaining_count = len(remaining_files)
        
        if remaining_files:
            remaining_list_html = "<ul>" + "".join([f"<li>{f}</li>" for f in remaining_files]) + "</ul>"
        else:
            remaining_list_html = "<p><strong>All documents have been uploaded ✔️</strong></p>"

        # -----------------------------------------
        # 2. Create tracking pixel entry
        # -----------------------------------------
        tracking = organizer_models.EmailTracking.objects.create(
            email=automation.client_email,
            document=document
        )

        tracking_url = f"{settings.SITE_URL}/tax/track-email/{tracking.tracking_id}/"
        tracking_pixel = f'<img src="{tracking_url}" width="1" height="1" style="display:none;" />'

        # -----------------------------------------
        # 3. Build dynamic HTML email body using template
        # -----------------------------------------
        # Get taxpayer info
        summary_data = document.extracted_data.summary_data if hasattr(document, 'extracted_data') and document.extracted_data else {}
        taxpayer_name = summary_data.get('taxpayer', 'Client')
        tax_year = summary_data.get('tax_year', 2024)
        
        subject = f"Document Upload Reminder - {client_name} - {tax_year} Tax Year"

        html_body = render_to_string('email/pending_reminder_email.html', {
            'client_name': client_name,
            'taxpayer_name': taxpayer_name,
            'tax_year': tax_year,
            'remaining_count': remaining_count,
            'remaining_list_html': remaining_list_html,
            'tracking_pixel': tracking_pixel
        })

        # -----------------------------------------
        # 4. Build Azure email message
        # -----------------------------------------
        message = {
            "senderAddress": settings.SENDER_ADDRESS,
            "recipients": {"to": [{"address": automation.client_email}]},
            "content": {
                "subject": subject,
                "html": html_body
            }
        }

        # -----------------------------------------
        # 5. Send email
        # -----------------------------------------
        poller = client.begin_send(message)
        poller.result()

        # -----------------------------------------
        # 6. Update last sent timestamp
        # -----------------------------------------
        automation.last_sent_at = timezone.now()
        automation.save()

        logger.info(f"✅ Reminder email sent to {automation.client_email} for document {document.id}")

        # -----------------------------------------
        # 7. Schedule next reminder
        # -----------------------------------------
        if automation.is_active:
            send_document_reminder_email.apply_async(
                args=[automation_id],
                countdown=60  # 1 minute
            )

        return f"Email sent successfully to {automation.client_email}"

    except organizer_models.EmailAutomation.DoesNotExist:
        logger.error(f"❌ EmailAutomation {automation_id} not found or inactive")
        return f"EmailAutomation {automation_id} not found or inactive"
    except Exception as e:
        logger.error(f"❌ Failed to send reminder email for automation {automation_id}: {str(e)}")
        raise self.retry(exc=e, countdown=60)


@shared_task(bind=True, max_retries=3)
def send_pending_documents_email(self, automation_id):
    """Send pending documents email with missing forms from required docs and unsorted files"""
    try:
        automation = organizer_models.EmailAutomation.objects.get(id=automation_id, is_active=True)
        document = automation.document
        
        # Get extracted data and required forms
        if not hasattr(document, 'extracted_data') or not document.extracted_data:
            logger.warning(f"No extracted data for document {document.id}")
            return "No extracted data available"
            
        extracted_data = document.extracted_data
        
        # Initialize Azure Email Client
        client = EmailClient.from_connection_string(settings.EMAIL_SERVICE)
        
        # Get client info
        client_name = document.get_display_name()
        summary_data = extracted_data.summary_data or {}
        taxpayer_name = summary_data.get('taxpayer', 'Client')
        spouse_name = summary_data.get('spouse', '')
        tax_year = summary_data.get('tax_year', 2024)
        
        # Build missing documents list from required forms
        missing_docs_html = ""
        if extracted_data.required_forms_json:
            missing_docs_html = generate_missing_docs_html(extracted_data.required_forms_json, taxpayer_name, spouse_name)
        
        # Get unsorted files from folder scan
        remaining_files = get_remaining_unsorted_files(client_name)
        
        # Build unsorted files HTML
        unsorted_docs_html = ""
        if remaining_files:
            unsorted_items = [f"<li><strong>Unprocessed Document:</strong> {f}</li>" for f in remaining_files]
            unsorted_docs_html = f"<ul style='padding-left: 20px;'>{''.join(unsorted_items)}</ul>"
        
        # Combine missing and unsorted documents
        all_pending_docs = ""
        if missing_docs_html and unsorted_docs_html:
            all_pending_docs = f"<h4>Missing Required Documents:</h4>{missing_docs_html}<h4>Unprocessed Documents:</h4>{unsorted_docs_html}"
        elif missing_docs_html:
            all_pending_docs = missing_docs_html
        elif unsorted_docs_html:
            all_pending_docs = unsorted_docs_html
        else:
            all_pending_docs = "<p>All documents have been processed and sorted ✔️</p>"
        
        # Create tracking pixel
        tracking = organizer_models.EmailTracking.objects.create(
            email=automation.client_email,
            document=document
        )
        tracking_url = f"{settings.SITE_URL}/tax/track-email/{tracking.tracking_id}/"
        tracking_pixel = f'<img src="{tracking_url}" width="1" height="1" style="display:none;" />'
        
        # Build email content using template
        subject = f"Pending Tax Documents - {client_name} - {tax_year} Tax Year"
        
        html_body = render_to_string('email/pending_reminder_email.html', {
            'client_name': client_name,
            'taxpayer_name': taxpayer_name,
            'tax_year': tax_year,
            'pending_docs_html': all_pending_docs,
            'tracking_pixel': tracking_pixel
        })
        
        # Send email
        message = {
            "senderAddress": settings.SENDER_ADDRESS,
            "recipients": {"to": [{"address": automation.client_email}]},
            "content": {
                "subject": subject,
                "html": html_body
            }
        }
        
        poller = client.begin_send(message)
        poller.result()
        
        # Update timestamp
        automation.last_sent_at = timezone.now()
        automation.save()
        
        logger.info(f"✅ Pending documents email sent to {automation.client_email} for document {document.id}")
        
        # Schedule next email if automation is still active
        if automation.is_active:
            send_pending_documents_email.apply_async(
                args=[automation_id],
                countdown=60  # 1 minute
            )
        
        return f"Pending documents email sent successfully to {automation.client_email}"
        
    except organizer_models.EmailAutomation.DoesNotExist:
        logger.error(f"❌ EmailAutomation {automation_id} not found or inactive")
        return f"EmailAutomation {automation_id} not found or inactive"
    except Exception as e:
        logger.error(f"❌ Failed to send pending documents email for automation {automation_id}: {str(e)}")
        raise self.retry(exc=e, countdown=60)

def generate_missing_docs_html(required_forms_json, taxpayer_name, spouse_name):
    """Generate HTML list of missing documents from required forms"""
    if not required_forms_json:
        return "<p>No missing documents identified.</p>"
    
    html_items = []
    
    # Handle both dict and list formats
    if isinstance(required_forms_json, dict):
        forms_data = required_forms_json
    elif isinstance(required_forms_json, list):
        # Convert list to dict format for processing
        forms_data = {}
        for item in required_forms_json:
            if isinstance(item, dict) and 'form_type' in item:
                form_type = item['form_type']
                if form_type not in forms_data:
                    forms_data[form_type] = []
                forms_data[form_type].append(item)
    else:
        return "<p>No missing documents identified.</p>"
    
    for form_type, form_details in forms_data.items():
        if not isinstance(form_details, list):
            continue
            
        for item in form_details:
            if not isinstance(item, dict):
                continue
                
            # Determine owner
            owner = get_owner_display(item.get('tsj', ''), taxpayer_name, spouse_name)
            
            # Skip owner field if it's "-" or "TSJ not specified"
            owner_text = "" if owner in ["-", "TSJ not specified"] else f" – {owner}"
            
            # Format based on form type
            if form_type == 'W-2':
                employer = item.get('employer_name', 'W-2 Wages')
                if employer == 'Unknown Employer':
                    employer = 'W-2 Wages'
                html_items.append(f"<li><strong>W-2 Wages</strong>{owner_text} – {employer}</li>")
                
            elif form_type == '1099-INT':
                payer = item.get('payer_name', 'Interest Income (1099-INT)')
                if payer == 'Unknown Payer':
                    payer = 'Interest Income (1099-INT)'
                html_items.append(f"<li><strong>Interest Income (1099-INT)</strong>{owner_text} – {payer}</li>")
                
            elif form_type == '1099-DIV':
                payer = item.get('payer_name', 'Dividend Income (1099-DIV)')
                if payer == 'Unknown Payer':
                    payer = 'Dividend Income (1099-DIV)'
                html_items.append(f"<li><strong>Dividend Income (1099-DIV)</strong>{owner_text} – {payer}</li>")
                
            elif form_type == '1099-B':
                payer = item.get('payer_name', 'Brokerage Statement')
                if payer == 'Unknown Payer':
                    payer = 'Brokerage Statement'
                account = item.get('account_number', '')
                account_text = f" (Account ending in XXXX{account[-4:]}" if account and len(account) >= 4 else ""
                html_items.append(f"<li><strong>Brokerage Statement</strong>{owner_text} – {payer}{account_text}</li>")
                
            elif form_type == 'Schedule K-1':
                entity = item.get('entity_name', 'Unknown Entity')
                if entity == 'Unknown Entity':
                    if 'partnership' in form_type.lower() or 'llc' in form_type.lower():
                        entity = 'Partnership K-1'
                    else:
                        entity = 'S-Corp K-1'
                if 'partnership' in entity.lower() or 'llc' in entity.lower():
                    html_items.append(f"<li><strong>Partnership K-1</strong>{owner_text} – {entity}</li>")
                else:
                    html_items.append(f"<li><strong>S-Corp K-1</strong>{owner_text} – {entity}</li>")
                    
            elif form_type == 'SSA-1099':
                html_items.append(f"<li><strong>Social Security Benefits (SSA-1099)</strong>{owner_text}</li>")
                
            elif form_type == '1098':
                payer = item.get('paid_to', 'Mortgage Interest (1098)')
                if payer == 'Unknown Lender':
                    payer = 'Mortgage Interest (1098)'
                html_items.append(f"<li><strong>Mortgage Interest (1098)</strong>{owner_text} – {payer}</li>")
                
            elif form_type == 'Schedule E':
                desc = item.get('description', 'Rental/Royalty Item')
                html_items.append(f"<li><strong>Rental And Royalty Expenses</strong> – {owner} – {desc}</li>")
                
            else:
                # Generic handling
                desc = item.get('description') or item.get('nature_source') or form_type
                html_items.append(f"<li><strong>{form_type}</strong>{owner_text} – {desc}</li>")
    
    if not html_items:
        return "<p>No missing documents identified.</p>"
        
    return f"<ul style='padding-left: 20px;'>{''.join(html_items)}</ul>"

def get_owner_display(tsj, taxpayer_name, spouse_name):
    """Convert TSJ code to readable owner name"""
    if not tsj or tsj.strip().upper() == 'TSJ NOT SPECIFIED':
        return "-"
    
    tsj_upper = tsj.upper().strip()
    
    if tsj_upper == 'T':
        return f"{taxpayer_name} (Taxpayer)" if taxpayer_name else "Taxpayer"
    elif tsj_upper == 'S':
        return f"{spouse_name} (Spouse)" if spouse_name else "Spouse"
    elif tsj_upper == 'J':
        return "Joint"
    return "-"

@shared_task
def cleanup_inactive_automations():
    """Clean up inactive email automations"""
    try:
        inactive_count = organizer_models.EmailAutomation.objects.filter(is_active=False).count()
        logger.info(f"Found {inactive_count} inactive email automations")
        return f"Cleanup completed. Found {inactive_count} inactive automations"
    except Exception as e:
        logger.error(f"Error during cleanup: {str(e)}")
        return f"Cleanup failed: {str(e)}"

def process_client_pdfs(client_name, document_id=None):
    """
    Process PDFs for a client and optionally track counts for a TaxDocument.

    Args:
        client_name (str): Friendly client name used for folder discovery.
        document_id (int|None): TaxDocument ID whose extracted data should
            receive sorted/unsorted count updates.
    """
    from .utils import (
        find_unsorted_folder,
        scan_unsorted_folder,
        process_pdf_with_forms_app,
        move_processed_file_to_sorted,
        move_unclassified_file_to_unsorted,
        mark_file_processed
    )

    try:
        unsorted_folder = find_unsorted_folder(client_name)

        # If no folder found → only warn and exit
        if not unsorted_folder:
            logger.warning(f"No upload folder found for {client_name}")
            return 0

        new_files = scan_unsorted_folder(client_name)
        if not new_files:
            return 0

        processed_count = 0
        sorted_delta = 0
        unsorted_delta = 0

        for file_info in new_files:
            try:
                result = process_pdf_with_forms_app(file_info['path'], client_name)

                if result.get('success'):
                    form_data = result.get('extracted_data', {})
                    stored_path = move_processed_file_to_sorted(
                        file_info['path'], client_name, form_data
                    )
                    mark_file_processed(client_name, file_info['hash'], file_info['name'])
                    processed_count += 1
                    sorted_delta += 1

                    if document_id:
                        try:
                            sorted_filename = os.path.basename(stored_path) if stored_path else None
                            folder_name = (
                                os.path.basename(os.path.dirname(stored_path))
                                if stored_path else None
                            )

                        except Exception as save_exc:
                            logger.warning(
                                "Unable to append classified form entry for %s: %s",
                                client_name,
                                save_exc,
                            )
                else:
                    # Unknown form → copy to unsorted_client_name
                    if result.get('form_type') == 'Unknown':
                        move_unclassified_file_to_unsorted(file_info['path'], client_name)
                        mark_file_processed(client_name, file_info['hash'], file_info['name'])
                        unsorted_delta += 1
                        logger.info(
                            f"Unknown form {file_info['name']} copied to unsorted_{client_name}"
                        )

            except Exception as e:
                logger.error(f"Error processing {file_info['name']}: {str(e)}")

        if (sorted_delta or unsorted_delta) and document_id:
            try:
                extracted = organizer_models.ExtractedData.objects.get(document_id=document_id)
                organizer_models.ExtractedData.objects.filter(pk=extracted.pk).update(
                    sorted_forms_count=F('sorted_forms_count') + sorted_delta,
                    unsorted_forms_count=F('unsorted_forms_count') + unsorted_delta,
                )
            except organizer_models.ExtractedData.DoesNotExist:
                logger.warning(
                    f"ExtractedData not found for document {document_id} while updating form counts"
                )

        return processed_count

    except Exception as e:
        logger.error(f"Error processing PDFs for {client_name}: {str(e)}")
        return 0

@shared_task(bind=True, max_retries=3)
def process_client_upload_folder(self, client_name):
    """Process all files in client's upload folder using forms app"""
    try:
        from .utils import (
            find_unsorted_folder,
            scan_unsorted_folder,
            process_pdf_with_forms_app,
            move_processed_file_to_sorted,
            move_unclassified_file_to_unsorted,
            mark_file_processed,
            create_sorted_folder,
            create_unsorted_client_folder
        )
        
        # Ensure folders exist
        create_sorted_folder(client_name)
        create_unsorted_client_folder(client_name)
        
        # Find upload folder
        upload_folder = find_unsorted_folder(client_name)
        if not upload_folder:
            logger.warning(f"No upload folder found for {client_name}")
            return f"No upload folder found for {client_name}"
        
        # Get all PDF files in upload folder
        import glob
        pdf_files = glob.glob(os.path.join(upload_folder, "*.pdf"))
        
        if not pdf_files:
            logger.info(f"No PDF files found in upload folder for {client_name}")
            return f"No PDF files found in upload folder for {client_name}"
        
        processed_count = 0
        sorted_count = 0
        unsorted_count = 0
        
        for pdf_path in pdf_files:
            try:
                # Check if file already processed using hash
                import hashlib
                with open(pdf_path, 'rb') as f:
                    file_hash = hashlib.sha256(f.read()).hexdigest()
                
                if organizer_models.ProcessedFileHash.objects.filter(
                    client_name=client_name, 
                    file_hash=file_hash
                ).exists():
                    logger.info(f"Skipping already processed file: {os.path.basename(pdf_path)}")
                    continue
                
                # Check progress before processing each file
                try:
                    for doc in organizer_models.TaxDocument.objects.filter(extracted_data__isnull=False).select_related('extracted_data'):
                        try:
                            first_page = doc.extracted_data.data[0]
                            taxpayer_info = first_page.get("data", {}).get("personal_information", {}).get("taxpayer")
                            if taxpayer_info:
                                first_name = taxpayer_info.get("first_name", "").strip()
                                last_name = taxpayer_info.get("last_name", "").strip()
                                doc_taxpayer_name = f"{first_name} {last_name}".strip()
                                if doc_taxpayer_name == client_name:
                                    extracted = doc.extracted_data
                                    required_forms_count = len(extracted.required_forms_json) if extracted.required_forms_json else 0
                                    if required_forms_count > 0:
                                        current_progress = (extracted.sorted_forms_count / required_forms_count) * 100
                                        if current_progress >= 85:
                                            logger.info(f"Stopping forms processing for {client_name}: progress reached {current_progress:.1f}%")
                                            return f"Processing stopped for {client_name}: progress reached {current_progress:.1f}% (85% threshold)"
                                    break
                        except Exception:
                            continue
                except Exception:
                    pass  # Continue processing if progress check fails
                
                # Process with forms app
                result = process_pdf_with_forms_app(pdf_path, client_name)
                
                if result.get('success') and result.get('form_type') != 'Unknown':
                    # Move to sorted folder
                    form_data = result.get('extracted_data', {})
                    move_processed_file_to_sorted(pdf_path, client_name, form_data)
                    sorted_count += 1
                    logger.info(f"Sorted: {os.path.basename(pdf_path)} -> {result.get('form_type')}")
                else:
                    # Move to unsorted folder
                    move_unclassified_file_to_unsorted(pdf_path, client_name)
                    unsorted_count += 1
                    logger.info(f"Unsorted: {os.path.basename(pdf_path)} (Unknown form type)")
                    

                
                # Mark file as processed
                organizer_models.ProcessedFileHash.objects.create(
                    client_name=client_name,
                    file_hash=file_hash,
                    file_name=os.path.basename(pdf_path)
                )
                
                processed_count += 1
                
            except Exception as e:
                logger.error(f"Error processing {pdf_path}: {str(e)}")
                continue
        
        logger.info(f"Forms processing completed for {client_name}: {processed_count} files processed, {sorted_count} sorted, {unsorted_count} unsorted")
        return f"Processed {processed_count} files for {client_name}: {sorted_count} sorted, {unsorted_count} unsorted"
        
    except Exception as e:
        logger.error(f"Error in forms processing for {client_name}: {str(e)}")
        raise self.retry(exc=e, countdown=60)

@shared_task(bind=True, max_retries=3)
def monitor_client_folder(self, monitoring_id):
    """Monitor client folder for new files and process them"""
    try:
        monitoring = organizer_models.FolderMonitoring.objects.get(id=monitoring_id, is_active=True)
        client_name = monitoring.client_name
        document_id = monitoring.document.id
        
        # Process any new files found
        processed_count = process_client_pdfs(client_name, document_id)
        
        if processed_count > 0:
            logger.info(f"Processed {processed_count} new files for {client_name}")
        
        # Update last checked timestamp
        monitoring.last_checked_at = timezone.now()
        monitoring.save()
        
        # Schedule next check if still active
        if monitoring.is_active:
            monitor_client_folder.apply_async(
                args=[monitoring_id],
                countdown=30  # Check every 30 seconds
            )
        
        return f"Monitoring check completed for {client_name}. Processed {processed_count} files."
        
    except organizer_models.FolderMonitoring.DoesNotExist:
        logger.error(f"FolderMonitoring {monitoring_id} not found or inactive")
        return f"FolderMonitoring {monitoring_id} not found or inactive"
    except Exception as e:
        logger.error(f"Error in folder monitoring for {monitoring_id}: {str(e)}")
        raise self.retry(exc=e, countdown=60)
