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
    
    # Ключ кэша и TTL — общие для всех инстансов виджета
    _CACHE_KEY = "task_types_all_dict"
    _CACHE_TTL = 300  # 5 минут

    def _get_task_types(self):
        """Загружает все TaskType одним запросом и кэширует на 5 минут."""
        if self._task_types_cache is not None:
            return self._task_types_cache

        try:
            cached_data = cache.get(self._CACHE_KEY)
        except Exception:
            cached_data = None

        if cached_data is None:
            try:
                from tasksapp.models import TaskType

                task_types = TaskType.objects.only("id", "name", "icon", "color").all()
                cached_data = {
                    str(tt.id): {
                        "id": tt.id,
                        "name": tt.name,
                        "icon": tt.icon or "",
                        "color": tt.color or "",
                    }
                    for tt in task_types
                }
                try:
                    cache.set(self._CACHE_KEY, cached_data, self._CACHE_TTL)
                except Exception:
                    # Кэш-бэкенд временно недоступен — используем данные без кэша.
                    import logging
                    logging.getLogger(__name__).warning(
                        "TaskTypeSelectWidget: не удалось записать кэш %s", self._CACHE_KEY,
                    )
            except Exception:
                import logging
                logging.getLogger(__name__).exception(
                    "TaskTypeSelectWidget: не удалось загрузить TaskType"
                )
                cached_data = {}

        self._task_types_cache = cached_data
        return cached_data
    
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
    """Кастомный виджет для выбора пользователя с группировкой по городам подразделений."""
    
    def optgroups(self, name, value, attrs=None):
        """Группируем пользователей по городам филиалов."""
        groups = []
        has_selected = False

        # Django передаёт value как список выбранных значений (даже для single select)
        if value is None:
            value_list = []
        elif isinstance(value, (list, tuple)):
            value_list = [str(v) for v in value if v not in (None, "")]
        else:
            value_list = [str(value)]
        value_set = set(value_list)
        
        # Группируем choices по branch__name
        # Сначала загружаем всех пользователей одним запросом для оптимизации
        from collections import defaultdict
        from accounts.models import User
        
        user_ids = [str(opt[0]) for opt in self.choices if opt[0] and opt[0] != '']
        users_dict = {}
        if user_ids:
            # ВАЖНО: не исключаем администраторов здесь — список доступных пользователей
            # должен определяться queryset'ом поля (в форме/view), а не виджетом.
            users = (
                User.objects.filter(id__in=user_ids)
                .select_related('branch')
                .only('id', 'branch__name', 'role')
            )
            users_dict = {str(u.id): u for u in users}
        
        grouped = defaultdict(list)
        
        for index, (option_value, option_label) in enumerate(self.choices):
            if option_value is None:
                option_value = ''
            
            # Получаем пользователя для определения города
            branch_name = "Без филиала"
            if option_value and option_value != '':
                user = users_dict.get(str(option_value))
                if user and user.branch:
                    branch_name = user.branch.name
            
            grouped[branch_name].append((index, option_value, option_label))
            
            if str(option_value) in value_set:
                has_selected = True
        
        # Формируем optgroups. "Без филиала" НЕ показываем по требованию.
        group_index = 0
        # Сортируем филиалы, но исключаем "Без филиала"
        branch_names = [bn for bn in grouped.keys() if bn != "Без филиала"]
        branch_names.sort()

        for branch_name in branch_names:
            subgroup = []
            for index, option_value, option_label in grouped[branch_name]:
                # ВАЖНО: value опции должен быть option_value (id пользователя),
                # иначе у всех опций будет одинаковый value вроде "['1']".
                option = self.create_option(
                    name,
                    option_value,
                    option_label,
                    str(option_value) in value_set,
                    index,
                )
                subgroup.append(option)
            
            # Добавляем группу только если в ней есть опции
            if subgroup:
                groups.append((branch_name, subgroup, group_index))
                group_index += 1
        
        # Не добавляем группу "Другое" - если значение не найдено, просто не показываем его
        
        return groups