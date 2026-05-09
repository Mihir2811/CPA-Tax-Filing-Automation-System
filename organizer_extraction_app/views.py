import os
import json as json_module
import time
import logging
import json
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse 
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.db.models import Q
from django.contrib.auth.models import User
from django.contrib import messages
from organizer_extraction_app import models as organizer_models
from organizer_extraction_app import services as organizer_services
from organizer_extraction_app import utils as organizer_utils
from organizer_extraction_app import tasks as organizer_tasks
from datetime import datetime
from django.db import transaction
from django.utils import timezone
from .decorators import role_required, get_or_create_profile
from organizer_extraction_app import serializers as organizer_serializers
from django.template.loader import render_to_string


logger = logging.getLogger(__name__)

def process_queue():
    """Process documents in queue one by one"""
    while True:
        # Get the next pending document
        with transaction.atomic():
            document = organizer_models.TaxDocument.objects.select_for_update().filter(
                status='pending'
            ).order_by('uploaded_at').first()
            
            if not document:
                break  # No more pending documents
            
            # Mark as processing
            document.status = 'processing'
            document.save()
        
        # Process the document
        process_single_document(document)
        
        # Small delay between processing
        time.sleep(1)

@login_required
@ensure_csrf_cookie
@require_http_methods(["GET", "POST"])
@csrf_protect
def upload_document(request):
    """Handle multiple tax document uploads to AWS S3"""
    if request.method == 'POST':
        pdf_files = request.FILES.getlist('pdf_files')
        
        if not pdf_files:
            if request.headers.get('Content-Type', '').startswith('application/json'):
                return JsonResponse({'success': False, 'error': 'No PDF files provided.'})
            return render(request, 'tax_extractor/upload.html', {'error': 'No PDF files provided.'})
        
        uploaded_documents = []
        errors = []
        
        for pdf_file in pdf_files:
            # Validate file type
            if not pdf_file.name.endswith('.pdf'):
                errors.append(f'{pdf_file.name}: Only PDF files are allowed.')
                continue
            
            try:
                # Create unique filename
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
                s3_key = f"{timestamp}_{pdf_file.name}"
                
                # Upload file to S3
                organizer_utils.upload_file_to_s3(pdf_file, s3_key)
                
                # Save document info in database
                document = organizer_models.TaxDocument.objects.create(
                    user=request.user,
                    file_name=pdf_file.name,
                    s3_key=s3_key,
                    status='pending'
                )
                
                uploaded_documents.append(document)
                
                organizer_utils.log_activity(
                    user=request.user,
                    action='document_upload',
                    description=f'Uploaded document: {pdf_file.name}',
                    document=document
                )
                
            except Exception as e:
                errors.append(f'{pdf_file.name}: Error uploading - {str(e)}')
        
        # Auto-start processing for uploaded organizer PDFs
        for document in uploaded_documents:
            document.status = 'processing'
            document.save()
            
            # Start processing in background thread
            import threading
            threading.Thread(target=process_single_document, args=(document,), daemon=True).start()
        
        # Check if this is an AJAX request
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in request.headers.get('Accept', '')
        
        if is_ajax:
            if uploaded_documents and not errors:
                return JsonResponse({
                    'success': True, 
                    'message': f'Successfully uploaded {len(uploaded_documents)} document(s). Processing started automatically.',
                    'uploaded_count': len(uploaded_documents)
                })
            elif uploaded_documents and errors:
                return JsonResponse({
                    'success': True,
                    'message': f'Uploaded {len(uploaded_documents)} document(s) with {len(errors)} error(s).',
                    'uploaded_count': len(uploaded_documents),
                    'errors': errors
                })
            else:
                return JsonResponse({'success': False, 'error': 'All uploads failed.', 'errors': errors})
        
        # For non-AJAX requests, redirect to document list
        if uploaded_documents:
            messages.success(request, f'Successfully uploaded {len(uploaded_documents)} document(s). Processing started automatically.')
            return redirect('document_list')
        else:
            return render(request, 'tax_extractor/upload.html', {'error': 'All uploads failed.', 'errors': errors})
    
    return render(request, 'tax_extractor/upload.html')

