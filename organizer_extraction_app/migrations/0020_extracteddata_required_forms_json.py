# Generated manually for required_forms_json field

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('organizer_extraction_app', '0019_requiredforms'),
    ]

    operations = [
        migrations.AddField(
            model_name='extracteddata',
            name='required_forms_json',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.DeleteModel(
            name='RequiredForms',
        ),
    ]