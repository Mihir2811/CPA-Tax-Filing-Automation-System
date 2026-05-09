from django.contrib import admin
from organizer_extraction_app import models as organizer_models
from organizer_extraction_app import tasks as organizer_tasks

@admin.register(organizer_models.TaxDocument)
class TaxDocumentAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'file', 'status', 'uploaded_at')
    list_filter = ('status', 'uploaded_at')
    search_fields = ('user__username', 'file')
    readonly_fields = ('uploaded_at',)

@admin.register(organizer_models.ExtractedData)
class ExtractedDataAdmin(admin.ModelAdmin):
    list_display = ('id', 'document', 'pages_processed', 'pages_skipped', 'pages_with_errors', 'has_logic', 'extracted_at')
    readonly_fields = ('extracted_at',)
    
    def has_logic(self, obj):
        return bool(obj.logic)
    has_logic.boolean = True
    has_logic.short_description = 'Logic Data'

@admin.register(organizer_models.EmailTracking)
class EmailTrackingAdmin(admin.ModelAdmin):
    list_display = (
        'email',
        'is_opened',
        'opened_at'
    )
    list_filter = (
        'is_opened', 
        'opened_at',
    )
    search_fields = ('email', 'tracking_id')
    readonly_fields = (
        'tracking_id', 
        'opened_at',
    )
@admin.register(organizer_models.ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = ('timestamp', 'user', 'action', 'description')
    list_filter = ('action', 'timestamp')
    search_fields = ('description', 'user__username')
    readonly_fields = ('timestamp',)
    ordering = ('-timestamp',)

@admin.register(organizer_models.ProcessedFileHash)
class ProcessedFileHashAdmin(admin.ModelAdmin):
    list_display = ('client_name', 'file_name', 'file_hash', 'processed_at')
    list_filter = ('client_name', 'processed_at')
    search_fields = ('client_name', 'file_name', 'file_hash')
    readonly_fields = ('processed_at',)
    ordering = ('-processed_at',)