def process_single_document(document):
    """Process a single document (used by both queue and direct processing)"""
    temp_file_path = None
    try:
        # Handle S3 files by downloading temporarily
        if document.s3_key:
            temp_file_path = organizer_utils.download_file_from_s3(document.s3_key)
            pdf_path = temp_file_path
        else:
            pdf_path = document.file.path

        extracted_data = organizer_services.process_multiple_pages(pdf_path, start_page=9, num_pages=25)

        total_pages = len(extracted_data)
        skipped = sum(1 for p in extracted_data if (data := p.get("data")) and isinstance(data, dict) and data.get("extraction_skipped"))
        errors = sum(1 for p in extracted_data if (data := p.get("data")) and isinstance(data, dict) and data.get("extraction_error"))
        successful = total_pages - skipped - errors
        
        summary_data = organizer_utils.generate_summary_dict(extracted_data)
        
        extracted_data_obj = organizer_models.ExtractedData.objects.create(
            document=document,
            data=extracted_data,
            pages_processed=successful,
            pages_skipped=skipped,
            pages_with_errors=errors,
            summary_data=summary_data 
        )
        
        # Generate required forms JSON
        organizer_services.generate_required_forms_json(extracted_data_obj)
        
        # Generate logic data from summary_data
        try:
            first_page = extracted_data[0]
            taxpayer_info = first_page.get("data", {}).get("personal_information", {}).get("taxpayer")
            if taxpayer_info:
                first_name = taxpayer_info.get("first_name", "").strip()
                last_name = taxpayer_info.get("last_name", "").strip()
                if first_name and last_name:
                    taxpayer_name = f"{first_name} {last_name}"
                    sorted_folder_path = organizer_utils.create_sorted_folder(taxpayer_name)
                    
                    logger.info(f"Generating logic data for {taxpayer_name}")
                    
                    # Create logic data
                    logic_data = organizer_utils.create_document_logic_from_summary(
                        {'summary_data': summary_data}, 
                        taxpayer_name, 
                        sorted_folder_path
                    )
                    
                    logger.info(f"Logic data generated: {logic_data.get('logic', {}).get('status', 'unknown')}")
                    
                    # Save logic data to extracted_data_obj
                    extracted_data_obj.logic = logic_data.get('logic')
                    extracted_data_obj.save(update_fields=['logic'])
                    
                    logger.info(f"Logic data saved to database for document {document.id}")
                else:
                    logger.warning(f"No taxpayer name found for document {document.id}")
            else:
                logger.warning(f"No taxpayer info found for document {document.id}")
        except Exception as e:
            # If logic generation fails, continue without error
            logger.error(f"Failed to generate logic data for document {document.id}: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            pass
        
        # Update filename with taxpayer name if available and create organizer folder
        try:
            first_page = extracted_data[0]
            taxpayer_info = first_page.get("data", {}).get("personal_information", {}).get("taxpayer")
            if taxpayer_info:
                first_name = taxpayer_info.get("first_name", "").strip()
                last_name = taxpayer_info.get("last_name", "").strip()
                if first_name and last_name:
                    organizer_name = f"{first_name} {last_name}"
                    new_filename = f"{first_name}_{last_name}.pdf"
                    
                    # Move non-PDF files to unsorted folder
                    organizer_utils.move_non_pdf_files_to_unsorted(organizer_name)
                    
                    if document.s3_key:
                        # For S3 files, just update the display name
                        document.file_name = new_filename
                    else:
                        # For local files, rename the physical file
                        old_path = document.file.path
                        file_ext = os.path.splitext(old_path)[1]
                        base_filename = f"{first_name}_{last_name}"
                        new_filename = f"{base_filename}{file_ext}"
                        new_path = os.path.join(os.path.dirname(old_path), new_filename)
                        
                        # Handle duplicates by adding counter
                        counter = 1
                        while os.path.exists(new_path):
                            new_filename = f"{base_filename}_{counter}{file_ext}"
                            new_path = os.path.join(os.path.dirname(old_path), new_filename)
                            counter += 1
                        
                        # Rename the physical file
                        if os.path.exists(old_path):
                            os.rename(old_path, new_path)
                            document.file.name = f"tax_documents/{new_filename}"
        except Exception:
            # If renaming fails, continue without error
            pass
        
        document.status = 'completed'
        document.save()
        
    except Exception as e:
        document.status = 'failed'
        document.save()
    finally:
        # Clean up temporary file if it was created
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.unlink(temp_file_path)
            except Exception:
                pass  # Ignore cleanup errors

@login_required
@csrf_protect
def process_document(request, document_id):
    """Process the uploaded tax document"""
    document = get_object_or_404(organizer_models.TaxDocument, id=document_id, user=request.user)
    
    # Handle AJAX requests (from dedicated form processing button)
    if request.method == 'POST' and (request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in request.headers.get('Accept', '')):
        if document.status == 'completed':
            return JsonResponse({'success': False, 'error': 'Document already processed'})
        
        if document.status == 'processing':
            return JsonResponse({'success': False, 'error': 'Document is already being processed'})
        
        document.status = 'processing'
        document.save()
        
        # Start processing in background thread
        import threading
        threading.Thread(target=process_single_document, args=(document,), daemon=True).start()
        
        return JsonResponse({'success': True, 'message': 'Form processing started'})
    
    # Handle regular requests (legacy)
    if document.status == 'completed':
        return redirect('view_results', document_id=document.id)
    
    document.status = 'processing'
    document.save()
    
    process_single_document(document)
    
    if document.status == 'completed':
        return redirect('view_results', document_id=document.id)
    else:
        return render(request, 'tax_extractor/error.html', {'error': 'Processing failed'})

# ================ View Extraction Results ==================

@login_required
@ensure_csrf_cookie
def view_results(request, document_id):
    """View extraction results"""
    document = get_object_or_404(organizer_models.TaxDocument, id=document_id, user=request.user)

    if document.status != 'completed':
        return redirect('process_document', document_id=document.id)

    extracted_data = document.extracted_data

    # Extract detected tax year from the data
    detected_tax_year = None
    for page in extracted_data.data:
        if page.get('data') and page['data'].get('detected_tax_year'):
            detected_tax_year = page['data']['detected_tax_year']
            break

    # Determine current and previous years
    if detected_tax_year:
        current_year = detected_tax_year
        previous_year = detected_tax_year - 1
    else:
        current_year = datetime.now().year - 1
        previous_year = current_year - 1

    # ✅ Extract taxpayer name from the first page (if available)
    taxpayer_name = None
    try:
        first_page = extracted_data.data[0]
        taxpayer_info = first_page.get("data", {}).get("personal_information", {}).get("taxpayer")
        if taxpayer_info:
            first_name = taxpayer_info.get("first_name", "").strip()
            last_name = taxpayer_info.get("last_name", "").strip()
            taxpayer_name = f"{first_name} {last_name}".strip() or None
    except Exception:
        taxpayer_name = None

    # Reshape data into uniform sections
    reshaped_data = organizer_utils.reshape_to_uniform_sections(extracted_data.data)
    
    activity_logs = document.activity_logs.select_related('user').all()[:20]

    context = {
        'document': document,
        'extracted_data': extracted_data,
        'data': extracted_data.data,
        'reshaped_data': reshaped_data,
        'current_year': current_year,
        'previous_year': previous_year,
        'taxpayer_name': taxpayer_name,  # ✅ Add this line
        'activity_logs': activity_logs
    }

    #return render(request, 'tax_extractor/results.html', context)
    return render(request, 'tax_extractor/results_uniform.html', context)


@login_required
def download_json(request, document_id):
    """Download extracted data as JSON"""
    document = get_object_or_404(organizer_models.TaxDocument, id=document_id, user=request.user)
    
    if not hasattr(document, 'extracted_data'):
        return JsonResponse({'error': 'No extracted data available'}, status=404)
    
    # Get taxpayer name for filename
    filename = f"tax_data_{document.id}.json"
    try:
        first_page = document.extracted_data.data[0]
        taxpayer_info = first_page.get("data", {}).get("personal_information", {}).get("taxpayer")
        if taxpayer_info:
            first_name = taxpayer_info.get("first_name", "").strip()
            last_name = taxpayer_info.get("last_name", "").strip()
            if first_name and last_name:
                filename = f"{first_name}_{last_name}.json"
    except Exception:
        pass
    
    response = JsonResponse(document.extracted_data.data, safe=False)
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@login_required
def download_summary(request, document_id):
    """Download extracted data as text summary"""
    document = get_object_or_404(organizer_models.TaxDocument, id=document_id, user=request.user)

    if not hasattr(document, 'extracted_data'):
        return HttpResponse('No extracted data available', status=404)

    extracted_data = document.extracted_data
    if extracted_data.summary_data:
        summary_text = organizer_utils.generate_summary_text_from_dict(extracted_data.summary_data)
    else:
        summary_text = "No summary available"

    # Get taxpayer name for filename
    filename = f"tax_summary_{document.id}.txt"
    try:
        first_page = document.extracted_data.data[0]
        taxpayer_info = first_page.get("data", {}).get("personal_information", {}).get("taxpayer")
        if taxpayer_info:
            first_name = taxpayer_info.get("first_name", "").strip()
            last_name = taxpayer_info.get("last_name", "").strip()
            if first_name and last_name:
                filename = f"{first_name}_{last_name}_summary.txt"
    except Exception:
        pass

    response = HttpResponse(summary_text, content_type='text/plain; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response

# ================ Document Listing ==================

@login_required
@ensure_csrf_cookie
def document_list(request):
    """List all user's documents with pagination and search"""
    search_query = request.GET.get('search', '').strip()
    
    # Get or create user profile
    user_profile = get_or_create_profile(request.user)

    # Base queryset
    documents = organizer_models.TaxDocument.objects.filter(
        user=request.user
    ).prefetch_related('email_tracking', 'email_automation')
    
    # Admins and preparers can see all documents
    if user_profile.role in ['admin', 'tax_preparer']:
        documents = organizer_models.TaxDocument.objects.all()

    documents = documents.select_related('user').prefetch_related('email_tracking')

    # Apply search filter
    if search_query:
        matching_documents = []
        
        for doc in documents:
            if hasattr(doc, 'extracted_data') and doc.extracted_data:
                try:
                    first_page = doc.extracted_data.data[0]
                    taxpayer_info = first_page.get("data", {}).get("personal_information", {}).get("taxpayer")
                    if taxpayer_info:
                        first_name = taxpayer_info.get("first_name", "").strip().lower()
                        last_name = taxpayer_info.get("last_name", "").strip().lower()
                        full_name = f"{first_name} {last_name}".strip()
                        
                        if (search_query.lower() in first_name or 
                            search_query.lower() in last_name or 
                            search_query.lower() in full_name):
                            matching_documents.append(doc.id)
                except Exception:
                    pass
        
        if matching_documents:
            documents = documents.filter(id__in=matching_documents)
        else:
            documents = documents.none()

    # Attach stored counts and calculate progress for each document
    for doc in documents:
        extracted = getattr(doc, "extracted_data", None)

        if extracted:
            doc.sorted_count = extracted.sorted_forms_count
            doc.unsorted_count = extracted.unsorted_forms_count
            
            # Calculate progress based on sorted forms vs required forms
            required_forms_count = len(extracted.required_forms_json) if extracted.required_forms_json else 0
            if required_forms_count > 0:
                doc.progress_percent = min(int((doc.sorted_count / required_forms_count) * 100), 100)
                # Update the model field
                if extracted.completion_percentage != doc.progress_percent:
                    extracted.completion_percentage = doc.progress_percent
                    extracted.save(update_fields=['completion_percentage'])
            else:
                doc.progress_percent = 0
        else:
            doc.unsorted_count = 0
            doc.sorted_count = 0
            doc.progress_percent = 0

    # Pagination
    paginator = Paginator(documents, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'page_obj': page_obj,
        'search_query': search_query,
        'total_documents': documents.count(),
        'user_role': user_profile.get_role_display(),
        'can_manage_users': user_profile.role == 'admin',
    }
    
    return render(request, 'tax_extractor/document_list.html', context)

# ============== Summary Viewing ==================

@login_required
@ensure_csrf_cookie
def view_summary(request, document_id):
    """View the text summary on a dedicated page"""
    document = get_object_or_404(organizer_models.TaxDocument, id=document_id, user=request.user)

    if not hasattr(document, 'extracted_data'):
        return render(request, 'tax_extractor/error.html', {'error': 'No extracted data available'})

    extracted_data = document.extracted_data

    if not extracted_data.summary_data:
        try:
            summary_data = organizer_utils.generate_summary_dict(extracted_data.data)
            extracted_data.summary_data = summary_data
            extracted_data.save()
            
            # Generate required forms JSON
            organizer_services.generate_required_forms_json(extracted_data)
        except Exception as e:
            summary_data = {}
    else:
        summary_data = extracted_data.summary_data

    # Generate text from dict for display
    summary_text = organizer_utils.generate_summary_text_from_dict(summary_data) if summary_data else "No summary available"
    
    # Generate HTML summary using the same logic as email
    summary_html = None
    if summary_data:
        try:
            summary_html_data = organizer_utils.generate_summary_html_from_dict(summary_data)
            summary_html = render_to_string('email/send_email.html', {
                'summary_data': summary_html_data,
                'recipient_name': 'Client',
                'sender_name': 'Tax Document Extraction System'
            })
        except Exception as e:
            summary_html = None

    # Extract tax year info for display
    tax_year = summary_data.get('tax_year') if summary_data else None
    previous_year = summary_data.get('previous_year') if summary_data else None
    
    context = {
        'document': document,
        'extracted_data': extracted_data,
        'summary_text': summary_text,
        'summary_html': summary_html,
        'summary_data': summary_data,
        'tax_year': tax_year,
        'previous_year': previous_year
    }

    # Add this after line where context is defined in view_summary function
    try:
        folder_monitoring = document.folder_monitoring
    except organizer_models.FolderMonitoring.DoesNotExist:
        folder_monitoring = None

    context.update({
        'folder_monitoring': folder_monitoring
    })

    return render(request, 'tax_extractor/summary.html', context)

# =============== Email Sending ==================

@login_required
@require_http_methods(["POST"])
def send_summary_email(request, document_id):
    """Send the summary via email to the user"""
    document = get_object_or_404(organizer_models.TaxDocument, id=document_id, user=request.user)

    if not hasattr(document, 'extracted_data') or not document.extracted_data.summary_data:
        return JsonResponse({'error': 'No summary data available'}, status=400)

    summary_data = document.extracted_data.summary_data
    summary_text = organizer_utils.generate_summary_text_from_dict(summary_data)

    subject = f"Tax Document Summary for {document.file.name}"
    message = summary_text
    from_email = 'noreply@example.com'  # Configure in settings
    recipient_list = [request.user.email]

    try:
        send_mail(subject, message, from_email, recipient_list)
        return JsonResponse({'message': 'Email sent successfully'})
    except Exception as e:
        return JsonResponse({'error': f'Failed to send email: {str(e)}'}, status=500)
    
@login_required
@csrf_protect
def send_email(request, document_id):
    """Send extracted data via email"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)
    
    document = get_object_or_404(organizer_models.TaxDocument, id=document_id, user=request.user)
    
    if not hasattr(document, 'extracted_data'):
        return JsonResponse({'success': False, 'error': 'No extracted data available'}, status=404)
    
    try:
        # Get recipient email from request
        data = json_module.loads(request.body)
        recipient_email = data.get('email', request.user.email)
        
        # Send email
        email_service = organizer_services.TaxEmailService()
        result = email_service.send_tax_data_email(
            recipient_email=recipient_email,
            extracted_data=document.extracted_data,
            document=document
        )
        
        if result['success']:
            organizer_utils.log_activity(
                user=request.user,
                action='email_sent',
                description=f'Sent email to {recipient_email}',
                document=document
            )
            return JsonResponse({
                'success': True,
                'message': f'Email sent successfully to {recipient_email}'
            })
        else:
            return JsonResponse({
                'success': False,
                'error': result.get('error', 'Failed to send email')
            }, status=500)
            
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)

@login_required
def download_pdf(request, document_id):
    """Download original PDF from S3 or local storage"""
    document = get_object_or_404(organizer_models.TaxDocument, id=document_id, user=request.user)
    
    if document.s3_key:
        # For S3 files, redirect to presigned URL
        try:
            download_url = organizer_utils.get_s3_file_url(document.s3_key, expiration=300)  # 5 minutes
            return redirect(download_url)
        except Exception as e:
            return HttpResponse(f'Error accessing file: {str(e)}', status=500)
    elif document.file:
        # For local files, serve directly
        response = HttpResponse(document.file.read(), content_type='application/pdf')
        filename = document.file_name or document.file.name.split('/')[-1]
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
    else:
        return HttpResponse('File not found', status=404)

@login_required
def check_status(request):
    """Check processing status of user's documents"""
    documents = organizer_models.TaxDocument.objects.filter(user=request.user).values(
        'id', 'status', 'file_name', 'uploaded_at'
    ).order_by('-uploaded_at')[:20]  # Last 20 documents
    
    return JsonResponse({
        'documents': list(documents),
        'queue_count': organizer_models.TaxDocument.objects.filter(status='pending').count(),
        'processing_count': organizer_models.TaxDocument.objects.filter(status='processing').count()
    })

def track_email(request, tracking_id):
    """Track email opens"""
    
    # The 1x1 transparent pixel
    pixel = b'\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00\x21\xf9\x04\x01\x00\x00\x00\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b'
    
    try:
        tracking = organizer_models.EmailTracking.objects.get(tracking_id=tracking_id)
        if not tracking.opened_at:
            tracking.is_opened = True
            tracking.opened_at = timezone.now()
            tracking.save()
    except:
        pass  # If error, still return pixel
    
    return HttpResponse(pixel, content_type='image/gif')

# ================ User Management ==================

@login_required
@role_required(['admin'])
def user_list(request):
    """List all users (Admin only)"""
    search_query = request.GET.get('search', '').strip()
    role_filter = request.GET.get('role', '')
    
    # Get all users
    users = User.objects.all()
    
    # Create profiles for users without one (for display purposes)
    for user in users:
        get_or_create_profile(user)
    
    users = users.select_related('profile')
    
    # Apply filters
    if search_query:
        users = users.filter(
            Q(username__icontains=search_query) |
            Q(email__icontains=search_query) |
            Q(first_name__icontains=search_query) |
            Q(last_name__icontains=search_query)
        )
    
    if role_filter:
        users = users.filter(profile__role=role_filter)
    
    # Pagination
    paginator = Paginator(users, 15)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'page_obj': page_obj,
        'search_query': search_query,
        'role_filter': role_filter,
        'role_choices': organizer_models.UserProfile.ROLE_CHOICES,
        'total_users': users.count(),
    }
    
    return render(request, 'tax_extractor/user_list.html', context)


@login_required
@role_required(['admin'])
def user_create(request):
    """Create new user using Serializer (Admin only)"""
    if request.method == 'POST':
        # Prepare data from POST
        data = {
            'username': request.POST.get('username', '').strip(),
            'email': request.POST.get('email', '').strip(),
            'first_name': request.POST.get('first_name', '').strip(),
            'last_name': request.POST.get('last_name', '').strip(),
            'password': request.POST.get('password', ''),
            'confirm_password': request.POST.get('confirm_password', ''),
            'role': request.POST.get('role', 'admin'),
            'is_active': request.POST.get('is_active') == 'on',
        }
        
        # Use serializer for validation
        serializer = organizer_serializers.UserSerializer(data=data)
        
        if serializer.is_valid():
            try:
                # Create user with profile
                role = data['role']
                is_active = data['is_active']
                
                # Create user
                user = User.objects.create_user(
                    username=data['username'],
                    email=data['email'],
                    first_name=data['first_name'],
                    last_name=data['last_name'],
                    password=data['password'],
                    is_active=is_active
                )
                
                # Create profile manually (CRUD approach)
                organizer_models.UserProfile.objects.create(
                    user=user,
                    role=role,
                    is_active=is_active
                )
                
                messages.success(request, f'User "{user.username}" created successfully!')

                return redirect('user_list')
            except Exception as e:
                messages.error(request, f'Error creating user: {str(e)}')
                # Show errors in form
                context = {
                    'errors': {'general': str(e)},
                    'data': request.POST,
                    'role_choices': organizer_models.UserProfile.ROLE_CHOICES,
                    'title': 'Create New User',
                    'button_text': 'Create User',
                }
                return render(request, 'tax_extractor/user_form.html', context)
        else:
            # Serializer validation failed
            errors = {}
            for field, error_list in serializer.errors.items():
                errors[field] = error_list[0] if isinstance(error_list, list) else error_list
            
            context = {
                'errors': errors,
                'data': request.POST,
                'role_choices': organizer_models.UserProfile.ROLE_CHOICES,
                'title': 'Create New User',
                'button_text': 'Create User',
            }
            return render(request, 'tax_extractor/user_form.html', context)
    
    # GET request
    context = {
        'role_choices': organizer_models.UserProfile.ROLE_CHOICES,
        'title': 'Create New User',
        'button_text': 'Create User',
        'data': {'is_active': True}  # Default active
    }
    
    return render(request, 'tax_extractor/user_form.html', context)


@login_required
@role_required(['admin'])
def user_edit(request, user_id):
    """Edit existing user using Serializer (Admin only)"""
    user = get_object_or_404(User, id=user_id)
    
    # Get or create profile
    profile = get_or_create_profile(user)
    
    if request.method == 'POST':
        # Prepare data from POST
        data = {
            'username': request.POST.get('username', '').strip(),
            'email': request.POST.get('email', '').strip(),
            'first_name': request.POST.get('first_name', '').strip(),
            'last_name': request.POST.get('last_name', '').strip(),
            'role': request.POST.get('role', 'admin'),
            'is_active': request.POST.get('is_active') == 'on',
        }
        
        # Use serializer for validation (partial update, no password required)
        serializer = organizer_serializers.UserSerializer(user, data=data, partial=True)
        
        if serializer.is_valid():
            try:
                # Update user
                user.username = data['username']
                user.email = data['email']
                user.first_name = data['first_name']
                user.last_name = data['last_name']
                user.is_active = data['is_active']
                user.save()
                
                # Update profile
                profile.role = data['role']
                profile.is_active = data['is_active']
                profile.save()
                
                messages.success(request, f'User "{user.username}" updated successfully!')
                
                return redirect('user_list')
            except Exception as e:
                messages.error(request, f'Error updating user: {str(e)}')
                context = {
                    'user_obj': user,
                    'errors': {'general': str(e)},
                    'data': request.POST,
                    'role_choices': organizer_models.UserProfile.ROLE_CHOICES,
                    'title': f'Edit User: {user.username}',
                    'button_text': 'Update User',
                    'is_edit': True,
                }
                return render(request, 'tax_extractor/user_form.html', context)
        else:
            # Serializer validation failed
            errors = {}
            for field, error_list in serializer.errors.items():
                errors[field] = error_list[0] if isinstance(error_list, list) else error_list
            
            context = {
                'user_obj': user,
                'errors': errors,
                'data': request.POST,
                'role_choices': organizer_models.UserProfile.ROLE_CHOICES,
                'title': f'Edit User: {user.username}',
                'button_text': 'Update User',
                'is_edit': True,
            }
            return render(request, 'tax_extractor/user_form.html', context)
    
    # GET request - Prepare initial data
    initial_data = {
        'username': user.username,
        'email': user.email,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'role': profile.role,
        'is_active': profile.is_active,
    }
    
    context = {
        'user_obj': user,
        'data': initial_data,
        'role_choices': organizer_models.UserProfile.ROLE_CHOICES,
        'title': f'Edit User: {user.username}',
        'button_text': 'Update User',
        'is_edit': True,
    }
    
    return render(request, 'tax_extractor/user_form.html', context)


@login_required
@role_required(['admin'])
def user_detail(request, user_id):
    """View user details (Admin only)"""
    user = get_object_or_404(User, id=user_id)
    
    # Get or create profile
    profile = get_or_create_profile(user)
    
    # Get user's documents
    documents = organizer_models.TaxDocument.objects.filter(user=user).select_related('extracted_data')[:10]
    
    context = {
        'user_obj': user,
        'profile': profile,
        'documents': documents,
        'total_documents': organizer_models.TaxDocument.objects.filter(user=user).count(),
    }
    
    return render(request, 'tax_extractor/user_detail.html', context)


@login_required
@role_required(['admin'])
def user_delete(request, user_id):
    """Delete user (Admin only)"""
    user = get_object_or_404(User, id=user_id)
    
    # Prevent deleting yourself
    if user == request.user:
        messages.error(request, "You cannot delete your own account!")
        return redirect('user_list')
    
    if request.method == 'POST':
        username = user.username
        
        # Delete profile first (if exists)
        try:
            if hasattr(user, 'profile'):
                user.profile.delete()
        except:
            pass
        
        # Delete user
        user.delete()
        
        messages.success(request, f'User "{username}" deleted successfully!')
        return redirect('user_list')
    
    context = {
        'user_obj': user,
    }
    
    return render(request, 'tax_extractor/user_confirm_delete.html', context)


# ================ Email Automation Management ==================

@login_required
@csrf_protect
@require_http_methods(["POST"])
def send_pending_docs_email(request, document_id):
    """Send pending documents email with missing forms"""
    document = get_object_or_404(organizer_models.TaxDocument, id=document_id, user=request.user)
    
    try:
        data = json_module.loads(request.body)
        client_email = data.get('client_email', '')
        
        if not client_email:
            return JsonResponse({
                'success': False, 
                'error': 'Client email is required'
            }, status=400)
        
        # Create or get automation record
        automation, created = organizer_models.EmailAutomation.objects.get_or_create(
            document=document,
            defaults={'client_email': client_email, 'is_active': False}
        )
        automation.client_email = client_email
        automation.save()
        
        # Send pending documents email immediately
        task = organizer_tasks.send_pending_documents_email.apply_async(
            args=[automation.id],
            countdown=5
        )
        
        logger.info(f"Pending documents email queued for document {document_id}, client: {client_email}")
        
        return JsonResponse({
            'success': True,
            'message': f'Pending documents email sent to {client_email}'
        })
        
    except json_module.JSONDecodeError:
        return JsonResponse({
            'success': False, 
            'error': 'Invalid JSON data'
        }, status=400)
    except Exception as e:
        logger.error(f"Error sending pending docs email for document {document_id}: {str(e)}")
        return JsonResponse({
            'success': False, 
            'error': str(e)
        }, status=500)

@login_required
@csrf_protect
@require_http_methods(["POST"])
def toggle_email_automation(request, document_id):
    """Toggle email automation for a document"""
    document = get_object_or_404(organizer_models.TaxDocument, id=document_id, user=request.user)
    
    try:
        data = json_module.loads(request.body)
        is_active = data.get('is_active', False)
        
        automation, created = organizer_models.EmailAutomation.objects.get_or_create(
            document=document,
            defaults={'client_email': 'mihir@digiqt.com', 'is_active': False}
        )
        
        if is_active:
            automation.client_email = 'mihir@digiqt.com'
            automation.is_active = True
            automation.save()
            
            # Start the pending documents email automation task (every 1 minute)
            task = organizer_tasks.send_pending_documents_email.apply_async(
                args=[automation.id],
                countdown=60  # Start after 1 minute
            )
            automation.celery_task_id = task.id
            automation.save()
            
            logger.info(f"Email automation activated for document {document_id}")
            
            return JsonResponse({
                'success': True,
                'message': 'Email automation activated',
                'is_active': True
            })
        else:
            automation.is_active = False
            automation.save()
            
            logger.info(f"Email automation deactivated for document {document_id}")
            
            return JsonResponse({
                'success': True,
                'message': 'Email automation deactivated',
                'is_active': False
            })
            
    except json_module.JSONDecodeError:
        return JsonResponse({
            'success': False, 
            'error': 'Invalid JSON data'
        }, status=400)
    except Exception as e:
        logger.error(f"Error toggling email automation for document {document_id}: {str(e)}")
        return JsonResponse({
            'success': False, 
            'error': str(e)
        }, status=500)

# ================ Custom Field Management ==================

@login_required
@require_http_methods(["POST"])
def add_custom_field(request, document_id):
    document = get_object_or_404(organizer_models.TaxDocument, id=document_id, user=request.user)
    
    if not hasattr(document, 'extracted_data') or not document.extracted_data:
        return JsonResponse({"success": False, "error": "No extracted data available"}, status=400)
    
    extracted = document.extracted_data

    try:
        body = json.loads(request.body)
        field_name = body.get("field_name", "").strip()
        field_value = body.get("field_value", "").strip()

        if not field_name or not field_value:
            return JsonResponse({"success": False, "error": "Missing field name or value"}, status=400)

        # Initialize custom_fields if not exists or not a list
        if not isinstance(extracted.custom_fields, list):
            extracted.custom_fields = []

        # Initialize summary_data if not exists
        if not extracted.summary_data:
            extracted.summary_data = {}

        # Append to custom_fields JSON
        custom_entry = {"name": field_name, "value": field_value}
        extracted.custom_fields.append(custom_entry)

        # ALSO inject into summary_data
        if "custom_fields" not in extracted.summary_data:
            extracted.summary_data["custom_fields"] = []

        extracted.summary_data["custom_fields"].append(custom_entry)
        extracted.save()

        return JsonResponse({"success": True, "message": "Custom field added"})
    
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "error": "Invalid JSON data"}, status=400)
    except Exception as e:
        return JsonResponse({"success": False, "error": f"Error adding custom field: {str(e)}"}, status=500)

