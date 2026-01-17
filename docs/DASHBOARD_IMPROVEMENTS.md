# Современные идеи для улучшения Рабочего стола (Dashboard)

**Дата:** 2026-01-17  
**Версия:** 1.0  
**Статус:** Предложения для реализации

---

## 1. Обзор текущего состояния

### 1.1. Текущий функционал

**Рабочий стол** (`/`) отображает:
- ✅ Задачи на сегодня (`tasks_today`)
- ✅ Просроченные задачи (`overdue`)
- ✅ Задачи на неделю (`tasks_week`)
- ✅ Новые задачи (`tasks_new`)
- ✅ Договоры, которые скоро истекают (`contracts_soon`)
- ✅ Кнопки для отчётов по холодным звонкам (для определённых ролей)

### 1.2. Текущие ограничения

1. **Производительность:**
   - 5 отдельных запросов к БД (tasks_today, overdue, tasks_week, tasks_new, contracts_soon)
   - Нет кэширования
   - Нет оптимизации запросов (можно объединить некоторые)

2. **UX:**
   - Статичная страница (нет автообновления)
   - Нет фильтрации/поиска на странице
   - Нет персонализации (все видят одинаковый layout)
   - Нет быстрых действий (quick actions)

3. **Аналитика:**
   - Нет метрик/статистики
   - Нет графиков/визуализаций
   - Нет трендов

4. **Мобильность:**
   - Grid layout не оптимизирован для мобильных
   - Нет адаптивных виджетов

---

## 2. Приоритетные улучшения (P0)

### 2.1. Оптимизация производительности

#### 2.1.1. Объединение запросов

**Проблема:** Сейчас выполняется 5 отдельных запросов к БД.

**Решение:** Объединить запросы задач в один с использованием `Q` объектов и аннотаций.

```python
# Вместо:
tasks_today = Task.objects.filter(...)
overdue = Task.objects.filter(...)
tasks_week = Task.objects.filter(...)
tasks_new = Task.objects.filter(...)

# Использовать:
from django.db.models import Q, Case, When, IntegerField

all_tasks = Task.objects.filter(assigned_to=user).exclude(
    status__in=[Task.Status.DONE, Task.Status.CANCELLED]
).select_related("company", "created_by").annotate(
    task_category=Case(
        When(due_at__lt=now, then=1),  # overdue
        When(due_at__gte=today_start, due_at__lt=tomorrow_start, then=2),  # today
        When(due_at__gte=week_start, due_at__lt=week_end, then=3),  # week
        When(status=Task.Status.NEW, then=4),  # new
        default=0,
        output_field=IntegerField()
    )
)

# Затем разделить в Python:
tasks_today = [t for t in all_tasks if t.task_category == 2]
overdue = [t for t in all_tasks if t.task_category == 1]
# и т.д.
```

**Выгода:** Снижение количества запросов с 4 до 1 для задач.

#### 2.1.2. Кэширование

**Проблема:** Dashboard пересчитывается при каждом запросе.

**Решение:** Использовать Django cache framework с TTL 1-2 минуты.

```python
from django.core.cache import cache
from django.utils.hashlib import md5

@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    user: User = request.user
    cache_key = f"dashboard_{user.id}_{timezone.now().date()}"
    cached_data = cache.get(cache_key)
    
    if cached_data:
        return render(request, "ui/dashboard.html", cached_data)
    
    # ... вычисления ...
    
    context = {
        "now": now,
        "local_now": local_now,
        "tasks_new": list(tasks_new),  # Преобразуем QuerySet в list для кэширования
        "tasks_today": list(tasks_today),
        "overdue": list(overdue),
        "tasks_week": list(tasks_week),
        "contracts_soon": contracts_soon,
        "can_view_cold_call_reports": _can_view_cold_call_reports(user),
    }
    
    cache.set(cache_key, context, timeout=120)  # 2 минуты
    return render(request, "ui/dashboard.html", context)
```

**Выгода:** Снижение нагрузки на БД на 80-90% для повторных запросов.

#### 2.1.3. Инвалидация кэша

**Решение:** Инвалидировать кэш при изменении задач/договоров.

```python
# В signals.py или в методах save() моделей:
from django.core.cache import cache
from django.db.models.signals import post_save, post_delete

def invalidate_dashboard_cache(sender, instance, **kwargs):
    if hasattr(instance, 'assigned_to') and instance.assigned_to:
        cache_key = f"dashboard_{instance.assigned_to.id}_*"
        cache.delete_pattern(cache_key)  # Требует django-redis

post_save.connect(invalidate_dashboard_cache, sender=Task)
post_delete.connect(invalidate_dashboard_cache, sender=Task)
```

