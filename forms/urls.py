from django.urls import path
from . import views

app_name = 'forms'

urlpatterns = [
    path('', views.index, name='index'),
    path('upload/', views.upload_and_extract, name='upload_and_extract'),
    path('stats/', views.processing_stats, name='processing_stats'),
    path('client-summary/', views.client_summary, name='client_summary'),
    path('move-pdf/', views.move_pdf, name='move_pdf'),
    path('pdf-lists/<str:client_name>/', views.get_pdf_lists, name='get_pdf_lists'),
]
