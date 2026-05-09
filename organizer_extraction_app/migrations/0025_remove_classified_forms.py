# Generated manually to remove classified_forms field

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('organizer_extraction_app', '0024_remove_unsorted_files'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='extracteddata',
            name='classified_forms',
        ),
    ]