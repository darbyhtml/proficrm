from django.apps import AppConfig


class TasksappConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = 'tasksapp'
    verbose_name = "Задачи"
    
    def ready(self):
        import tasksapp.signals  # noqa
