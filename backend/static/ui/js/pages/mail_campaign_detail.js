/* Extracted from backend/templates/ui/mail/campaign_detail.html — W2.3 Phase 2c.
 *
 * 14 inline handlers заменены на data-* attributes + delegated listeners.
 * Functions (toggleAllStatuses/toggleAllSpheres/toggleAllRegions/modal
 * toggling) остаются в inline <script nonce> внутри template; здесь
 * только event wiring.
 *
 * Mapping:
 *   onclick="document.getElementById('generateModal').classList.remove('hidden'); ..."
 *                                                → data-action="open-generate-modal".
 *   onclick="document.getElementById('generateModal').classList.add('hidden')"
 *                                                → data-action="close-generate-modal".
 *   onclick="event.stopPropagation()"            → data-stop-propagation.
 *   onchange="this.form.submit()" (per_page × 2) → data-action="filter-submit".
 *   onchange="toggleAllStatuses(this)"           → data-action="toggle-all-statuses".
 *   onchange="toggleAllSpheres(this)"            → data-action="toggle-all-spheres".
 *   onchange="toggleAllRegions(this)"            → data-action="toggle-all-regions".
 *   onsubmit="return confirm('...')" (× 5)       → data-confirm="<msg>".
 *
 * Zero behavior change.
 */

(function () {
  "use strict";

  // Delegated click handler.
  document.addEventListener(
    "click",
    function (event) {
      // data-stop-propagation: stop event там где нужно (modal body).
      var stopNode = event.target.closest("[data-stop-propagation]");
      if (stopNode) {
        event.stopPropagation();
      }

      var trigger = event.target.closest("[data-action]");
      if (!trigger) return;
      var action = trigger.getAttribute("data-action");

      if (action === "open-generate-modal") {
        var modal = document.getElementById("generateModal");
        if (modal) modal.classList.remove("hidden");
        if (typeof window.initMailRegionDropdown === "function") {
          window.initMailRegionDropdown();
        }
      } else if (action === "close-generate-modal") {
        var m = document.getElementById("generateModal");
        if (m) m.classList.add("hidden");
      }
    },
    // Use capture: false (default) — stopPropagation works correctly в
    // bubble phase т.к. listener attaches к document (последний в chain).
    false
  );

  // Delegated change handler.
  document.addEventListener("change", function (event) {
    var trigger = event.target.closest("[data-action]");
    if (!trigger) return;
    var action = trigger.getAttribute("data-action");

    if (action === "filter-submit") {
      var form = trigger.form || trigger.closest("form");
      if (form && typeof form.submit === "function") form.submit();
    } else if (
      action === "toggle-all-statuses" &&
      typeof window.toggleAllStatuses === "function"
    ) {
      window.toggleAllStatuses(trigger);
    } else if (
      action === "toggle-all-spheres" &&
      typeof window.toggleAllSpheres === "function"
    ) {
      window.toggleAllSpheres(trigger);
    } else if (
      action === "toggle-all-regions" &&
      typeof window.toggleAllRegions === "function"
    ) {
      window.toggleAllRegions(trigger);
    }
  });

  // Delegated submit handler для [data-confirm].
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
