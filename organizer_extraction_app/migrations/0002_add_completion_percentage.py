# Generated migration for completion_percentage field

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('organizer_extraction_app', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='extracteddata',
            name='completion_percentage',
            field=models.FloatField(default=0.0),
        ),
    ]