---

### 2.2. AJAX обновление (Live Updates)

**Проблема:** Пользователь должен обновлять страницу вручную.

**Решение:** Использовать polling или WebSockets для автообновления.

```javascript
// В dashboard.html добавить:
<script>
  (function(){
    let lastUpdate = Date.now();
    async function pollDashboard(){
      try{
        const res = await fetch('/api/dashboard/poll/?since=' + lastUpdate, {
          credentials: 'same-origin'
        });
        const data = await res.json();
        if(data.updated){
          // Обновляем только изменённые секции
          updateTasksToday(data.tasks_today);
          updateOverdue(data.overdue);
          // и т.д.
          lastUpdate = Date.now();
        }
      }catch(e){
        console.error('Dashboard poll error:', e);
      }
    }
    
    // Polling каждые 30 секунд
    setInterval(pollDashboard, 30000);
  })();
</script>
```

**Backend endpoint:**

```python
@login_required
def dashboard_poll(request: HttpRequest) -> JsonResponse:
    since = request.GET.get('since')
    if since:
        since_dt = datetime.fromtimestamp(int(since) / 1000, tz=timezone.utc)
        # Проверяем, были ли изменения после since_dt
        has_changes = (
            Task.objects.filter(assigned_to=request.user, updated_at__gt=since_dt).exists() or
            Company.objects.filter(responsible=request.user, updated_at__gt=since_dt).exists()
        )
        if not has_changes:
            return JsonResponse({"updated": False})
    
    # Возвращаем обновлённые данные
    # ... (логика как в dashboard, но возвращаем JSON)
    return JsonResponse({
        "updated": True,
        "tasks_today": [...],
        "overdue": [...],
        # и т.д.
    })
```

**Выгода:** Пользователь видит актуальные данные без перезагрузки страницы.

---

## 3. Важные улучшения (P1)

### 3.1. Виджеты и персонализация

#### 3.1.1. Drag & Drop виджетов

**Решение:** Позволить пользователю настраивать layout dashboard.

```python
# Модель для хранения настроек dashboard:
class DashboardWidget(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    widget_type = models.CharField(max_length=32)  # 'tasks_today', 'overdue', etc.
    position = models.IntegerField()  # Порядок отображения
    visible = models.BooleanField(default=True)
    settings = models.JSONField(default=dict)  # Дополнительные настройки виджета
```

**Frontend:** Использовать библиотеку для drag & drop (например, `sortablejs`).

#### 3.1.2. Дополнительные виджеты

- **Статистика звонков за сегодня:** Количество звонков, дозвоняемость
- **Активность за неделю:** График выполненных задач
- **Ближайшие встречи:** Календарь событий
- **Метрики продаж:** Для менеджеров/РОП

---

### 3.2. Быстрые действия (Quick Actions)

**Решение:** Добавить панель быстрых действий на dashboard.

```html
<!-- В dashboard.html -->
<div class="quick-actions">
  <button class="btn btn-primary" onclick="createTask()">+ Задача</button>
  <button class="btn btn-primary" onclick="createCompany()">+ Компания</button>
  <button class="btn btn-outline" onclick="createCall()">📞 Звонок</button>
  <button class="btn btn-outline" onclick="createNote()">📝 Заметка</button>
</div>
```

**Выгода:** Ускорение работы пользователя.

---

### 3.3. Фильтрация и поиск

**Решение:** Добавить фильтры для каждой секции.

```html
<!-- Для задач на сегодня -->
<div class="section-header">
  <h3>На сегодня</h3>
  <select id="filter-tasks-today" onchange="filterTasksToday(this.value)">
    <option value="all">Все</option>
    <option value="new">Только новые</option>
    <option value="in_progress">В работе</option>
  </select>
</div>
```

---

### 3.4. Аналитика и метрики

#### 3.4.1. Виджет статистики

**Решение:** Добавить карточки с метриками.

```python
# В dashboard view:
metrics = {
    "tasks_completed_today": Task.objects.filter(
        assigned_to=user,
        status=Task.Status.DONE,
        completed_at__date=today_date
    ).count(),
    "tasks_overdue_count": Task.objects.filter(
        assigned_to=user,
        due_at__lt=now
    ).exclude(status__in=[Task.Status.DONE, Task.Status.CANCELLED]).count(),
    "calls_today": CallRequest.objects.filter(
        user=user,
        created_at__date=today_date
    ).count(),
    # и т.д.
}
```

**Frontend:** Отобразить в виде карточек с иконками.

