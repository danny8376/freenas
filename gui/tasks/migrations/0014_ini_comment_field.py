from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tasks', '0013_init_tasks_timeout'),
    ]

    operations = [
        migrations.AddField(
            model_name='initshutdown',
            name='ini_comment',
            field=models.TextField(
                verbose_name='Comment',
                blank=True,
            ),
        ),
    ]
