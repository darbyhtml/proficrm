from django import forms
from django.utils.html import format_html


class TaskTypeSelectWidget(forms.Select):
    """Кастомный виджет для выбора типа задачи с data-атрибутами для иконок и цветов."""
    
    def __init__(self, attrs=None, choices=()):
        super().__init__(attrs, choices)
        # Добавляем класс для стилизации и data-атрибут для идентификации
        if attrs is None:
            attrs = {}
        attrs.setdefault('class', 'w-full rounded-lg border px-3 py-2 task-type-select')
        self.attrs = attrs
    
    def render_option(self, selected_choices, option_value, option_label):
        """Переопределяем рендеринг опции для добавления data-атрибутов с иконками и цветами."""
        if option_value is None:
            option_value = ''
        option_value = str(option_value)
        
        # Получаем TaskType для этой опции
        task_type = None
        if option_value:
            try:
                from tasksapp.models import TaskType
                task_type = TaskType.objects.filter(id=int(option_value)).first()
            except (ValueError, TypeError):
                pass
        
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
