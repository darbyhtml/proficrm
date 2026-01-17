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
            try:
                from tasksapp.models import TaskType
                # Используем кэш Django для оптимизации (TTL 5 минут)
                # Кэшируем словарь с данными, а не объекты модели (объекты не pickle-able)
                cache_key = 'task_types_all_dict'
                cached_data = None
                try:
                    cached_data = cache.get(cache_key)
                except Exception:
                    # Если кэш недоступен, просто пропускаем
                    pass
                
                if cached_data is None:
                    # Загружаем все TaskType одним запросом
                    task_types = TaskType.objects.only('id', 'name', 'icon', 'color').all()
                    # Сохраняем как словарь словарей (можно кэшировать)
                    cached_data = {
                        str(tt.id): {
                            'id': tt.id,
                            'name': tt.name,
                            'icon': tt.icon or '',
                            'color': tt.color or ''
                        }
                        for tt in task_types
                    }
                    try:
                        cache.set(cache_key, cached_data, 300)  # 5 минут
                    except Exception:
                        # Если кэш недоступен, просто используем данные без кэширования
                        pass
                self._task_types_cache = cached_data
            except Exception:
                # Если что-то пошло не так, возвращаем пустой словарь
                self._task_types_cache = {}
        return self._task_types_cache
    
    def render_option(self, selected_choices, option_value, option_label):
        """Переопределяем рендеринг опции для добавления data-атрибутов с иконками и цветами."""
        if option_value is None:
            option_value = ''
        option_value = str(option_value)
        
        # Получаем TaskType из кэша (без запросов к БД)
        task_type_data = None
        if option_value:
            try:
                task_types = self._get_task_types()
                task_type_data = task_types.get(option_value) if task_types else None
            except Exception:
                # Если что-то пошло не так, используем обычный рендеринг
                task_type_data = None
        
        # Если нашли TaskType, добавляем data-атрибуты
        if task_type_data:
            selected = 'selected' if option_value in selected_choices else ''
            try:
                return format_html(
                    '<option value="{}" {} data-icon="{}" data-color="{}">{}</option>',
                    option_value,
                    selected,
                    task_type_data.get('icon', ''),
                    task_type_data.get('color', ''),
                    task_type_data.get('name', option_label)  # Используем name из кэша, или option_label как fallback
                )
            except Exception:
                # Если format_html не работает, используем обычный рендеринг
                pass
        
        # Обычный рендеринг для пустых опций
        selected = 'selected' if option_value in selected_choices else ''
        return format_html(
            '<option value="{}" {}>{}</option>',
            option_value,
            selected,
            option_label
        )
