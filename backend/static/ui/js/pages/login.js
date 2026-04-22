/* Extracted from backend/templates/registration/login.html — W2.3 Phase 2b.
 *
 * Inline onclick="switchTab(...)" blocked by strict CSP (script-src-attr),
 * заменены на data-action="switch-tab" data-tab="..." + delegated listener.
 *
 * Критичный публичный endpoint — работает для всех user types (magic link
 * access_key + admin password). Strict CSP compatibility обязательна до
 * Phase 3 flip, иначе visual tab switcher сломан при enforce.
 *
 * Zero behavior change relative to prior inline script.
 */

(function () {
  "use strict";

  function switchTab(tab) {
    var accessKeyTab = document.getElementById("tab-access-key");
    var passwordTab = document.getElementById("tab-password");
    var accessKeyForm = document.getElementById("form-access-key");
    var passwordForm = document.getElementById("form-password");
    var description = document.getElementById("login-description");

    if (!accessKeyTab || !passwordTab || !accessKeyForm || !passwordForm) {
      return;
    }

    if (tab === "access-key") {
      accessKeyTab.classList.remove(
        "text-brand-dark/60",
        "hover:text-brand-dark",
        "hover:bg-brand-soft/20"
      );
      accessKeyTab.classList.add("bg-brand-teal", "text-white");
      passwordTab.classList.remove("bg-brand-teal", "text-white");
      passwordTab.classList.add(
        "text-brand-dark/60",
        "hover:text-brand-dark",
        "hover:bg-brand-soft/20"
      );

      accessKeyForm.classList.remove("hidden");
      passwordForm.classList.add("hidden");
      if (description) {
        description.textContent =
          "Вставьте ключ доступа, полученный от администратора";
      }

      var accessKeyInput = accessKeyForm.querySelector(
        'input[name="access_key"]'
      );
      if (accessKeyInput) {
        accessKeyInput.focus();
      }
    } else {
      passwordTab.classList.remove(
        "text-brand-dark/60",
        "hover:text-brand-dark",
        "hover:bg-brand-soft/20"
      );
      passwordTab.classList.add("bg-brand-teal", "text-white");
      accessKeyTab.classList.remove("bg-brand-teal", "text-white");
      accessKeyTab.classList.add(
        "text-brand-dark/60",
        "hover:text-brand-dark",
        "hover:bg-brand-soft/20"
      );

      passwordForm.classList.remove("hidden");
      accessKeyForm.classList.add("hidden");
      if (description) {
        description.textContent =
          "Вход по логину и паролю (только для администраторов)";
      }

      var usernameInput = passwordForm.querySelector('input[name="username"]');
      if (usernameInput) {
        usernameInput.focus();
      }
    }
  }

  // Delegated click listener: [data-action="switch-tab"] → switchTab(data-tab).
  document.addEventListener("click", function (event) {
    var trigger = event.target.closest('[data-action="switch-tab"]');
    if (!trigger) return;

    var tab = trigger.getAttribute("data-tab");
    if (tab) {
      switchTab(tab);
    }
  });
})();
