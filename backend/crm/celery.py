"""
Celery configuration for CRM ПРОФИ.
"""

import os

from celery import Celery

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "crm.settings")

app = Celery("crm")

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
# - namespace='CELERY' means all celery-related configuration keys
#   should have a `CELERY_` prefix.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Load task modules from all registered Django apps.
app.autodiscover_tasks()

# Wave 0.4 (2026-04-20): подключаем request_id + Sentry tags в celery-tasks.
# Сигналы task_prerun/task_postrun кладут request_id в thread-local
# и в sentry scope для cross-reference логов web → task.
from core.celery_signals import register_signals

register_signals()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    import logging

    logging.getLogger(__name__).debug("debug_task: %r", self.request)