@login_required
@require_http_methods(["POST"])
def delete_custom_field(request, document_id):
    document = get_object_or_404(organizer_models.TaxDocument, id=document_id, user=request.user)
    
    if not hasattr(document, 'extracted_data') or not document.extracted_data:
        return JsonResponse({"success": False, "error": "No extracted data available"}, status=400)
    
    extracted = document.extracted_data

    try:
        body = json.loads(request.body)
        field_index = body.get("field_index")

        if field_index is None or not isinstance(field_index, int):
            return JsonResponse({"success": False, "error": "Invalid field index"}, status=400)

        if not isinstance(extracted.custom_fields, list) or field_index >= len(extracted.custom_fields):
            return JsonResponse({"success": False, "error": "Field not found"}, status=400)

        # Remove from custom_fields
        extracted.custom_fields.pop(field_index)

        # Remove from summary_data
        if extracted.summary_data and "custom_fields" in extracted.summary_data:
            if field_index < len(extracted.summary_data["custom_fields"]):
                extracted.summary_data["custom_fields"].pop(field_index)

        extracted.save()
        return JsonResponse({"success": True, "message": "Custom field deleted"})
    
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "error": "Invalid JSON data"}, status=400)
    except Exception as e:
        return JsonResponse({"success": False, "error": f"Error deleting custom field: {str(e)}"}, status=500)



