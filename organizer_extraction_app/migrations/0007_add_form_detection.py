# Generated manually for form detection functionality

from django.db import migrations, models
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('organizer_extraction_app', '0006_emailtracking_document'),
    ]

    operations = [
        migrations.CreateModel(
            name='FormDetectionToggle',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('is_enabled', models.BooleanField(default=False)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Form Detection Toggle',
                'verbose_name_plural': 'Form Detection Toggle',
            },
        ),
        migrations.AddField(
            model_name='taxdocument',
            name='organizer_folder',
            field=models.CharField(blank=True, max_length=500, null=True),
        ),
    ]