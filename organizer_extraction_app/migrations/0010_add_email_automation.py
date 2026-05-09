# Generated manually for EmailAutomation model

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('organizer_extraction_app', '0009_add_processed_files_hash'),
    ]

    operations = [
        migrations.CreateModel(
            name='EmailAutomation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('is_active', models.BooleanField(default=False)),
                ('client_email', models.EmailField(max_length=254)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('last_sent_at', models.DateTimeField(blank=True, null=True)),
                ('celery_task_id', models.CharField(blank=True, max_length=255, null=True)),
                ('document', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='email_automation', to='organizer_extraction_app.taxdocument')),
            ],
        ),
    ]