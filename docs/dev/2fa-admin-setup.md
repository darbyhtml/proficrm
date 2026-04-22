# Admin 2FA Setup Guide

Mandatory 2FA для admin accounts (W2.2 staging, W9.10 prod).

Applies to users с:
- `is_superuser=True`, или
- `is_staff=True`, или
- `role='admin'`.

Non-admin roles (MANAGER, BRANCH_DIRECTOR, SALES_HEAD, GROUP_MANAGER, TENDERIST) — 2FA не требуется.

---

## Setup steps

1. Login в CRM как admin user (username + password как обычно).
2. Navigate to **https://crm-staging.groupprofi.ru/accounts/2fa/setup/**
   (прод URL будет `https://crm.groupprofi.ru/accounts/2fa/setup/` после W9.10).
3. Отсканируй QR-код в authenticator app:
   - Google Authenticator (Android / iOS) — рекомендуется.
   - Authy.
   - 1Password / Bitwarden (поддерживает TOTP).
   - Microsoft Authenticator.
4. Введи 6-значный код из приложения в форме.
5. **Сохрани recovery codes** (10 штук, format `XXXX-XXXX`):
   - В password manager (1Password, Bitwarden, KeePass).
   - Или распечатай, положи в сейф.
   - **Каждый код одноразовый. Использовать только если потеряешь authenticator.**
6. Logout.
7. Login again:
   - Username + password (как обычно).
   - После password будет prompt для 6-значного кода — введи из authenticator.
8. Готово — session verified, можешь работать.

---

## Typical daily usage

- Один раз per session: 2FA prompt после login.
- Session остаётся verified пока не logout (или cookies не истекут).
- Следующий login — повторный 2FA prompt.

---

## Losing access scenarios

### Case 1: потерян телефон с authenticator

Use recovery code на verify-странице:
1. На login page введи username + password.
2. На 2FA prompt нажми «Использовать recovery code».
3. Введи один из 10 сохранённых codes (format `XXXX-XXXX`).
4. Код consumed (одноразовый) — remaining codes работают.
5. После login **setup 2FA заново** на новом устройстве.

### Case 2: потеряны recovery codes AND authenticator

Contact Dmitry. Он через shell:
```python
# docker compose exec web python manage.py shell
from accounts.models import AdminTOTPDevice
AdminTOTPDevice.objects.filter(user__username="YOUR_USERNAME").delete()
```

После этого login → redirect к setup flow → setup fresh.

Полный runbook: `docs/runbooks/2fa-rollback.md`.

---

## Security notes

- **Не делись 2FA secret с кем-либо**. Secret = plaintext base32 string в БД (mitigated через DB access control + HTTPS).
- **Recovery codes = same security level как password** — treat accordingly.
- Если подозреваешь compromise — delete device + recovery codes + setup fresh (Option 2 в rollback runbook).

---

## Implementation details (for developers)

- Package: `pyotp` + `qrcode[pil]` (standard Python TOTP).
- Algorithm: HMAC-SHA1, 30-second window, ±1 window drift tolerance.
- Secret storage: plaintext base32 в `accounts_admintotpdevice.secret_key` (CharField 32).
- Recovery codes: SHA-256 hash в `accounts_adminrecoverycode.code_hash` (plaintext never stored).
- Session flag: `request.session['otp_verified'] = True` после successful verify.
- Middleware: `accounts.middleware_2fa.TwoFactorMandatoryMiddleware`.
- Safe paths (no 2FA required): `/accounts/login|logout|2fa/*`, `/static/`, `/media/`, `/live/`, `/ready/`, `/health/`, `/api/*`.

Source:
- `backend/accounts/models.py::AdminTOTPDevice`, `AdminRecoveryCode`.
- `backend/accounts/views_2fa.py` — setup / verify views.
- `backend/accounts/middleware_2fa.py` — enforcement middleware.
- `backend/accounts/tests_2fa.py` — 20 unit tests.
- `backend/templates/accounts/2fa/*.html` — templates.
