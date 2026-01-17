from django import forms
from django.utils.html import format_html
from django.core.cache import cache


class TaskTypeSelectWidget(forms.Select):
    """Кастомный виджет для выбора типа задачи с data-атрибутами для иконок и цветов."""
    
    def __init__(self, attrs=None, choices=()):
        super().__init__(attrs, choices)
        # Добавляем класс для стилизации и data-атрибут для идентификации
        if attrs is None:
            attrs = {}
        attrs.setdefault('class', 'w-full rounded-lg border px-3 py-2 task-type-select')
        self.attrs = attrs
        # Кэш для TaskType (загружаем один раз)
        self._task_types_cache = None
    
    def _get_task_types(self):
        """Загружает все TaskType одним запросом и кэширует."""
        if self._task_types_cache is None:
            from tasksapp.models import TaskType
            # Используем кэш Django для оптимизации (TTL 5 минут)
            cache_key = 'task_types_all'
            self._task_types_cache = cache.get(cache_key)
            if self._task_types_cache is None:
                self._task_types_cache = {
                    str(tt.id): tt for tt in TaskType.objects.only('id', 'name', 'icon', 'color').all()
                }
                cache.set(cache_key, self._task_types_cache, 300)  # 5 минут
        return self._task_types_cache
    
    def render_option(self, selected_choices, option_value, option_label):
        """Переопределяем рендеринг опции для добавления data-атрибутов с иконками и цветами."""
        if option_value is None:
            option_value = ''
        option_value = str(option_value)
        
        # Получаем TaskType из кэша (без запросов к БД)
        task_type = None
        if option_value:
            task_types = self._get_task_types()
            task_type = task_types.get(option_value)
        
        # Если нашли TaskType, добавляем data-атрибуты
        if task_type:
            selected = 'selected' if option_value in selected_choices else ''
            return format_html(
                '<option value="{}" {} data-icon="{}" data-color="{}">{}</option>',
                option_value,
                selected,
                task_type.icon or '',
                task_type.color or '',
                task_type.name
            )
        
        # Обычный рендеринг для пустых опций
        selected = 'selected' if option_value in selected_choices else ''
        return format_html(
            '<option value="{}" {}>{}</option>',
            option_value,
            selected,
            option_label
        )
