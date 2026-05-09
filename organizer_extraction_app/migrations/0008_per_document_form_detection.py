# Generated manually for per-document form detection

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('organizer_extraction_app', '0007_add_form_detection'),
    ]

    operations = [
        migrations.DeleteModel(
            name='FormDetectionToggle',
        ),
        migrations.AddField(
            model_name='taxdocument',
            name='form_detection_enabled',
            field=models.BooleanField(default=False),
        ),
    ]