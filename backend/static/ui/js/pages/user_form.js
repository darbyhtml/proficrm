/* Extracted from backend/templates/ui/settings/user_form.html — W2.3 Phase 2c.
 *
 * 5 inline onclick handlers заменены на data-action атрибуты:
 *   onclick="switchAccessKeyTab('token')" → data-action="switch-access-key-tab" data-tab="token".
 *   onclick="switchAccessKeyTab('link')"  → data-action="switch-access-key-tab" data-tab="link".
 *   onclick="copyMagicToken()"            → data-action="copy-magic-token".
 *   onclick="copyMagicLink()"             → data-action="copy-magic-link".
 *   onclick="copyAdminPassword()"         → data-action="copy-admin-password".
 *
 * Functions switchAccessKeyTab / copyMagicToken / copyMagicLink /
 * copyAdminPassword остаются в inline <script nonce> внутри template
 * (declared на top-level → window-scoped). Здесь только делегированный
 * event wiring.
 *
 * Zero behavior change.
 */

(function () {
  "use strict";

  document.addEventListener("click", function (event) {
    var trigger = event.target.closest("[data-action]");
    if (!trigger) return;
    var action = trigger.getAttribute("data-action");

    if (action === "switch-access-key-tab") {
      var tab = trigger.getAttribute("data-tab");
      if (tab && typeof window.switchAccessKeyTab === "function") {
        window.switchAccessKeyTab(tab);
      }
    } else if (
      action === "copy-magic-token" &&
      typeof window.copyMagicToken === "function"
    ) {
      window.copyMagicToken();
    } else if (
      action === "copy-magic-link" &&
      typeof window.copyMagicLink === "function"
    ) {
      window.copyMagicLink();
    } else if (
      action === "copy-admin-password" &&
      typeof window.copyAdminPassword === "function"
    ) {
      window.copyAdminPassword();
    }
  });
})();
