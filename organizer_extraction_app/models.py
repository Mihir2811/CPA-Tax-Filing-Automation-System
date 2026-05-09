from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
import uuid
import hashlib
from organizer_extraction_app import constants as orgnanizer_constants

def default_classified_forms():
    return {}

class UserProfile(models.Model):
    """Extended user profile with role and additional details"""
    ROLE_CHOICES = orgnanizer_constants.ROLE_CHOICES
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='client')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"{self.user.username}"
class TaxDocument(models.Model):
    """Model to store uploaded tax documents"""
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='tax_documents')
    file = models.FileField(upload_to='tax_documents/', null=True, blank=True)
    file_name = models.CharField(max_length=255, null=True, blank=True)  # Original filename
    s3_key = models.CharField(max_length=500, null=True, blank=True)  # S3 object key/path
    uploaded_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=[
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed')
    ], default='pending')
    
    class Meta:
        ordering = ['-uploaded_at']
    
    def __str__(self):
        return f"{self.user.username} - {self.file_name or self.file.name}"
    
    def get_file_url(self):
        """Get presigned URL for S3 file or local file path"""
        if self.s3_key:
            from .utils import get_s3_file_url
            return get_s3_file_url(self.s3_key)
        return self.file.url if self.file else None
    
    def get_file_path(self):
        """Get file path for processing (local or download from S3)"""
        if self.s3_key:
            # For S3 files, we'll need to download temporarily for processing
            return None  # Will be handled in views
        return self.file.path if self.file else None
    
    def get_display_name(self):
        """Get clean display name using taxpayer info if available"""
        if hasattr(self, 'extracted_data') and self.extracted_data:
            try:
                first_page = self.extracted_data.data[0]
                taxpayer_info = first_page.get("data", {}).get("personal_information", {}).get("taxpayer")
                if taxpayer_info:
                    first_name = taxpayer_info.get("first_name", "").strip()
                    last_name = taxpayer_info.get("last_name", "").strip()
                    if first_name and last_name:
                        return f"{first_name} {last_name}"
            except Exception:
                pass
        # Fallback to filename without path and extension
        if self.file_name:
            filename = self.file_name
        elif self.file:
            filename = self.file.name.split('/')[-1]
        else:
            return "Unknown Document"
        return filename.rsplit('.', 1)[0] if '.' in filename else filename


class ExtractedData(models.Model):
    """Model to store extracted tax form data"""
    
    document = models.OneToOneField(TaxDocument, on_delete=models.CASCADE, related_name='extracted_data')
    data = models.JSONField()
    extracted_at = models.DateTimeField(auto_now_add=True)
    pages_processed = models.IntegerField(default=0)
    pages_skipped = models.IntegerField(default=0)
    pages_with_errors = models.IntegerField(default=0)
    summary_data = models.JSONField(blank=True, null=True)
    logic = models.JSONField(blank=True, null=True)
    custom_fields = models.JSONField(default=list, blank=True)
    sorted_forms_count = models.PositiveIntegerField(default=0)
    unsorted_forms_count = models.PositiveIntegerField(default=0)
    completion_percentage = models.FloatField(default=0.0)
    required_forms_json = models.JSONField(default=list, blank=True)
    
    def __str__(self):
        return f"Extracted data for {self.document.file.name}"
    
class EmailTracking(models.Model):
    document = models.ForeignKey(TaxDocument, on_delete=models.CASCADE, related_name='email_tracking', null=True, blank=True)
    email = models.EmailField()
    tracking_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    is_opened = models.BooleanField(default=False)
    opened_at = models.DateTimeField(null=True, blank=True)
    
class ActivityLog(models.Model):
    """Track user activities and system events"""
    timestamp = models.DateTimeField(auto_now_add=True)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='activity_logs')
    document = models.ForeignKey(TaxDocument, on_delete=models.CASCADE, related_name='activity_logs', null=True, blank=True)
    action = models.CharField(max_length=50, choices=orgnanizer_constants.ACTION_CHOICES)
    description = models.TextField()
    
    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['document', '-timestamp']),
            models.Index(fields=['user', '-timestamp']),
        ]
    
    def __str__(self):
        return f"{self.action} by {self.user.username if self.user else 'System'}"

class EmailAutomation(models.Model):
    document = models.OneToOneField(TaxDocument, on_delete=models.CASCADE, related_name='email_automation')
    is_active = models.BooleanField(default=False)
    client_email = models.EmailField()
    created_at = models.DateTimeField(auto_now_add=True)
    last_sent_at = models.DateTimeField(null=True, blank=True)
    celery_task_id = models.CharField(max_length=255, null=True, blank=True)
    
    def __str__(self):
        return f"Email automation for {self.document.get_display_name()} - {'Active' if self.is_active else 'Inactive'}"

class OrganizerFolder(models.Model):
    """Model to track organizer folders created for clients"""
    client_name = models.CharField(max_length=255)
    folder_path = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ['client_name']
    
    def __str__(self):
        return f"Organizer folder for {self.client_name}"

class ProcessedFileHash(models.Model):
    """Model to track processed PDF files by hash to avoid duplicates"""
    client_name = models.CharField(max_length=255)
    file_hash = models.CharField(max_length=64)  # SHA-256 hash
    file_name = models.CharField(max_length=255)
    processed_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ['client_name', 'file_hash']
        indexes = [
            models.Index(fields=['client_name', 'file_hash']),
        ]
    
    def __str__(self):
        return f"Processed file {self.file_name} for {self.client_name}"

class FolderMonitoring(models.Model):
    """Model to track folder monitoring for clients"""
    document = models.OneToOneField(TaxDocument, on_delete=models.CASCADE, related_name='folder_monitoring')
    client_name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    celery_task_id = models.CharField(max_length=255, null=True, blank=True)
    
    def __str__(self):
        return f"Folder monitoring for {self.client_name} - {'Active' if self.is_active else 'Inactive'}"



