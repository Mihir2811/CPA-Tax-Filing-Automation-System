# Generated manually to remove unsorted files tracking

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('organizer_extraction_app', '0023_add_unsorted_files'),
    ]

    operations = [
        migrations.DeleteModel(
            name='UnsortedFile',
        ),
        migrations.RemoveField(
            model_name='extracteddata',
            name='unsorted_files_list',
        ),
    ]