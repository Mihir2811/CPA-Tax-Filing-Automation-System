# Generated manually for processed files hash tracking

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('organizer_extraction_app', '0008_per_document_form_detection'),
    ]

    operations = [
        migrations.AddField(
            model_name='taxdocument',
            name='processed_files_hash',
            field=models.TextField(blank=True, null=True),
        ),
    ]