# ================ Organizer Folder Management ==================

@login_required
def create_organizer_folder_view(request):
    """Create organizer folder when organizer is uploaded"""
    if request.method == 'POST':
        try:
            data = json_module.loads(request.body)
            organizer_name = data.get('organizer_name', '').strip()
            
            if not organizer_name:
                return JsonResponse({
                    'success': False,
                    'error': 'Organizer name is required'
                }, status=400)
            
            folder, created = organizer_utils.create_organizer_folder(organizer_name)
            
            return JsonResponse({
                'success': True,
                'message': f"Organizer folder {'created' if created else 'already exists'} for {organizer_name}",
                'folder_path': folder.folder_path,
                'created': created
            })
            
        except Exception as e:
            return JsonResponse({
                'success': False,
                'error': str(e)
            }, status=500)
    
    return JsonResponse({'error': 'Invalid request method'}, status=405)


# =============== PDF Sort Unsort Swap Management ==================

@login_required
def get_required_forms_json(request, document_id):
    """Get required forms JSON from summary data"""
    document = get_object_or_404(organizer_models.TaxDocument, id=document_id, user=request.user)
    
    if not hasattr(document, 'extracted_data'):
        return JsonResponse({'error': 'No extracted data available'}, status=404)
    
    extracted_data = document.extracted_data
    
    # Generate required forms if not exists
    if not extracted_data.required_forms_json:
        organizer_services.generate_required_forms_json(extracted_data)
        extracted_data.refresh_from_db()
    
    return JsonResponse({
        'required_forms': extracted_data.required_forms_json,

    })

