/* Extracted from backend/templates/ui/settings/messenger_inbox_form.html — W2.3 Phase 2c.
 *
 * 5 inline handlers заменены на data-* атрибуты + delegated listeners:
 *   onclick="copyCode()"       → data-action="copy-code".
 *   onclick="copyLazyCode()"   → data-action="copy-lazy-code".
 *   onclick="copyToken()"      → data-action="copy-token".
 *   onsubmit="return confirm('Удалить источник…')"  → data-confirm="<msg>".
 *   onsubmit="return confirm('Старый токен перестанет…')" → data-confirm="<msg>".
 *
 * Функции copyCode / copyLazyCode / copyToken остаются в inline <script nonce>
 * внутри template, т.к. ссылаются на Django-переменные `{{ inbox.widget_token }}`
 * и `{{ base_url }}`. Здесь — только делегированный event wiring.
 *
 * Zero behavior change.
 */

(function () {
  "use strict";

  // Delegated click listener для copy-* actions.
  document.addEventListener("click", function (event) {
    var trigger = event.target.closest("[data-action]");
    if (!trigger) return;
    var action = trigger.getAttribute("data-action");

    if (action === "copy-code" && typeof window.copyCode === "function") {
      window.copyCode();
    } else if (
      action === "copy-lazy-code" &&
      typeof window.copyLazyCode === "function"
    ) {
      window.copyLazyCode();
    } else if (action === "copy-token" && typeof window.copyToken === "function") {
      window.copyToken();
    }
  });

  // Delegated submit listener для [data-confirm] forms.
  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    var msg = form.getAttribute("data-confirm");
    if (msg && !window.confirm(msg)) {
      event.preventDefault();
      event.stopPropagation();
    }
  });
})();
