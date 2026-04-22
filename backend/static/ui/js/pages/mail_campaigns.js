/* Extracted from backend/templates/ui/mail/campaigns.html — W2.3 Phase 2b.
 *
 * Campaigns list page триггерил script-src-attr CSP violation на line ~429
 * (после минификации) — inline onchange/onclick attributes. Enforce
 * заблокировал бы filter selects + quota refresh button в strict mode.
 *
 * Extract:
 *   - onchange="this.form.submit()" на <select name="branch">   → data-action="filter-submit".
 *   - onchange="this.form.submit()" на <select name="manager">  → data-action="filter-submit".
 *   - onclick="window.__refreshQuota && window.__refreshQuota(true)" на button "Обновить"
 *     → data-action="quota-refresh".
 *
 * Zero behavior change — filter auto-submit + manual quota refresh работают идентично.
 */

(function () {
  "use strict";

  // Delegated change listener: [data-action="filter-submit"] → submit parent form.
  document.addEventListener("change", function (event) {
    var trigger = event.target.closest('[data-action="filter-submit"]');
    if (!trigger) return;
    var form = trigger.form || trigger.closest("form");
    if (form && typeof form.submit === "function") {
      form.submit();
    }
  });

  // Delegated click listener: [data-action="quota-refresh"] → __refreshQuota(true).
  document.addEventListener("click", function (event) {
    var trigger = event.target.closest('[data-action="quota-refresh"]');
    if (!trigger) return;
    if (typeof window.__refreshQuota === "function") {
      window.__refreshQuota(true);
    }
  });
})();
