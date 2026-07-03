# New `find` output action: report/highlight matches only — no cell is edited
# and no row is filtered. Choices-only change; no schema alteration.

from django.db import migrations, models

_CHOICES = [
    ('auto', 'Auto (AI decides)'),
    ('find', 'Find only'),
    ('replace', 'Replace'),
    ('mask', 'Mask'),
    ('extract', 'Extract'),
    ('keep', 'Keep rows'),
    ('drop', 'Drop rows'),
]


class Migration(migrations.Migration):

    dependencies = [
        ('jobs', '0004_job_action_resolved_action'),
    ]

    operations = [
        migrations.AlterField(
            model_name='job',
            name='action',
            field=models.CharField(choices=_CHOICES, default='auto', max_length=8),
        ),
        migrations.AlterField(
            model_name='job',
            name='resolved_action',
            field=models.CharField(
                blank=True, choices=_CHOICES, default='', max_length=8
            ),
        ),
    ]
