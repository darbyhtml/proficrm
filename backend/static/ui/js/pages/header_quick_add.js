/* UX-4 (2026-04-23): Header universal quick-add dropdown.
 *
 * Items:
 *   Задача   → открывает V2Modal с partial /tasks/v2/new/partial/.
 *   Компания → navigate к /companies/new/ (href на <a> — default browser behavior).
 *
 * Accessibility:
 *   - aria-haspopup + aria-expanded.
 *   - Escape закрывает menu + focus trigger.
 *   - Click outside closes menu.
 *
 * CSP strict compliant — delegated listeners + data-action pattern.
 */

(function () {
  "use strict";

  var wrapper = document.getElementById("quickAddWrapper");
  var trigger = document.getElementById("quickAddBtn");
  var menu = document.getElementById("quickAddMenu");
  if (!wrapper || !trigger || !menu) return;

  function isOpen() {
    return !menu.classList.contains("hidden");
  }

  function openMenu() {
    menu.classList.remove("hidden");
    trigger.setAttribute("aria-expanded", "true");
  }

  function closeMenu() {
    menu.classList.add("hidden");
    trigger.setAttribute("aria-expanded", "false");
  }

  function toggle() {
    if (isOpen()) closeMenu();
    else openMenu();
  }

  // Toggle dropdown on trigger click.
  trigger.addEventListener("click", function (event) {
    event.preventDefault();
    event.stopPropagation();
    toggle();
  });

  // Close on outside click.
  document.addEventListener("click", function (event) {
    if (!isOpen()) return;
    if (!wrapper.contains(event.target)) {
      closeMenu();
    }
  });

  // Close on Escape.
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && isOpen()) {
      closeMenu();
      trigger.focus();
    }
  });

  // Task item — delegated click.
  menu.addEventListener("click", function (event) {
    var taskBtn = event.target.closest('[data-action="quick-add-task"]');
    if (taskBtn) {
      event.preventDefault();
      closeMenu();
      if (window.V2Modal && typeof window.V2Modal.open === "function") {
        window.V2Modal.open({
          url: "/tasks/v2/new/partial/",
          title: "Новая задача",
        });
      } else {
        // Fallback — page navigation if V2Modal somehow not loaded.
        window.location.href = "/tasks/";
      }
      return;
    }
    // Company <a href> item uses default browser navigation — no JS needed.
    // Closing menu for any menu item click (defensive).
    var anyItem = event.target.closest("[role='menuitem']");
    if (anyItem) {
      closeMenu();
    }
  });
})();
