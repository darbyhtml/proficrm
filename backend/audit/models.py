import uuid
import traceback
import sys

from django.conf import settings
from django.db import models


class ActivityEvent(models.Model):
    """
    Универсальный журнал действий.
    entity_type: company/contact/task/note/user/branch/...
    entity_id: UUID/int в строковом виде (для простоты и унификации)
    """

    class Verb(models.TextChoices):
        CREATE = "create", "Создал"
        UPDATE = "update", "Изменил"
        DELETE = "delete", "Удалил"
        STATUS = "status", "Сменил статус"
        COMMENT = "comment", "Комментарий"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="Кто", null=True, on_delete=models.SET_NULL, related_name="activity_events")

    verb = models.CharField("Действие", max_length=16, choices=Verb.choices)
    entity_type = models.CharField("Сущность", max_length=32, db_index=True)
    entity_id = models.CharField("ID сущности", max_length=64, db_index=True)

    # Для удобства фильтрации по компании
    company_id = models.UUIDField("ID компании", null=True, blank=True, db_index=True)

    message = models.CharField("Описание", max_length=255, blank=True, default="")
    meta = models.JSONField("Данные", default=dict, blank=True)

    created_at = models.DateTimeField("Когда", auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.created_at} {self.entity_type}:{self.entity_id} {self.verb}"


class ErrorLog(models.Model):
    """
    Лог ошибок, произошедших на сайте.
    Аналогично error_log в MODX CMS.
    """
    
    class Level(models.TextChoices):
        ERROR = "error", "Ошибка"
        WARNING = "warning", "Предупреждение"
        CRITICAL = "critical", "Критическая"
        EXCEPTION = "exception", "Исключение"
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    level = models.CharField("Уровень", max_length=16, choices=Level.choices, default=Level.ERROR, db_index=True)
    message = models.TextField("Сообщение", blank=True, default="")
    exception_type = models.CharField("Тип исключения", max_length=255, blank=True, default="", db_index=True)
    traceback = models.TextField("Трассировка", blank=True, default="")
    
    # Информация о запросе
    path = models.CharField("Путь", max_length=500, blank=True, default="", db_index=True)
    method = models.CharField("Метод", max_length=10, blank=True, default="", db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="Пользователь", null=True, blank=True, on_delete=models.SET_NULL, related_name="error_logs")
    user_agent = models.CharField("User-Agent", max_length=500, blank=True, default="")
    ip_address = models.GenericIPAddressField("IP адрес", null=True, blank=True, db_index=True)
    
    # Дополнительная информация
    request_data = models.JSONField("Данные запроса", default=dict, blank=True)
    resolved = models.BooleanField("Исправлено", default=False, db_index=True)
    resolved_at = models.DateTimeField("Когда исправлено", null=True, blank=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="Исправил", null=True, blank=True, on_delete=models.SET_NULL, related_name="resolved_errors")
    notes = models.TextField("Заметки", blank=True, default="")
    
    created_at = models.DateTimeField("Когда произошло", auto_now_add=True, db_index=True)
    
    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Ошибка"
        verbose_name_plural = "Ошибки"
        indexes = [
            models.Index(fields=["-created_at", "resolved"]),
            models.Index(fields=["level", "resolved"]),
            models.Index(fields=["path", "resolved"]),
        ]
    
    def __str__(self) -> str:
        return f"{self.created_at} [{self.level}] {self.exception_type or self.message[:50]}"
    
    @classmethod
    def log_error(cls, exception, request=None, level=Level.ERROR, **kwargs):
        """
        Удобный метод для логирования ошибки.
        """
        try:
            exc_type, exc_value, exc_traceback = sys.exc_info()
            exception_type = f"{exc_type.__module__}.{exc_type.__name__}" if exc_type else ""
            message = str(exception) if exception else ""
            traceback_text = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback)) if exc_traceback else ""
            
            # Информация о запросе
            path = ""
            method = ""
            user = None
            user_agent = ""
            ip_address = None
            request_data = {}
            
            if request:
                path = request.path[:500]
                method = request.method[:10]
                user = getattr(request, 'user', None) if hasattr(request, 'user') and request.user.is_authenticated else None
                user_agent = request.META.get('HTTP_USER_AGENT', '')[:500]
                ip_address = cls._get_client_ip(request)
                
                # Безопасное извлечение данных запроса (без паролей и токенов)
                request_data = cls._safe_request_data(request)
            
            error_log = cls.objects.create(
                level=level,
                message=message[:10000] if len(message) > 10000 else message,
                exception_type=exception_type[:255],
                traceback=traceback_text[:50000] if len(traceback_text) > 50000 else traceback_text,
                path=path,
                method=method,
                user=user,
                user_agent=user_agent,
                ip_address=ip_address,
                request_data=request_data,
                **kwargs
            )
            
            return error_log
        except Exception as e:
            # Если не удалось сохранить ошибку в БД, логируем в консоль
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to save error log to database: {e}", exc_info=True)
            return None
    
    @staticmethod
    def _get_client_ip(request):
        """Получить IP адрес клиента из запроса."""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0].strip()
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip if ip else None
    
    @staticmethod
    def _safe_request_data(request):
        """Безопасное извлечение данных запроса (исключая чувствительные данные)."""
        data = {}
        
        # GET параметры
        if request.GET:
            data['get'] = dict(request.GET)
        
        # POST данные (исключая пароли и токены)
        if request.POST:
            safe_post = {}
            for key, value in request.POST.items():
                if any(sensitive in key.lower() for sensitive in ['password', 'token', 'secret', 'key', 'csrf']):
                    safe_post[key] = '***HIDDEN***'
                else:
                    safe_post[key] = str(value)[:500]  # Ограничиваем длину
            data['post'] = safe_post
        
        # Заголовки (только безопасные)
        if hasattr(request, 'META'):
            safe_headers = {}
            for key in ['HTTP_USER_AGENT', 'HTTP_REFERER', 'CONTENT_TYPE']:
                if key in request.META:
                    safe_headers[key] = str(request.META[key])[:500]
            data['headers'] = safe_headers
        
        return data

# Create your models here.
