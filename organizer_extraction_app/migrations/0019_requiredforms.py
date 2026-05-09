# Generated manually for RequiredForms model

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('organizer_extraction_app', '0018_extracteddata_classified_forms'),
    ]

    operations = [
        migrations.CreateModel(
            name='RequiredForms',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('forms_list', models.TextField(blank=True)),
                ('generated_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('extracted_data', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='required_forms', to='organizer_extraction_app.extracteddata')),
            ],
        ),
    ]