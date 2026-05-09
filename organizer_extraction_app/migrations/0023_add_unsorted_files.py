# Generated manually for unsorted files tracking

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('organizer_extraction_app', '0022_merge_20251127_1102'),
    ]

    operations = [
        migrations.AddField(
            model_name='extracteddata',
            name='unsorted_files_list',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.CreateModel(
            name='UnsortedFile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('filename', models.CharField(max_length=255)),
                ('file_path', models.CharField(max_length=500)),
                ('file_size', models.PositiveIntegerField(default=0)),
                ('added_at', models.DateTimeField(auto_now_add=True)),
                ('extracted_data', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='unsorted_files', to='organizer_extraction_app.extracteddata')),
            ],
            options={
                'ordering': ['added_at'],
            },
        ),
        migrations.AlterUniqueTogether(
            name='unsortedfile',
            unique_together={('extracted_data', 'filename')},
        ),
    ]