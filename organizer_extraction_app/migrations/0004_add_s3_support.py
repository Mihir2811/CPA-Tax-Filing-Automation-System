# Generated migration for S3 support

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('organizer_extraction_app', '0003_remove_extracteddata_summary_text_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='taxdocument',
            name='file_name',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='taxdocument',
            name='s3_key',
            field=models.CharField(blank=True, max_length=500, null=True),
        ),
        migrations.AlterField(
            model_name='taxdocument',
            name='file',
            field=models.FileField(blank=True, null=True, upload_to='tax_documents/'),
        ),
    ]