/* UX-1 (2026-04-23): Company timeline filter pills + load-more wiring.
 *
 * Поведение:
 *   - Нажатие на .company-timeline-filter-btn показывает только items
 *     с соответствующим data-kind; остальные скрываются через CSS class.
 *   - "Все" сбрасывает фильтр — видимы все items.
 *   - При нажатии "Показать ещё" fetched HTML partial загружается в OL
 *     и применяется текущий активный фильтр к новым items.
 *
 * CSP strict compliant — никакой inline JS, data-action + delegated listeners.
 * Функционирует в classical mode; modern mode имеет свой существующий
 * load-more wiring (не трогаем — совместим через общий класс).
 */

(function () {
  "use strict";

  var HIDDEN_CLASS = "company-timeline-hidden";

  /** Массив kinds текущего активного фильтра, или null если "all". */
  function parseFilter(btn) {
    var raw = btn.getAttribute("data-filter") || "all";
    if (raw === "all") return null;
    return raw
      .split(",")
      .map(function (s) {
        return s.trim();
      })
      .filter(Boolean);
  }

  /** Применить фильтр к списку <li data-kind=...>. */
  function applyFilter(listEl, allowedKinds) {
    var items = listEl.querySelectorAll(".company-timeline-entry");
    items.forEach(function (li) {
      var kind = li.getAttribute("data-kind") || "";
      var shouldShow = allowedKinds === null || allowedKinds.indexOf(kind) !== -1;
      li.classList.toggle(HIDDEN_CLASS, !shouldShow);
    });
  }

  /** Перерендерить визуальное состояние pills (active highlight). */
  function activatePill(container, activeBtn) {
    container.querySelectorAll(".company-timeline-filter-btn").forEach(function (b) {
      b.classList.toggle("active", b === activeBtn);
    });
  }

  /** Найти <ol> список timeline для pills container. */
  function findListFor(filtersContainer) {
    var selector = filtersContainer.getAttribute("data-target");
    if (selector) {
      return document.querySelector(selector);
    }
    // Fallback: искать следующий <ol> внутри общего wrapper
    var wrapper = filtersContainer.closest(
      "[data-company-id], .modern-tab-panel, details"
    );
    return wrapper ? wrapper.querySelector("ol") : null;
  }

  /** Сохранённый активный фильтр на listEl (для повторного применения
   *  после load-more). */
  function getActiveKinds(listEl) {
    var stored = listEl.getAttribute("data-active-filter-kinds");
    if (!stored || stored === "all") return null;
    return stored.split(",").filter(Boolean);
  }
  function setActiveKinds(listEl, kinds) {
    listEl.setAttribute(
      "data-active-filter-kinds",
      kinds === null ? "all" : kinds.join(",")
    );
  }

  // Delegated click — filter pills + load-more.
  document.addEventListener("click", function (event) {
    // Filter pill
    var btn = event.target.closest(".company-timeline-filter-btn");
    if (btn) {
      var container = btn.closest(".company-timeline-filters");
      if (!container) return;
      var listEl = findListFor(container);
      if (!listEl) return;

      var kinds = parseFilter(btn);
      applyFilter(listEl, kinds);
      setActiveKinds(listEl, kinds);
      activatePill(container, btn);
      return;
    }

    // Load-more button (classical): data-target specifies list; if absent,
    // skip (existing modern load-more button имеет свой wiring).
    var loadBtn = event.target.closest("#companyTimelineClassicLoadMore");
    if (loadBtn) {
      event.preventDefault();
      var cid = loadBtn.getAttribute("data-company-id");
      var listSel = loadBtn.getAttribute("data-target") || "#companyTimelineClassicList";
      var list = document.querySelector(listSel);
      if (!list || !cid) return;
      var offset = parseInt(list.getAttribute("data-timeline-offset") || "0", 10);
      loadBtn.disabled = true;
      loadBtn.textContent = "Загрузка…";

      fetch("/companies/" + cid + "/timeline/items/?offset=" + offset + "&limit=50", {
        headers: { "X-Requested-With": "XMLHttpRequest" },
        credentials: "same-origin",
      })
        .then(function (r) {
          if (!r.ok) throw new Error("HTTP " + r.status);
          return r.text();
        })
        .then(function (html) {
          list.insertAdjacentHTML("beforeend", html);
          // Re-apply active filter к newly added items
          var activeKinds = getActiveKinds(list);
          applyFilter(list, activeKinds);
          // Update offset
          var newCount = list.querySelectorAll(".company-timeline-entry").length;
          list.setAttribute("data-timeline-offset", String(newCount));
          var total = parseInt(list.getAttribute("data-timeline-total") || "0", 10);
          if (newCount >= total) {
            loadBtn.style.display = "none";
          } else {
            loadBtn.disabled = false;
            loadBtn.textContent = "Показать ещё (" + (total - newCount) + ")";
          }
        })
        .catch(function () {
          loadBtn.disabled = false;
          loadBtn.textContent = "Ошибка — повторить";
        });
    }
  });
})();
