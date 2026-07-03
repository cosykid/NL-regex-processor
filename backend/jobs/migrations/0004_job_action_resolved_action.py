# Output actions: what to do with matched text/rows (replace/mask/extract/keep/drop),
# plus the concrete action the model resolved when the request was `auto`.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('jobs', '0003_job_predicates_combinator'),
    ]

    operations = [
        migrations.AddField(
            model_name='job',
            name='action',
            field=models.CharField(
                choices=[
                    ('auto', 'Auto (AI decides)'),
                    ('replace', 'Replace'),
                    ('mask', 'Mask'),
                    ('extract', 'Extract'),
                    ('keep', 'Keep rows'),
                    ('drop', 'Drop rows'),
                ],
                default='auto',
                max_length=8,
            ),
        ),
        migrations.AddField(
            model_name='job',
            name='resolved_action',
            field=models.CharField(
                blank=True,
                choices=[
                    ('auto', 'Auto (AI decides)'),
                    ('replace', 'Replace'),
                    ('mask', 'Mask'),
                    ('extract', 'Extract'),
                    ('keep', 'Keep rows'),
                    ('drop', 'Drop rows'),
                ],
                default='',
                max_length=8,
            ),
        ),
    ]
