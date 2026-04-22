# 2FA Rollback Procedure (W2.2)

If you're locked out or staging 2FA misbehaves — follow one of options below.

---

## Option 1: Recovery code (when losing authenticator app)

При login на verify-странице нажми «Использовать recovery code» и введи один из 10 recovery codes (format `XXXX-XXXX`) сохранённых во время setup.

Каждый код одноразовый.

---

## Option 2: Disable 2FA device via shell

Если recovery codes тоже утеряны:

```bash
ssh -i ~/.ssh/id_proficrm_deploy sdm@5.181.254.172
cd /opt/proficrm-staging
docker compose -f docker-compose.staging.yml -p proficrm-staging exec -T web python manage.py shell
```

Delete confirmed device чтобы re-setup flow:

```python
from accounts.models import AdminTOTPDevice, AdminRecoveryCode
from django.contrib.auth import get_user_model
User = get_user_model()
u = User.objects.get(username="sdm")  # или другой admin
AdminTOTPDevice.objects.filter(user=u).delete()
AdminRecoveryCode.objects.filter(user=u).delete()
# Now middleware will redirect user к /accounts/2fa/setup/ on next request
```

После этого login как раньше, попадёшь на setup flow — setup fresh.

---

## Option 3: Disable middleware (emergency)

Если ошибка в middleware сама блокирует login flow:

```bash
ssh -i ~/.ssh/id_proficrm_deploy root@5.181.254.172
cd /opt/proficrm-staging
# Edit settings.py to comment out TwoFactorMandatoryMiddleware
docker compose -f docker-compose.staging.yml -p proficrm-staging exec -T web sed -i 's|^\(    "accounts.middleware_2fa.TwoFactorMandatoryMiddleware",\)|#\1|' /app/backend/crm/settings.py
docker compose -f docker-compose.staging.yml -p proficrm-staging restart web
```

Restore позже когда fix готов:

```bash
docker compose -f docker-compose.staging.yml -p proficrm-staging exec -T web sed -i 's|^#\(    "accounts.middleware_2fa.TwoFactorMandatoryMiddleware",\)|\1|' /app/backend/crm/settings.py
docker compose -f docker-compose.staging.yml -p proficrm-staging restart web
```

---

## Option 4: Git revert (nuclear option)

Если массовая проблема — revert the middleware enable commit:

```bash
# Identify commit via git log
git log --oneline | grep -i 'enable.*2fa\|TwoFactor'
# Revert
git revert <commit-sha>
git push
# Auto-deploy будет применять через ~2 min
```

**Safe**: revert не trogает пользовательские TOTPDevice данные — они остаются в БД на follow-up.

---

## Prevention checklist (будущие sessions)

Перед enable middleware:
- [ ] Минимум 1 admin уже имеет `confirmed=True` TOTPDevice в БД.
- [ ] Recovery codes printed/saved в password manager.
- [ ] Second admin может reach shell в case of lockout.
- [ ] Rollback procedure read + understood.

---

## Prod deployment (future, W9.10)

When activating on prod:
1. Deploy code без middleware enable (same as staging W2.2 ordering).
2. Users (2 admins) setup 2FA manually.
3. ONLY after both confirm — enable middleware в prod config.

Same safety pattern as staging.
