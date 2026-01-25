# Логирование и ротация (crm.groupprofi.ru)

## Nginx

- Логи: `/var/log/nginx/access.log`, `/var/log/nginx/error.log`
- Ротация: скопировать `config/logrotate-nginx-proficrm.conf` в `/etc/logrotate.d/`:
  ```bash
  sudo cp /opt/proficrm/config/logrotate-nginx-proficrm.conf /etc/logrotate.d/nginx-proficrm
  sudo logrotate -d /etc/logrotate.d/nginx-proficrm   # пробный прогон
  sudo logrotate -f /etc/logrotate.d/nginx-proficrm   # принудительно один раз
  ```
- Политика: daily, rotate 14, compress.

## Docker (логи контейнеров)

Ограничить размер логов в `daemon.json`:

```bash
# /etc/docker/daemon.json (или создать, или дописать)
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  }
}
# затем: sudo systemctl restart docker
```

Для уже запущенных контейнеров лимит применится после пересоздания (`docker compose up -d`).

## Проверки

```bash
# размер логов nginx
ls -la /var/log/nginx/

# размер логов контейнера
docker inspect --format '{{.LogPath}}' $(docker compose -f docker-compose.prod.yml ps -q web) 2>/dev/null
ls -la /var/lib/docker/containers/<container_id>/*.log

# последние строки
docker compose -f docker-compose.prod.yml logs --tail=50 web
```
