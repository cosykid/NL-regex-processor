# Multi-column matching: per-column predicates + AND/OR combinator.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('jobs', '0002_remove_uploadedfile_preview_rows'),
    ]

    operations = [
        migrations.AddField(
            model_name='job',
            name='predicates',
            field=models.JSONField(default=list),
        ),
        migrations.AddField(
            model_name='job',
            name='combinator',
            field=models.CharField(
                choices=[('all', 'All (AND)'), ('any', 'Any (OR)')],
                default='all',
                max_length=8,
            ),
        ),
    ]
