/* Extracted from backend/templates/ui/company_detail.html W1.3 #6.
 * Delegated event handlers заменяют inline on* attributes для CSP strict compatibility.
 * Zero behavior change.
 *
 * Data attributes:
 *   data-confirm="<msg>"           → confirm(msg) перед submit, отменяет если cancel
 *   data-stop-propagation          → stopPropagation() on click
 *   data-switch-tab="<tab>"        → вызов window.switchModernCompanyTab(tab)
 *   data-navigate="<url>"          → window.location = url on click
 */

(function () {
  "use strict";

  // data-confirm: перехватываем submit, показываем confirm, отменяем при cancel
  document.addEventListener("submit", function (e) {
    var target = e.target;
    if (!(target instanceof HTMLFormElement)) return;
    var msg = target.getAttribute("data-confirm");
    if (msg && !window.confirm(msg)) {
      e.preventDefault();
      e.stopPropagation();
    }
  });

  // Все data-* click handlers — одна delegation на document
  document.addEventListener("click", function (e) {
    var el = e.target;
    // Walk up до элемента с нужным data-attribute (закрытая структура хардикод)
    var node = el;
    while (node && node !== document) {
      if (node.hasAttribute && node.hasAttribute("data-stop-propagation")) {
        e.stopPropagation();
        // не return — продолжаем искать другие data-атрибуты
      }
      if (node.hasAttribute && node.hasAttribute("data-switch-tab")) {
        var tab = node.getAttribute("data-switch-tab");
        if (typeof window.switchModernCompanyTab === "function") {
          window.switchModernCompanyTab(tab);
        }
        return;
      }
      if (node.hasAttribute && node.hasAttribute("data-navigate")) {
        var url = node.getAttribute("data-navigate");
        if (url) {
          window.location = url;
        }
        return;
      }
      node = node.parentNode;
    }
  });
})();
