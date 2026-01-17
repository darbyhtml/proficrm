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
    
    def optgroups(self, name, value, attrs=None):
        """Переопределяем optgroups для добавления data-атрибутов ко всем опциям."""
        from django.forms.widgets import flatatt
        
        groups = []
        has_selected = False
        
        # Получаем данные TaskType один раз
        task_types = self._get_task_types()
        
        for index, (option_value, option_label) in enumerate(self.choices):
            if option_value is None:
                option_value = ''
            
            option_value = str(option_value)
            selected = str(option_value) == str(value)
            if selected:
                has_selected = True
            
            # Получаем TaskType данные
            task_type_data = None
            if option_value and option_value != '' and task_types:
                task_type_data = task_types.get(option_value)
            
            # Формируем HTML опции
            if task_type_data:
                icon = task_type_data.get('icon', '') or ''
                color = task_type_data.get('color', '') or ''
                name_text = task_type_data.get('name', option_label) or option_label
                option_html = format_html(
                    '<option value="{}"{} data-icon="{}" data-color="{}">{}</option>',
                    option_value,
                    ' selected' if selected else '',
                    icon,
                    color,
                    name_text
                )
            else:
                option_html = format_html(
                    '<option value="{}"{}>{}</option>',
                    option_value,
                    ' selected' if selected else '',
                    option_label
                )
            
            subgroup = [option_html]
            groups.append((None, subgroup, index))
        
        if value and not has_selected:
            # Если значение не найдено в choices, добавляем его
            task_type_data = None
            if task_types:
                task_type_data = task_types.get(str(value))
            
            if task_type_data:
                icon = task_type_data.get('icon', '') or ''
                color = task_type_data.get('color', '') or ''
                name_text = task_type_data.get('name', str(value)) or str(value)
                option_html = format_html(
                    '<option value="{}" selected data-icon="{}" data-color="{}">{}</option>',
                    value,
                    icon,
                    color,
                    name_text
                )
            else:
                option_html = format_html(
                    '<option value="{}" selected>{}</option>',
                    value,
                    value
                )
            groups.append((None, [option_html], len(groups)))
        
        return groups
    
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
                    # Логируем для отладки (можно убрать позже)
                    import logging
                    logger = logging.getLogger(__name__)
                    logger.info(f"TaskTypeSelectWidget: загружено {len(cached_data)} типов задач из БД")
                    if cached_data:
                        sample = list(cached_data.values())[0]
                        logger.info(f"TaskTypeSelectWidget: пример данных - {sample}")
                    # ВРЕМЕННО ОТКЛЮЧАЕМ КЭШ ДЛЯ ОТЛАДКИ
                    # try:
                    #     cache.set(cache_key, cached_data, 300)  # 5 минут
                    # except Exception:
                    #     # Если кэш недоступен, просто используем данные без кэширования
                    #     pass
                self._task_types_cache = cached_data
            except Exception:
                # Если что-то пошло не так, возвращаем пустой словарь
                self._task_types_cache = {}
        return self._task_types_cache
    
