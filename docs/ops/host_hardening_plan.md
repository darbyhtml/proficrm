# План ужесточения хоста (без потери доступа)

Оставшиеся дыры до «100% по инфраструктурной безопасности»:

- **PermitRootLogin yes** — вход по SSH под root разрешён
- **Пароли по SSH** — PasswordAuthentication yes
- **Postgres 0.0.0.0:5432** — порт доступен снаружи (если есть)
- **ufw выключен** — нет базового файрвола

Ниже — безопасный порядок шагов, чтобы не отрезать себе доступ.

---

## 0. Подготовка

- Резервный способ доступа: консоль VPS/гипервизора (VNC, «Emergency console» и т.п.) или второй SSH-ключ на другой машине.
- Убедитесь, что по SSH вы заходите по ключу (не по паролю).

---

## 1. Новый пользователь с sudo и ключом (до отключения root)

```bash
# от root или через sudo
adduser deploy
usermod -aG sudo deploy
mkdir -p /home/deploy/.ssh
cp ~/.ssh/authorized_keys /home/deploy/.ssh/   # или вставьте свой pub-ключ
chown -R deploy:deploy /home/deploy/.ssh
chmod 700 /home/deploy/.ssh
chmod 600 /home/deploy/.ssh/authorized_keys
```

**Проверка:** в **новой** сессии (не закрывая текущую root) выполните `ssh deploy@<хост>`. Вход по ключу должен пройти. `sudo -i` или `sudo whoami` — должны работать.

---

## 2. SSH: отключение паролей и root

Редактировать `/etc/ssh/sshd_config`:

```
PermitRootLogin no
PasswordAuthentication no
PubkeyAuthentication yes
```

(При необходимости раскомментировать и выставить именно эти значения.)

```bash
sudo systemctl reload sshd
# или: sudo systemctl restart sshd
```

**Проверка:** снова зайдите `ssh deploy@<хост>` в новой сессии. Root по SSH больше не должен пускать. Только после этого закрывайте сессию root.

---

## 3. UFW: не резать SSH при включении

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp    # SSH — обязательно до enable
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status
```

Порядок важен: сначала `allow 22`, потом `enable`. Иначе можно потерять SSH.

---

## 4. Postgres: порт 5432 не наружу

### 4a. Postgres в Docker (docker-compose.prod.yml)

В нашем `docker-compose.prod.yml` у сервиса `db` нет `ports:` — 5432 только во внутренней сети Docker. Наружу не светится.

Если в вашем проекте или другом compose есть:

```yaml
ports:
  - "5432:5432"
```

или `- "0.0.0.0:5432:5432"` — заменить на привязку только к localhost:

```yaml
ports:
  - "127.0.0.1:5432:5432"
```

либо убрать `ports:` совсем, если с хоста к Postgres подключаться не нужно. После правок: `docker compose -f docker-compose.prod.yml up -d`.

### 4b. Postgres установлен на хосте

В `postgresql.conf`:

```
listen_addresses = 'localhost'
```

Перезапуск: `sudo systemctl restart postgresql` (или `postgresql@...` по вашей версии).

### 4c. Проверка

```bash
ss -tlnp | grep 5432
# или
netstat -tlnp | grep 5432
```

Должно быть `127.0.0.1:5432` или `[::1]:5432`, а не `0.0.0.0:5432` / `[::]:5432`.

---

## 5. Чеклист после применения

- [ ] `ssh root@<хост>` — отказ (PermitRootLogin no)
- [ ] `ssh deploy@<хост>` по ключу — ок
- [ ] `ssh deploy@<хост>` по паролю — отказ (PasswordAuthentication no)
- [ ] `sudo ufw status` — 22, 80, 443 allowed; default incoming deny
- [ ] `ss -tlnp | grep 5432` — только 127.0.0.1 или ::1, не 0.0.0.0
- [ ] Сайт и CRM работают (проверка после ufw и при необходимости правок Postgres)

---

## 6. Риски при пропуске шагов

- Включение ufw до `ufw allow 22` — риск потери SSH.
- Выключение root и паролей до проверки входа под `deploy` по ключу — риск потери доступа.
- Смена `listen_addresses`/`ports` для Postgres без перезапуска сервиса/контейнеров — изменения не применятся.

Поэтому порядок: сначала пользователь и ключ → проверка входа → ужесточение SSH → ufw → Postgres.