#### 3.4.2. Графики

**Решение:** Использовать библиотеку для графиков (например, Chart.js).

```html
<canvas id="tasksChart"></canvas>
<script>
  const ctx = document.getElementById('tasksChart');
  new Chart(ctx, {
    type: 'line',
    data: {
      labels: ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'],
      datasets: [{
        label: 'Выполнено задач',
        data: [12, 19, 15, 20, 18, 10, 8],
        borderColor: 'rgb(1, 148, 142)',
      }]
    }
  });
</script>
```

---

## 4. Дополнительные улучшения (P2)

### 4.1. Уведомления в реальном времени

**Решение:** Использовать WebSockets (Django Channels) или Server-Sent Events (SSE).

```python
# channels/routing.py
from channels.routing import URLRouter
from django.urls import path

websocket_urlpatterns = [
    path('ws/dashboard/<int:user_id>/', DashboardConsumer.as_asgi()),
]

# channels/consumers.py
class DashboardConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.user_id = self.scope['url_route']['kwargs']['user_id']
        await self.channel_layer.group_add(
            f'dashboard_{self.user_id}',
            self.channel_name
        )
        await self.accept()
    
    async def dashboard_update(self, event):
        await self.send(text_data=json.dumps(event['data']))
```

**Выгода:** Мгновенное обновление dashboard при изменении данных.

---

### 4.2. Экспорт данных

**Решение:** Добавить кнопки экспорта для каждой секции.

```python
@login_required
def dashboard_export_tasks(request: HttpRequest) -> HttpResponse:
    # Генерируем CSV/Excel с задачами
    # ...
    return HttpResponse(content, content_type='text/csv')
```

---

### 4.3. Темная тема

**Решение:** Добавить переключатель темы.

```javascript
// Сохраняем в localStorage
localStorage.setItem('theme', 'dark');
document.documentElement.classList.add('dark');
```

---

### 4.4. Мобильная оптимизация

**Решение:**
- Адаптивный grid (1 колонка на мобильных)
- Swipe-жесты для навигации между секциями
- Bottom navigation для быстрого доступа

---

## 5. План реализации

### Этап 1 (1-2 недели): P0 улучшения
1. ✅ Объединение запросов
2. ✅ Кэширование
3. ✅ Инвалидация кэша
4. ✅ AJAX polling

### Этап 2 (2-3 недели): P1 улучшения
1. ✅ Виджеты и персонализация
2. ✅ Быстрые действия
3. ✅ Фильтрация
4. ✅ Базовые метрики

### Этап 3 (3-4 недели): P2 улучшения
1. ✅ WebSockets/SSE
2. ✅ Экспорт данных
3. ✅ Темная тема
4. ✅ Мобильная оптимизация

---

## 6. Метрики успеха

- **Производительность:** Время загрузки dashboard < 500ms (с кэшем)
- **UX:** Пользователи проводят на dashboard > 30% времени в системе
- **Удовлетворённость:** NPS > 8/10 для dashboard
- **Использование:** > 80% пользователей настраивают виджеты

---

## 7. Технические детали

### 7.1. Зависимости

- `django-redis` — для кэширования
- `channels` — для WebSockets (опционально)
- `chart.js` — для графиков
- `sortablejs` — для drag & drop

### 7.2. Миграции

```python
# migrations/XXXX_add_dashboard_widgets.py
class Migration(migrations.Migration):
    operations = [
        migrations.CreateModel(
            name='DashboardWidget',
            fields=[
                ('id', models.AutoField(...)),
                ('widget_type', models.CharField(max_length=32)),
                ('position', models.IntegerField()),
                ('visible', models.BooleanField(default=True)),
                ('settings', models.JSONField(default=dict)),
                ('user', models.ForeignKey(...)),
            ],
        ),
    ]
```

---

## 8. Риски и митигация

### 8.1. Риски

1. **Кэширование может показывать устаревшие данные**
   - Митигация: Короткий TTL (1-2 минуты) + инвалидация при изменениях

2. **WebSockets увеличивают нагрузку на сервер**
   - Митигация: Использовать polling как fallback, ограничить количество подключений

3. **Персонализация усложняет поддержку**
   - Митигация: Хранить настройки в JSON, валидировать widget_type

---

## 9. Заключение

Реализация этих улучшений превратит dashboard из статичной страницы в интерактивный, персонализированный центр управления, который повысит продуктивность пользователей и удовлетворённость системой.

**Приоритет:** Начать с P0 улучшений (производительность и AJAX), затем переходить к P1 (UX и аналитика).
