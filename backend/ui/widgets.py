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
    
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        """Переопределяем create_option для добавления data-атрибутов."""
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        
        # Получаем TaskType данные
        if value and value != '':
            task_types = self._get_task_types()
            if task_types:
                task_type_data = task_types.get(str(value))
                if task_type_data:
                    icon = task_type_data.get('icon', '') or ''
                    color = task_type_data.get('color', '') or ''
                    # Добавляем data-атрибуты к attrs опции
                    if 'attrs' not in option:
                        option['attrs'] = {}
                    option['attrs']['data-icon'] = icon
                    option['attrs']['data-color'] = color
                    # Обновляем label на name из справочника
                    if task_type_data.get('name'):
                        option['label'] = task_type_data.get('name')
        
        return option


class UserSelectWithBranchWidget(forms.Select):
    """Кастомный виджет для выбора пользователя с группировкой по городам филиалов."""
    
    def optgroups(self, name, value, attrs=None):
        """Группируем пользователей по городам филиалов."""
        groups = []
        has_selected = False
        
        # Группируем choices по branch__name
        from collections import defaultdict
        grouped = defaultdict(list)
        
        for index, (option_value, option_label) in enumerate(self.choices):
            if option_value is None:
                option_value = ''
            
            # Получаем пользователя для определения города
            branch_name = "Без филиала"
            if option_value and option_value != '':
                try:
                    from accounts.models import User
                    user = User.objects.select_related('branch').filter(id=option_value).first()
                    if user and user.branch:
                        branch_name = user.branch.name
                except Exception:
                    pass
            
            grouped[branch_name].append((index, option_value, option_label))
            
            if str(option_value) == str(value):
                has_selected = True
        
        # Формируем optgroups (исключаем "Без филиала" и пустые группы)
        group_index = 0
        for branch_name in sorted(grouped.keys()):
            # Пропускаем группу "Без филиала"
            if branch_name == "Без филиала":
                continue
            
            subgroup = []
            for index, option_value, option_label in grouped[branch_name]:
                option = self.create_option(name, value, option_label, str(option_value) == str(value), index)
                subgroup.append(option)
            
            # Добавляем группу только если в ней есть опции
            if subgroup:
                groups.append((branch_name, subgroup, group_index))
                group_index += 1
        
        # Не добавляем группу "Другое" - если значение не найдено, просто не показываем его
        
        return groups