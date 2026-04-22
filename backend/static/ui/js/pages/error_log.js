/* Extracted from backend/templates/ui/settings/error_log.html — W2.3 Phase 2c.
 *
 * 5 inline handlers заменены на data-action + delegated listeners:
 *   onclick="showErrorDetails('<id>')"  → data-action="show-error-details" data-error-id="<id>".
 *   onclick="showResolveModal('<id>')"  → data-action="show-resolve-modal" data-error-id="<id>".
 *   onclick="closeErrorDetails()"       → data-action="close-error-details".
 *   onclick="closeResolveModal()"       → data-action="close-resolve-modal".
 *
 * Zero behavior change.
 */

(function () {
  "use strict";

  var currentErrorId = null;

  function escapeHtml(text) {
    var div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  function showErrorDetails(errorId) {
    currentErrorId = errorId;
    var modal = document.getElementById("errorDetailsModal");
    var content = document.getElementById("errorDetailsContent");
    content.innerHTML =
      '<div class="text-center py-8"><div class="animate-spin w-8 h-8 border-4 border-brand-teal border-t-transparent rounded-full mx-auto"></div><div class="mt-4 text-brand-dark/70">Загрузка...</div></div>';
    modal.classList.remove("hidden");

    fetch("/admin/error-log/" + errorId + "/details/")
      .then(function (response) {
        return response.json();
      })
      .then(function (data) {
        var levelBadgeClass =
          data.level === "critical"
            ? "badge-danger"
            : data.level === "error"
              ? "badge-warn"
              : data.level === "warning"
                ? "badge-progress"
                : "badge";

        var html =
          '<div class="space-y-4">' +
          '<div><div class="text-sm text-brand-dark/70 mb-1">Когда произошло</div><div class="font-mono text-sm">' +
          data.created_at +
          "</div></div>" +
          '<div><div class="text-sm text-brand-dark/70 mb-1">Уровень</div><div><span class="badge ' +
          levelBadgeClass +
          '">' +
          data.level_display +
          "</span></div></div>" +
          '<div><div class="text-sm text-brand-dark/70 mb-1">Тип исключения</div><div class="font-mono text-sm">' +
          (data.exception_type || "—") +
          "</div></div>" +
          '<div><div class="text-sm text-brand-dark/70 mb-1">Сообщение</div><div class="bg-brand-soft/20 p-3 rounded-lg font-mono text-xs whitespace-pre-wrap">' +
          escapeHtml(data.message || "—") +
          "</div></div>" +
          '<div><div class="text-sm text-brand-dark/70 mb-1">Трассировка</div><div class="bg-brand-soft/20 p-3 rounded-lg font-mono text-xs whitespace-pre-wrap overflow-x-auto">' +
          escapeHtml(data.traceback || "—") +
          "</div></div>" +
          '<div><div class="text-sm text-brand-dark/70 mb-1">Запрос</div><div class="bg-brand-soft/20 p-3 rounded-lg font-mono text-xs">' +
          data.method +
          " " +
          data.path +
          "</div></div>" +
          '<div><div class="text-sm text-brand-dark/70 mb-1">Пользователь</div><div>' +
          (data.user || "—") +
          "</div></div>" +
          '<div><div class="text-sm text-brand-dark/70 mb-1">IP адрес</div><div class="font-mono text-xs">' +
          (data.ip_address || "—") +
          "</div></div>" +
          '<div><div class="text-sm text-brand-dark/70 mb-1">User-Agent</div><div class="font-mono text-xs text-brand-dark/70">' +
          (data.user_agent || "—") +
          "</div></div>" +
          (data.request_data
            ? '<div><div class="text-sm text-brand-dark/70 mb-1">Данные запроса</div><div class="bg-brand-soft/20 p-3 rounded-lg font-mono text-xs whitespace-pre-wrap overflow-x-auto">' +
              escapeHtml(JSON.stringify(data.request_data, null, 2)) +
              "</div></div>"
            : "") +
          (data.notes
            ? '<div><div class="text-sm text-brand-dark/70 mb-1">Заметки</div><div class="bg-brand-soft/20 p-3 rounded-lg">' +
              escapeHtml(data.notes) +
              "</div></div>"
            : "") +
          "</div>";
        content.innerHTML = html;
      })
      .catch(function (error) {
        content.innerHTML =
          '<div class="text-red-600">Ошибка загрузки: ' +
          error.message +
          "</div>";
      });
  }

  function closeErrorDetails() {
    document.getElementById("errorDetailsModal").classList.add("hidden");
    currentErrorId = null;
  }

  function showResolveModal(errorId) {
    currentErrorId = errorId;
    var form = document.getElementById("resolveForm");
    form.action = "/admin/error-log/" + errorId + "/resolve/";
    document.getElementById("resolveModal").classList.remove("hidden");
  }

  function closeResolveModal() {
    document.getElementById("resolveModal").classList.add("hidden");
    document.getElementById("resolveForm").reset();
    currentErrorId = null;
  }

  // Delegated click listener.
  document.addEventListener("click", function (event) {
    var trigger = event.target.closest("[data-action]");
    if (!trigger) return;
    var action = trigger.getAttribute("data-action");
    var errorId = trigger.getAttribute("data-error-id");

    if (action === "show-error-details" && errorId) {
      showErrorDetails(errorId);
    } else if (action === "show-resolve-modal" && errorId) {
      showResolveModal(errorId);
    } else if (action === "close-error-details") {
      closeErrorDetails();
    } else if (action === "close-resolve-modal") {
      closeResolveModal();
    }
  });

  // Закрытие модалок по клику на backdrop — передают this = the modal element.
  var detailsModal = document.getElementById("errorDetailsModal");
  if (detailsModal) {
    detailsModal.addEventListener("click", function (e) {
      if (e.target === this) closeErrorDetails();
    });
  }
  var resolveModal = document.getElementById("resolveModal");
  if (resolveModal) {
    resolveModal.addEventListener("click", function (e) {
      if (e.target === this) closeResolveModal();
    });
  }
})();
