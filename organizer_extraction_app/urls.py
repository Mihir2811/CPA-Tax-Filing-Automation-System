from django.urls import path
from . import views

urlpatterns = [
    path('', views.document_list, name='document_list'),
    path('upload/', views.upload_document, name='upload_document'),
    path('process/<int:document_id>/', views.process_document, name='process_document'),
    path('results/<int:document_id>/', views.view_results, name='view_results'),
    path('summary/<int:document_id>/', views.view_summary, name='view_summary'),
    path('download/<int:document_id>/', views.download_json, name='download_json'),
    path('download-summary/<int:document_id>/', views.download_summary, name='download_summary'),
    path('send-email/<int:document_id>/', views.send_email, name='send_email'),
    path('download-pdf/<int:document_id>/', views.download_pdf, name='download_pdf'),
    path('track-email/<str:tracking_id>/', views.track_email, name='track_email'),
    path('status/', views.check_status, name='check_status'),
    path('users/', views.user_list, name='user_list'),
    
    path('users/create/', views.user_create, name='user_create'),
    path('users/<int:user_id>/edit/', views.user_edit, name='user_edit'),
    path('users/<int:user_id>/', views.user_detail, name='user_detail'),
    path('users/<int:user_id>/delete/', views.user_delete, name='user_delete'),
    path('send-pending-docs/<int:document_id>/', views.send_pending_docs_email, name='send_pending_docs_email'),
    path('toggle-automation/<int:document_id>/', views.toggle_email_automation, name='toggle_email_automation'),
    path('add-custom-field/<int:document_id>/', views.add_custom_field, name='add_custom_field'),
    path('delete-custom-field/<int:document_id>/', views.delete_custom_field, name='delete_custom_field'),

    path('create-organizer-folder/', views.create_organizer_folder_view, name='create_organizer_folder'),
    path('required-forms-json/<int:document_id>/', views.get_required_forms_json, name='get_required_forms_json'),
    path('process-forms-app/<int:document_id>/', views.process_forms_app, name='process_forms_app'),
    path('client-required-forms/<str:taxpayer_name>/', views.get_client_required_forms, name='get_client_required_forms'),
    path('get-pdf-lists/<str:taxpayer_name>/', views.get_pdf_lists, name='get_pdf_lists'),
    path('move-pdf/', views.move_pdf, name='move_pdf'),
    path('toggle-folder-monitoring/<int:document_id>/', views.toggle_folder_monitoring, name='toggle_folder_monitoring'),

]