@login_required
@csrf_protect
def process_forms_app(request, document_id):
    """Process upload folder using forms app"""
    document = get_object_or_404(organizer_models.TaxDocument, id=document_id, user=request.user)
    
    if request.method == 'POST':
        try:
            # Get client name
            client_name = document.get_display_name()
            if not client_name or client_name == "Unknown Document":
                return JsonResponse({'success': False, 'error': 'Cannot determine client name'})
            
            # Create folders
            organizer_utils.create_sorted_folder(client_name)
            organizer_utils.create_unsorted_client_folder(client_name)
            
            # Start forms app processing task
            organizer_tasks.process_client_upload_folder.apply_async(
                args=[client_name],
                countdown=5
            )
            
            return JsonResponse({
                'success': True,
                'message': f'Forms processing started for {client_name}'
            })
            
        except Exception as e:
            return JsonResponse({
                'success': False,
                'error': str(e)
            }, status=500)
    
    return JsonResponse({'success': False, 'error': 'Invalid request method'}, status=405)

@login_required
def get_client_required_forms(request, taxpayer_name):
    """Get required forms for a specific client"""
    try:
        # Find document for this taxpayer
        documents = organizer_models.TaxDocument.objects.filter(user=request.user, extracted_data__isnull=False).select_related('extracted_data')
        
        for doc in documents:
            try:
                first_page = doc.extracted_data.data[0]
                taxpayer_info = first_page.get("data", {}).get("personal_information", {}).get("taxpayer")
                if taxpayer_info:
                    first_name = taxpayer_info.get("first_name", "").strip()
                    last_name = taxpayer_info.get("last_name", "").strip()
                    doc_taxpayer_name = f"{first_name} {last_name}".strip()
                    if doc_taxpayer_name == taxpayer_name:
                        extracted_data = doc.extracted_data
                        
                        # Generate required forms if not exists
                        if not extracted_data.required_forms_json:
                            organizer_services.generate_required_forms_json(extracted_data)
                            extracted_data.refresh_from_db()
                        
                        # Extract form types with unique keys from required forms
                        form_types = []
                        if extracted_data.required_forms_json:
                            for form in extracted_data.required_forms_json:
                                if isinstance(form, dict) and 'form_type' in form:
                                    form_type = form['form_type']
                                    unique_key = form.get('unique_key', '')
                                    if unique_key:
                                        display_name = f"{form_type}-{unique_key}"
                                    else:
                                        display_name = form_type
                                    form_types.append({
                                        'value': form_type,
                                        'display': display_name
                                    })
                        
                        return JsonResponse({
                            'success': True,
                            'form_types': form_types
                        })
            except Exception:
                continue
        
        return JsonResponse({
            'success': False,
            'error': 'No required forms data found for this client'
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)

@login_required
def get_pdf_lists(request, taxpayer_name):
    """Get lists of sorted and unsorted PDFs for a taxpayer"""
    try:
        # Find sorted and unsorted folders
        sorted_folder = organizer_utils.create_sorted_folder(taxpayer_name)
        unsorted_folder = organizer_utils.create_unsorted_client_folder(taxpayer_name)
        
        # Get PDF lists
        sorted_pdfs = organizer_utils.list_folder_pdfs(sorted_folder)
        unsorted_pdfs = organizer_utils.list_folder_pdfs(unsorted_folder)
        
        return JsonResponse({
            'success': True,
            'sorted_pdfs': sorted_pdfs,
            'unsorted_pdfs': unsorted_pdfs
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)

@login_required
@require_http_methods(["POST"])
def move_pdf(request):
    """Move PDF between sorted and unsorted folders"""
    try:
        data = json_module.loads(request.body)
        filename = data.get('filename')
        from_folder = data.get('from_folder')  # 'sorted' or 'unsorted'
        to_folder = data.get('to_folder')      # 'sorted' or 'unsorted'
        taxpayer_name = data.get('taxpayer_name')
        form_type = data.get('form_type')      # Only when moving to sorted
        
        if not all([filename, from_folder, to_folder, taxpayer_name]):
            return JsonResponse({
                'success': False,
                'error': 'Missing required parameters'
            }, status=400)
        
        # Get folder paths
        if from_folder == 'sorted':
            source_folder = organizer_utils.create_sorted_folder(taxpayer_name)
        else:
            source_folder = organizer_utils.create_unsorted_client_folder(taxpayer_name)
            
        if to_folder == 'sorted':
            dest_folder = organizer_utils.create_sorted_folder(taxpayer_name)
        else:
            dest_folder = organizer_utils.create_unsorted_client_folder(taxpayer_name)
        
        source_path = os.path.join(source_folder, filename)
        
        if not os.path.exists(source_path):
            return JsonResponse({
                'success': False,
                'error': 'Source file not found'
            }, status=404)
        
        # Generate destination filename
        if to_folder == 'sorted' and form_type:
            # Generate standardized filename for sorted folder
            prefix = organizer_utils.get_form_prefix(form_type)
            safe_taxpayer = organizer_utils.sanitize_folder_name(taxpayer_name)
            safe_form = form_type.replace('-', '_').replace('_', '_').upper()
            new_filename = f"{prefix}_{safe_form}_{safe_taxpayer}_Unknown.pdf"
        elif to_folder == 'unsorted':
            # Generate unsorted filename with counter
            import random
            counter = random.randint(100, 999)
            name, ext = os.path.splitext(filename)
            new_filename = f"unsorted_{counter}{ext}"
        else:
            # Keep original filename
            new_filename = filename
        
        dest_path = os.path.join(dest_folder, new_filename)
        
        # Handle duplicates
        counter = 1
        while os.path.exists(dest_path):
            name, ext = os.path.splitext(new_filename)
            numbered_filename = f"{name}_{counter:02d}{ext}"
            dest_path = os.path.join(dest_folder, numbered_filename)
            counter += 1
        
        # Move the file
        import shutil
        shutil.move(source_path, dest_path)

        # Update database counts
        try:
            for doc in organizer_models.TaxDocument.objects.filter(user=request.user, extracted_data__isnull=False).select_related('extracted_data'):
                try:
                    first_page = doc.extracted_data.data[0]
                    taxpayer_info = first_page.get("data", {}).get("personal_information", {}).get("taxpayer")
                    if taxpayer_info:
                        first_name = taxpayer_info.get("first_name", "").strip()
                        last_name = taxpayer_info.get("last_name", "").strip()
                        doc_taxpayer_name = f"{first_name} {last_name}".strip()
                        if doc_taxpayer_name == taxpayer_name:
                            extracted = doc.extracted_data
                            if from_folder == 'sorted' and to_folder == 'unsorted':
                                extracted.sorted_forms_count = max(0, extracted.sorted_forms_count - 1)
                                extracted.unsorted_forms_count += 1
                            elif from_folder == 'unsorted' and to_folder == 'sorted':
                                extracted.unsorted_forms_count = max(0, extracted.unsorted_forms_count - 1)
                                extracted.sorted_forms_count += 1
                            extracted.save(update_fields=['sorted_forms_count', 'unsorted_forms_count'])
                            break
                except Exception:
                    continue
        except Exception:
            pass  # Don't fail the move if count update fails
    
        # Log activity
        organizer_utils.log_activity(
            user=request.user,
            action='document_upload',
            description=f'Moved PDF {filename} from {from_folder} to {to_folder} folder for {taxpayer_name}'
        )
        
        return JsonResponse({
            'success': True,
            'message': f'Successfully moved {filename} to {to_folder} folder'
        })
        
    except json_module.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Invalid JSON data'
        }, status=400)
    except Exception as e:
        logger.error(f"Error moving PDF: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)

@login_required
@require_http_methods(["POST"])
def toggle_folder_monitoring(request, document_id):
    """Toggle folder monitoring for a document"""
    document = get_object_or_404(organizer_models.TaxDocument, id=document_id, user=request.user)
    
    try:
        data = json_module.loads(request.body)
        is_active = data.get('is_active', False)
        
        # Get client name from document
        client_name = document.get_display_name()
        if not client_name or client_name == "Unknown Document":
            return JsonResponse({
                'success': False, 
                'error': 'Cannot determine client name for folder monitoring'
            }, status=400)
        
        monitoring, created = organizer_models.FolderMonitoring.objects.get_or_create(
            document=document,
            defaults={'client_name': client_name, 'is_active': False}
        )
        
        if is_active:
            monitoring.is_active = True
            monitoring.save()
            
            # Start the folder monitoring task
            task = organizer_tasks.monitor_client_folder.apply_async(
                args=[monitoring.id],
                countdown=10  # Start after 10 seconds
            )
            monitoring.celery_task_id = task.id
            monitoring.save()
            
            return JsonResponse({
                'success': True,
                'message': f'Folder monitoring activated for {client_name}',
                'is_active': True
            })
        else:
            monitoring.is_active = False
            monitoring.save()
            
            return JsonResponse({
                'success': True,
                'message': 'Folder monitoring deactivated',
                'is_active': False
            })
            
    except json_module.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON data'}, status=400)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)
