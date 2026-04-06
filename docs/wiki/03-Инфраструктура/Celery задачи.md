---
tags: [инфраструктура, celery, задачи]
---

# Celery задачи

Broker: Redis (DB 1). Result backend: Redis (DB 2).
Serializer: JSON. Timeout: 30 мин hard, 25 мин soft. Acks: late.

## Периодические задачи (CELERY_BEAT_SCHEDULE)

### Рассылки
| Задача | Интервал |
|--------|---------|
| `send-pending-emails` | 1 мин |
| `sync-smtp-bz-quota` | 5 мин |
| `sync-smtp-bz-unsubscribes` | 10 мин |
| `sync-smtp-bz-delivery-events` | 10 мин |
| `reconcile-mail-campaign-queue` | 5 мин |

### Мессенджер
| Задача | Интервал |
|--------|---------|
| `escalate-old-conversations` | Ежедневно 09:00 MSK |
| `close-old-conversations` | Ежедневно 03:00 MSK |
| `escalate-stalled-conversations` | Каждые 2 мин |
| `auto-resolve-conversations` | Каждые 5 мин |

### Компании
| Задача | Интервал |
|--------|---------|
| `reindex-companies-daily` | Ежедневно 00:00 MSK |

### Задачи (TasksApp)
| Задача | Интервал |
|--------|---------|
| `generate-recurring-tasks` | Ежедневно 06:00 MSK |

### Телефония
| Задача | Интервал |
|--------|---------|
| `clean-old-call-requests` | Каждый час |

### Очистка (retention)
| Задача | Интервал |
|--------|---------|
| `purge-old-activity-events` | Вс 03:00 MSK (180 дней) |
| `purge-old-error-logs` | Вс 03:15 MSK (90 дней) |
| `purge-old-notifications` | Вс 03:30 MSK (90 дней) |

---

Связано: [[Docker и сервисы]] · [[Рассылки]] · [[Мессенджер]]
