# Бэкапы Postgres (crm.groupprofi.ru)

## Скрипт

`scripts/backup_postgres.sh` — дамп в `PROFICRM_BACKUP_DIR` (по умолчанию `/opt/proficrm/backups`), сжатие gzip, опционально gpg.

Переменные (в .env или при вызове):

- `PROFICRM_BACKUP_DIR` — каталог (по умолчанию `/opt/proficrm/backups`)
- `BACKUP_RETENTION_DAYS` — хранить N дней (по умолчанию 14)
- `BACKUP_GPG_KEY` или `GPG_KEY_ID` — ID ключа gpg для шифрования (если пусто — без gpg)

## Cron

```bash
# Ежедневно в 03:15 (подставить путь к проекту)
15 3 * * * cd /opt/proficrm && ./scripts/backup_postgres.sh >> /var/log/proficrm-backup.log 2>&1
```

Добавить: `crontab -e` (от пользователя, под которым крутится compose) или в `/etc/cron.d/proficrm-backup`:

```
15 3 * * * appuser cd /opt/proficrm && ./scripts/backup_postgres.sh >> /var/log/proficrm-backup.log 2>&1
```

(заменить `appuser` на нужного; для `/var/log/...` нужны права на запись или отдельный лог-файл.)

## Systemd timer (альтернатива cron)

```bash
# /etc/systemd/system/proficrm-backup.service
[Unit]
Description=Proficrm Postgres backup
After=docker.service

[Service]
Type=oneshot
User=appuser
WorkingDirectory=/opt/proficrm
ExecStart=/opt/proficrm/scripts/backup_postgres.sh
```

```bash
# /etc/systemd/system/proficrm-backup.timer
[Unit]
Description=Run Proficrm backup daily

[Timer]
OnCalendar=*-*-* 03:15:00
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now proficrm-backup.timer
sudo systemctl list-timers | grep proficrm
```

## Проверка восстановления

Восстановление в отдельную БД (не трогая prod):

```bash
# 1) Распаковать (если gzip)
gunzip -k /opt/proficrm/backups/crm_20250125_031500.sql.gz
# или, если gpg: gpg -d crm_....sql.gz.gpg | gunzip > crm_restore.sql

# 2) Создать тестовую БД в том же postgres
docker compose -f docker-compose.prod.yml exec -T db psql -U crm -d postgres -c "CREATE DATABASE crm_restore_test;"

# 3) Восстановить
docker compose -f docker-compose.prod.yml exec -T db psql -U crm -d crm_restore_test -f - < /opt/proficrm/backups/crm_20250125_031500.sql
# или: cat /path/to/crm_....sql | docker compose -f docker-compose.prod.yml exec -T db psql -U crm -d crm_restore_test -f -

# 4) Проверить (число таблиц и т.п.) и удалить тестовую БД
docker compose -f docker-compose.prod.yml exec -T db psql -U crm -d crm_restore_test -c "\dt"
docker compose -f docker-compose.prod.yml exec -T db psql -U crm -d postgres -c "DROP DATABASE crm_restore_test;"
```
