/* UX-2 (2026-04-23): Global search Ctrl+K modal.
 *
 * Ctrl+K (Cmd+K on Mac) anywhere — открывает search modal.
 * Fanout fetch к /api/search/global/?q= — cross-entity results
 * (companies / contacts / tasks).
 *
 * Keyboard:
 *   Ctrl+K / Cmd+K — open.
 *   Escape          — close.
 *   ↑ / ↓           — navigate results.
 *   Enter           — activate selected.
 *
 * Local Ctrl+K handlers (messenger dialog search, company detail search)
 * precede глобальному: они used preventDefault + stopPropagation,
 * event до document listener не доходит → их scope preserved.
 *
 * CSP strict compliant — external module, no inline handlers.
 */

(function () {
  "use strict";

  var MIN_QUERY = 2;
  var DEBOUNCE_MS = 220;

  var modalEl = null;
  var inputEl = null;
  var resultsEl = null;
  var statusEl = null;
  var debounceTimer = null;
  var selectedIdx = -1;

  function buildModal() {
    var root = document.createElement("div");
    root.id = "globalSearchModal";
    root.className = "global-search-modal";
    root.setAttribute("role", "dialog");
    root.setAttribute("aria-modal", "true");
    root.setAttribute("aria-label", "Глобальный поиск");
    root.hidden = true;
    root.innerHTML =
      '<div class="global-search-backdrop" data-action="global-search-close"></div>' +
      '<div class="global-search-box">' +
      '<div class="global-search-input-row">' +
      '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="opacity:0.5">' +
      '<circle cx="11" cy="11" r="8"/>' +
      '<path d="M21 21l-4.35-4.35"/>' +
      "</svg>" +
      '<input type="search" class="global-search-input" placeholder="Искать по компаниям, контактам, задачам…" autocomplete="off" spellcheck="false" />' +
      '<kbd class="global-search-esc">Esc</kbd>' +
      "</div>" +
      '<div class="global-search-results" aria-live="polite"></div>' +
      '<div class="global-search-footer">' +
      '<kbd>↑↓</kbd> навигация &nbsp; <kbd>Enter</kbd> открыть &nbsp; <kbd>Esc</kbd> закрыть' +
      "</div>" +
      "</div>";
    document.body.appendChild(root);

    modalEl = root;
    inputEl = root.querySelector(".global-search-input");
    resultsEl = root.querySelector(".global-search-results");

    inputEl.addEventListener("input", onInput);
  }

  function openModal() {
    if (!modalEl) buildModal();
    modalEl.hidden = false;
    setTimeout(function () {
      if (inputEl) {
        inputEl.value = "";
        inputEl.focus();
        resultsEl.innerHTML = renderHint("Введите минимум " + MIN_QUERY + " символа");
        selectedIdx = -1;
      }
    }, 10);
  }

  function closeModal() {
    if (!modalEl) return;
    modalEl.hidden = true;
    if (inputEl) inputEl.value = "";
    if (resultsEl) resultsEl.innerHTML = "";
    selectedIdx = -1;
  }

  function isOpen() {
    return modalEl && !modalEl.hidden;
  }

  function onInput(event) {
    var q = (event.target.value || "").trim();
    clearTimeout(debounceTimer);
    if (q.length < MIN_QUERY) {
      resultsEl.innerHTML = renderHint("Введите минимум " + MIN_QUERY + " символа");
      selectedIdx = -1;
      return;
    }
    debounceTimer = setTimeout(function () {
      fetchResults(q);
    }, DEBOUNCE_MS);
  }

  function fetchResults(query) {
    resultsEl.innerHTML = renderHint("Поиск…");
    fetch("/api/search/global/?q=" + encodeURIComponent(query), {
      credentials: "same-origin",
      headers: { "X-Requested-With": "XMLHttpRequest" },
    })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(renderResults)
      .catch(function () {
        resultsEl.innerHTML = renderHint("Ошибка поиска. Попробуйте снова.", "error");
      });
  }

  function renderResults(data) {
    var sections = [];
    if (data.companies && data.companies.length) {
      sections.push(renderSection("Компании", data.companies));
    }
    if (data.contacts && data.contacts.length) {
      sections.push(renderSection("Контакты", data.contacts));
    }
    if (data.tasks && data.tasks.length) {
      sections.push(renderSection("Задачи", data.tasks));
    }
    if (sections.length === 0) {
      resultsEl.innerHTML = renderHint("Ничего не найдено");
      selectedIdx = -1;
      return;
    }
    resultsEl.innerHTML = sections.join("");
    selectedIdx = -1;
  }

  function renderSection(title, items) {
    var rows = items
      .map(function (item) {
        return (
          '<a class="global-search-item" role="option" href="' +
          escapeAttr(item.url) +
          '">' +
          '<div class="global-search-item__name">' +
          escapeHtml(item.name || "—") +
          "</div>" +
          (item.subtitle
            ? '<div class="global-search-item__sub">' +
              escapeHtml(item.subtitle) +
              "</div>"
            : "") +
          "</a>"
        );
      })
      .join("");
    return (
      '<div class="global-search-section">' +
      '<div class="global-search-section__title">' +
      escapeHtml(title) +
      "</div>" +
      rows +
      "</div>"
    );
  }

  function renderHint(text, kind) {
    var cls = kind === "error" ? "global-search-hint--error" : "";
    return '<div class="global-search-hint ' + cls + '">' + escapeHtml(text) + "</div>";
  }

  function escapeHtml(s) {
    var d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  function escapeAttr(s) {
    return escapeHtml(s).replace(/"/g, "&quot;");
  }

  function moveSelection(delta) {
    var items = resultsEl.querySelectorAll(".global-search-item");
    if (!items.length) return;
    if (selectedIdx >= 0 && items[selectedIdx]) {
      items[selectedIdx].classList.remove("global-search-item--active");
    }
    selectedIdx = (selectedIdx + delta + items.length) % items.length;
    items[selectedIdx].classList.add("global-search-item--active");
    items[selectedIdx].scrollIntoView({ block: "nearest" });
  }

  function activateSelection() {
    var items = resultsEl.querySelectorAll(".global-search-item");
    if (selectedIdx >= 0 && items[selectedIdx]) {
      window.location.href = items[selectedIdx].getAttribute("href");
    }
  }

  // Global keyboard handler.
  document.addEventListener(
    "keydown",
    function (event) {
      // Open на Ctrl+K / Cmd+K.
      var isMod = event.ctrlKey || event.metaKey;
      if (isMod && (event.key === "k" || event.key === "K")) {
        // Skip если local scoped handler already captured (they preventDefault +
        // stopPropagation before document-level listener); browser fires document
        // listener только если event propagated. Но defaultPrevented still true —
        // check и не открываем в этом случае.
        if (event.defaultPrevented) return;
        event.preventDefault();
        openModal();
        return;
      }

      if (!isOpen()) return;

      if (event.key === "Escape") {
        event.preventDefault();
        closeModal();
      } else if (event.key === "ArrowDown") {
        event.preventDefault();
        moveSelection(1);
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        moveSelection(-1);
      } else if (event.key === "Enter" && selectedIdx >= 0) {
        event.preventDefault();
        activateSelection();
      }
    },
    // Capture: false (bubble phase). Scoped handlers run first с stopPropagation
    // свой scope, не доходя до нас.
    false
  );

  // Backdrop click closes (data-action delegation).
  document.addEventListener("click", function (event) {
    var closeTrigger = event.target.closest('[data-action="global-search-close"]');
    if (closeTrigger) {
      event.preventDefault();
      closeModal();
    }
  });
})();
