/**
 * Messenger Operator Panel - Unified Chatwoot-style interface
 * Управление трёхколоночной панелью мессенджера
 */

const OPERATOR_EMOJI_LIST = ['😀','😃','😄','😁','😅','😂','🤣','😊','😇','🙂','😉','😌','😍','🥰','😘','😗','😙','😚','😋','😛','😜','🤪','😝','🤑','🤗','🤭','🤫','🤔','😐','😑','😶','😏','😣','😥','😮','🤐','😯','😪','😫','😴','🤤','😷','🤒','🤕','🤢','🤮','😎','🤓','🧐','😕','😟','🙁','😮','😯','😲','😳','🥺','😢','😭','😤','😠','😡','👍','👎','👌','✌️','🤞','🤟','🤘','🤙','👋','🤚','🖐️','✋','🖖','👏','🙌','👐','🤲','🙏','❤️','🧡','💛','💚','💙','💜','🖤','🤍','🤎','💔','❣️','💕','💞','💓','💗','💖','💘','💝','💟'];

function operatorEmojiToCodepoint(emoji) {
  var parts = [];
  for (var i = 0; i < emoji.length; i++) {
    var code = emoji.codePointAt(i);
    if (code > 0xFFFF) i++;
    parts.push(code.toString(16).toLowerCase());
  }
  return parts.join('-');
}
var OPERATOR_EMOJI_APPLE_CDN = 'https://cdn.jsdelivr.net/npm/emoji-datasource-apple@15.1.0/img/apple/64/';

class MessengerOperatorPanel {
  constructor() {
    this.currentConversationId = null;
    this.pollingIntervals = {};
    this.lastMessageTimestamp = null;
    this.selectedConversationId = null;
    this.composeMode = 'OUT'; // OUT | INTERNAL
    this.listPollingTimer = null;
    this.lastMessageIds = new Set(); // ID уже отображённых сообщений
    this.lastRenderedDate = null; // Последняя дата, для которой был сепаратор
    this.typingPollTimer = null;
    this.lastOperatorTypingSentAt = 0;
    this.pendingNewMessagesCount = 0;
    this.notificationsEnabled = false;
    this.earliestMessageTimestamp = null;
    this.earliestMessageId = null;
    this.loadingOlderMessages = false;
    this.hasMoreOlderMessages = true;
    this.initialMessagesLimit = 50;
    this.eventSource = null; // SSE соединение
    this.sseReconnectAttempts = 0;
    this.sseReconnectDelayMs = 1000;
    this.maxSseReconnectAttempts = 5;
    this._sseTimeoutId = null; // Таймер автозакрытия SSE
    this._activeAbortControllers = new Set(); // Для отмены fetch при смене диалога
    this._appendLock = false; // Защита от race condition при append
    this._toastContainer = null; // Контейнер для toast-уведомлений
    this._cannedResponses = []; // Кэш шаблонных ответов
    this._cannedDropdownVisible = false;
    this._cannedFilterText = '';
    this._dragCounter = 0; // Для корректного отслеживания drag-and-drop
    this._bulkSelected = new Set(); // Выделенные диалоги для bulk-действий
    this._bulkMode = false;
    this._globalEventSource = null; // Глобальный SSE для уведомлений
    this._globalSSETimeoutId = null;
    this._globalSSEReconnectAttempts = 0;
    this._soundEnabled = localStorage.getItem('messenger_sound') !== 'off'; // Звук вкл по умолчанию
    this._resolveModalConversationId = null; // id диалога, для которого открыта resolve-модалка
    this._pendingResolve = null; // {id, outcome, comment, timerId, toast}
    this._resolveModalInitialized = false; // защита от дублей слушателей
    this._transferModalInitialized = false; // Plan 2 Task 8
    this._pendingTransferId = null;
    this._pendingTransferOriginBranch = null;
  }

  /** Fetch с таймаутом и AbortController */
  _fetch(url, options = {}, timeoutMs = 30000) {
    const controller = new AbortController();
    this._activeAbortControllers.add(controller);
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
    const mergedOptions = {
      ...options,
      credentials: 'same-origin',
      signal: controller.signal,
      headers: {
        'Accept': 'application/json',
        ...(options.headers || {}),
      },
    };
    return fetch(url, mergedOptions).finally(() => {
      clearTimeout(timeoutId);
      this._activeAbortControllers.delete(controller);
    });
  }

  /** Отменить все активные запросы (при смене диалога) */
  _abortAllFetches() {
    this._activeAbortControllers.forEach(c => c.abort());
    this._activeAbortControllers.clear();
  }

  // ===== Plan 2 Task 9 — Автосохранение черновиков сообщений =====

  /** Префикс ключей черновиков в localStorage */
  _draftKeyPrefix() { return 'messenger:draft:v1:'; }

  /** Построить ключ для конкретного диалога и режима */
  _draftKey(conversationId, composeMode) {
    return this._draftKeyPrefix() + conversationId + ':' + composeMode;
  }

  /** Сохранить черновик; если пусто — удалить */
  saveDraft(conversationId, composeMode, text) {
    if (!conversationId || !composeMode) return;
    try {
      const key = this._draftKey(conversationId, composeMode);
      const clean = (text || '').trim();
      if (!clean) {
        localStorage.removeItem(key);
        return;
      }
      const payload = JSON.stringify({ text: clean, savedAt: Date.now() });
      localStorage.setItem(key, payload);
      // Контроль ёмкости — не более 50 черновиков
      this._enforceDraftLimit(50);
    } catch (e) {
      // private mode / quota exceeded — игнорируем
    }
  }

  /** Загрузить черновик */
  loadDraft(conversationId, composeMode) {
    if (!conversationId || !composeMode) return null;
    try {
      const raw = localStorage.getItem(this._draftKey(conversationId, composeMode));
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed.text === 'string') return parsed;
      return null;
    } catch (e) {
      return null;
    }
  }

  /** Очистить черновик */
  clearDraft(conversationId, composeMode) {
    if (!conversationId || !composeMode) return;
    try {
      localStorage.removeItem(this._draftKey(conversationId, composeMode));
    } catch (e) {}
  }

  /** Удалить устаревшие черновики (TTL 7 дней) */
  pruneOldDrafts() {
    try {
      const prefix = this._draftKeyPrefix();
      const ttlMs = 7 * 24 * 60 * 60 * 1000;
      const now = Date.now();
      const toRemove = [];
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (!k || !k.startsWith(prefix)) continue;
        try {
          const parsed = JSON.parse(localStorage.getItem(k) || 'null');
          if (!parsed || typeof parsed.savedAt !== 'number' || (now - parsed.savedAt) > ttlMs) {
            toRemove.push(k);
          }
        } catch (e) {
          toRemove.push(k);
        }
      }
      toRemove.forEach(k => localStorage.removeItem(k));
    } catch (e) {}
  }

  /** Ограничить количество черновиков — удалить самые старые */
  _enforceDraftLimit(maxCount) {
    try {
      const prefix = this._draftKeyPrefix();
      const entries = [];
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (!k || !k.startsWith(prefix)) continue;
        try {
          const parsed = JSON.parse(localStorage.getItem(k) || 'null');
          const savedAt = (parsed && typeof parsed.savedAt === 'number') ? parsed.savedAt : 0;
          entries.push({ key: k, savedAt });
        } catch (e) {
          entries.push({ key: k, savedAt: 0 });
        }
      }
      if (entries.length <= maxCount) return;
      entries.sort((a, b) => a.savedAt - b.savedAt);
      const excess = entries.length - maxCount;
      for (let i = 0; i < excess; i++) {
        localStorage.removeItem(entries[i].key);
      }
    } catch (e) {}
  }

  /** Применить черновик к полю ввода; показать уведомление */
  applyDraftToInput(conversationId, composeMode) {
    const messageBody = document.getElementById('messageBody');
    if (!messageBody) return;
    const draft = this.loadDraft(conversationId, composeMode);
    if (draft && draft.text) {
      messageBody.textContent = draft.text;
      this.updateOperatorInputHeight(messageBody);
      try { this.showNotification('Восстановлен черновик', 'info'); } catch (e) {}
    } else {
      // При переключении режима — очистить поле, если для нового режима черновика нет
      messageBody.innerHTML = '';
      this.updateOperatorInputHeight(messageBody);
    }
  }

  /** Plan 2 Task 10 — визуальный стиль compose mode (обёртка, плашка, кнопка Send) */
  applyComposeModeStyle(mode) {
    const wrapper = document.getElementById('messageInputWrapper');
    const hint = document.getElementById('internalNoteHint');
    const sendBtn = document.getElementById('messageSendBtn');
    if (!wrapper || !hint) return;
    // Plan 2 Task 11 — быстрые ответы только для OUT-режима
    const quickRow = document.getElementById('quickRepliesRow');
    if (quickRow) {
      // Ре-рендер (HTML диалога пересоздаётся при открытии — кнопок может не быть)
      if (!quickRow.childElementCount && this._quickReplies && this._quickReplies.length) {
        this.renderQuickReplies();
      }
      const hasItems = !!(this._quickReplies && this._quickReplies.length);
      quickRow.classList.toggle('hidden', mode === 'INTERNAL' || !hasItems);
    }
    if (mode === 'INTERNAL') {
      wrapper.classList.add('bg-yellow-50', 'ring-2', 'ring-yellow-400/50');
      hint.classList.remove('hidden');
      if (sendBtn) {
        sendBtn.classList.add('messenger-operator-send-btn-internal');
        sendBtn.setAttribute('title', 'Сохранить заметку (Ctrl+Enter)');
      }
    } else {
      wrapper.classList.remove('bg-yellow-50', 'ring-2', 'ring-yellow-400/50');
      hint.classList.add('hidden');
      if (sendBtn) {
        sendBtn.classList.remove('messenger-operator-send-btn-internal');
        sendBtn.setAttribute('title', 'Отправить (Ctrl+Enter)');
      }
    }
  }

  /** Debounced сохранение черновика (300мс) */
  _scheduleDraftSave(conversationId, composeMode, text) {
    this._draftDebounceTimers = this._draftDebounceTimers || {};
    const key = conversationId + ':' + composeMode;
    clearTimeout(this._draftDebounceTimers[key]);
    this._draftDebounceTimers[key] = setTimeout(() => {
      this.saveDraft(conversationId, composeMode, text);
    }, 300);
  }

  init() {
    this.initSidebarControls();
    this.startListPolling();
    this.initKeyboardShortcuts();
    this.initNotifications();
    this.initOverlayHandlers();
    this.initCannedResponsesTrigger();
    this.initDragDrop();
    this.loadCannedResponses();
    this.loadQuickReplies();
    this.initBulkActions();
    this.startGlobalNotificationStream();
    this.initResolveModal();
    this.initTransferModal();
    this.pruneOldDrafts();

    // Plan 3 Task 6: сброс title-badge при возврате на вкладку
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) {
        this._pendingUnread = 0;
        this.updateTitleBadge(0);
      }
    });
    // Plan 3 Task 6: запрос разрешения Notification при первом клике (user gesture)
    document.addEventListener('click', () => this.requestNotificationPermission(), { once: true });

    // Обработка URL hash для открытия диалога
    const hash = window.location.hash;
    if (hash && hash.startsWith('#conversation/')) {
      const conversationId = parseInt(hash.replace('#conversation/', ''));
      if (conversationId) {
        this.openConversation(conversationId);
      }
    } else {
      const ctx = window.MESSENGER_CONTEXT || {};
      if (ctx.selectedConversationId) {
        this.openConversation(parseInt(ctx.selectedConversationId));
      }
    }
  }

  initSidebarControls() {
    const searchInput = document.getElementById('conversationSearchInput');
    const statusSelect = document.getElementById('conversationStatusSelect');
    const mineInput = document.getElementById('mineInput');
    const mineBtn = document.getElementById('mineToggleBtn');
    const resetBtn = document.getElementById('resetFiltersBtn');

    let searchTimer = null;
    if (searchInput) {
      searchInput.addEventListener('input', () => {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(() => {
          this.refreshConversationList();
        }, 300);
      });
      searchInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          clearTimeout(searchTimer);
          this.refreshConversationList();
        }
      });
    }
    if (statusSelect) statusSelect.addEventListener('change', () => this.refreshConversationList());

    if (mineBtn && mineInput) {
      mineBtn.addEventListener('click', () => {
        const nowMine = !mineInput.value;
        mineInput.value = nowMine ? '1' : '';
        mineBtn.classList.toggle('btn-primary', nowMine);
        this.refreshConversationList();
      });
    }
    if (resetBtn) {
      resetBtn.addEventListener('click', () => {
        if (searchInput) searchInput.value = '';
        if (statusSelect) statusSelect.value = '';
        if (mineInput) mineInput.value = '';
        if (mineBtn) mineBtn.classList.remove('btn-primary');
        this.refreshConversationList();
      });
    }
  }

  /**
   * Инициализация keyboard shortcuts
   */
  initKeyboardShortcuts() {
    document.addEventListener('keydown', (e) => {
      // Ctrl+K или Cmd+K: фокус на поиск
      if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault();
        const searchInput = document.getElementById('conversationSearchInput');
        if (searchInput) {
          searchInput.focus();
          searchInput.select();
        }
      }
      
      // Esc: закрыть диалог (очистить hash)
      if (e.key === 'Escape' && this.currentConversationId) {
        const prevConversationId = this.currentConversationId;
        const contentArea = document.getElementById('conversationContent');
        const infoArea = document.getElementById('conversationInfo');
        if (contentArea) {
          contentArea.innerHTML = `
            <div class="flex items-center justify-center h-full text-brand-dark/40">
              <div class="text-center">
                <svg class="w-16 h-16 mx-auto mb-4 text-brand-dark/20" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"/>
                </svg>
                <h3 class="text-lg font-semibold mb-2 text-brand-dark/70">Выберите диалог</h3>
                <p class="text-sm mb-3">Выберите диалог из списка слева, чтобы начать общение</p>
                <p class="text-xs text-brand-dark/50">Нажмите <kbd class="px-1.5 py-0.5 bg-brand-soft/40 rounded text-xs">Ctrl+K</kbd> для быстрого поиска</p>
              </div>
            </div>
          `;
        }
        if (infoArea) {
          infoArea.innerHTML = `
            <div class="flex items-center justify-center h-full text-brand-dark/40">
              <div class="text-center">
                <p class="text-sm mb-1">Информация о диалоге</p>
                <p class="text-xs">Выберите диалог для просмотра</p>
              </div>
            </div>
          `;
        }
        this.currentConversationId = null;
        this.stopPolling(prevConversationId);
        this.stopTypingPolling();
        window.history.replaceState(null, '', window.location.pathname + window.location.search);

        const layout = document.querySelector('.messenger-unified-layout');
        if (layout) layout.classList.remove('chat-open', 'info-open');
        
        // Убрать активное состояние с карточек
        document.querySelectorAll('.conversation-card.active').forEach(card => {
          card.classList.remove('active');
        });
      }
    });
  }

  initOverlayHandlers() {
    const overlay = document.getElementById('messengerOverlay');
    if (overlay) {
      overlay.addEventListener('click', () => this.closeInfoPanel());
    }
  }

  openInfoPanel() {
    const layout = document.querySelector('.messenger-unified-layout');
    if (layout) layout.classList.add('info-open');
  }

  closeInfoPanel() {
    const layout = document.querySelector('.messenger-unified-layout');
    if (layout) layout.classList.remove('info-open');
  }

  toggleInfoPanel() {
    const layout = document.querySelector('.messenger-unified-layout');
    if (!layout) return;
    layout.classList.toggle('info-open');
  }

  hideSidebarOnMobile() {
    const layout = document.querySelector('.messenger-unified-layout');
    if (layout) layout.classList.add('chat-open');
  }

  showSidebarOnMobile() {
    const layout = document.querySelector('.messenger-unified-layout');
    if (layout) layout.classList.remove('chat-open');
  }

  /**
   * Инициализация браузерных уведомлений
   */
  initNotifications() {
    if (typeof window === 'undefined' || typeof Notification === 'undefined') {
      this.notificationsEnabled = false;
      return;
    }
    if (Notification.permission === 'granted') {
      this.notificationsEnabled = true;
    } else if (Notification.permission === 'default') {
      Notification.requestPermission().then((result) => {
        this.notificationsEnabled = result === 'granted';
      }).catch(() => {
        this.notificationsEnabled = false;
      });
    } else {
      this.notificationsEnabled = false;
    }

    // Sound toggle button
    this._updateSoundToggleUI();
    const soundBtn = document.getElementById('soundToggleBtn');
    if (soundBtn) {
      soundBtn.addEventListener('click', () => {
        this._soundEnabled = !this._soundEnabled;
        localStorage.setItem('messenger_sound', this._soundEnabled ? 'on' : 'off');
        this._updateSoundToggleUI();
        if (this._soundEnabled) this.playIncomingSoundV2();
      });
    }

    // Browser Push — register Service Worker + subscribe
    this._registerPushSubscription();
  }

  async _registerPushSubscription() {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) return;

    try {
      const reg = await navigator.serviceWorker.register('/sw-push.js', { scope: '/' });

      // Получить VAPID public key с сервера
      const resp = await fetch('/api/push/vapid-key/', { credentials: 'same-origin' });
      if (!resp.ok) return;
      const { public_key } = await resp.json();
      if (!public_key) return;

      // Проверить/создать подписку
      let subscription = await reg.pushManager.getSubscription();
      if (!subscription) {
        const applicationServerKey = this._urlBase64ToUint8Array(public_key);
        subscription = await reg.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: applicationServerKey,
        });
      }

      // Отправить подписку на сервер
      const subJson = subscription.toJSON();
      const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]')?.value
        || document.cookie.match(/csrftoken=([^;]+)/)?.[1] || '';

      await fetch('/api/push/subscribe/', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrfToken,
        },
        body: JSON.stringify({
          endpoint: subJson.endpoint,
          p256dh: subJson.keys.p256dh,
          auth: subJson.keys.auth,
        }),
      });
    } catch (err) {
      // Push registration не критична — продолжаем без неё
      console.warn('Push subscription failed:', err);
    }
  }

  _urlBase64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - base64String.length % 4) % 4);
    const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    const rawData = atob(base64);
    const outputArray = new Uint8Array(rawData.length);
    for (let i = 0; i < rawData.length; i++) {
      outputArray[i] = rawData.charCodeAt(i);
    }
    return outputArray;
  }

  _updateSoundToggleUI() {
    const onIcon = document.getElementById('soundOnIcon');
    const offIcon = document.getElementById('soundOffIcon');
    if (onIcon) onIcon.style.display = this._soundEnabled ? '' : 'none';
    if (offIcon) offIcon.style.display = this._soundEnabled ? 'none' : '';
  }

  /**
   * Глобальный SSE стрим уведомлений (аналог Chatwoot account-wide ActionCable).
   * Получает ВСЕ новые входящие сообщения по всем видимым диалогам.
   * Играет звук и показывает push/toast даже если оператор в другом диалоге.
   */
  startGlobalNotificationStream() {
    this._cleanupGlobalSSE();
    this._globalSSEReconnectAttempts = 0;
    this._connectGlobalSSE();
  }

  _connectGlobalSSE() {
    if (this._globalSSEReconnectAttempts > 10) {
      console.warn('Global notification SSE: max reconnects reached, falling back to polling');
      return;
    }

    try {
      const es = new EventSource('/api/conversations/notifications/stream/');
      this._globalEventSource = es;

      es.addEventListener('ready', () => {
        this._globalSSEReconnectAttempts = 0;
      });

      es.addEventListener('notification.message', (e) => {
        try {
          const data = JSON.parse(e.data || '{}');
          // Не уведомлять о сообщениях в текущем открытом диалоге (SSE per-conversation уже обработает)
          if (data.conversation_id === this.currentConversationId) return;

          // Звук
          this.playIncomingSound();

          // Обновить список диалогов (новое сообщение поднимет диалог наверх)
          this.refreshConversationList();

          // Toast уведомление внутри панели
          const name = data.contact_name || 'Посетитель';
          const preview = data.preview || 'Новое сообщение';
          this._showNotificationToast(name, preview, data.conversation_id);

          // Browser Notification (если вкладка в фоне)
          if (document.hidden && this.notificationsEnabled && typeof Notification !== 'undefined' && Notification.permission === 'granted') {
            const n = new Notification(`${name}`, {
              body: preview,
              tag: `messenger-global-${data.message_id}`,
              icon: '/static/messenger/icon-chat.png',
              requireInteraction: false,
            });
            n.onclick = () => {
              window.focus();
              this.openConversation(data.conversation_id);
              n.close();
            };
            setTimeout(() => n.close(), 8000);
          }
        } catch (err) {
          console.warn('Global SSE notification.message error:', err);
        }
      });

      es.addEventListener('notification.assignment', (e) => {
        try {
          const data = JSON.parse(e.data || '{}');
          this.playIncomingSound();
          this.refreshConversationList();
          const name = data.contact_name || 'Новый диалог';
          this._showNotificationToast('Назначен диалог', name, data.conversation_id);

          if (this.notificationsEnabled && typeof Notification !== 'undefined' && Notification.permission === 'granted') {
            const n = new Notification('Назначен новый диалог', {
              body: name,
              tag: `messenger-assign-${data.conversation_id}`,
              requireInteraction: true,
            });
            n.onclick = () => {
              window.focus();
              this.openConversation(data.conversation_id);
              n.close();
            };
            setTimeout(() => n.close(), 15000);
          }
        } catch (err) {
          console.warn('Global SSE notification.assignment error:', err);
        }
      });

      es.onerror = () => {
        this._cleanupGlobalSSE();
        this._globalSSEReconnectAttempts++;
        const delay = Math.min(2000 * Math.pow(1.5, this._globalSSEReconnectAttempts - 1), 30000);
        setTimeout(() => this._connectGlobalSSE(), delay);
      };

      // Reconnect через 50сек (сервер закрывает через 55сек)
      this._globalSSETimeoutId = setTimeout(() => {
        if (this._globalEventSource === es) {
          this._cleanupGlobalSSE();
          this._connectGlobalSSE();
        }
      }, 50000);

    } catch (err) {
      console.error('Global notification SSE start error:', err);
    }
  }

  _cleanupGlobalSSE() {
    if (this._globalSSETimeoutId) {
      clearTimeout(this._globalSSETimeoutId);
      this._globalSSETimeoutId = null;
    }
    if (this._globalEventSource) {
      try { this._globalEventSource.close(); } catch (_) {}
      this._globalEventSource = null;
    }
  }

  /**
   * Toast-уведомление внутри панели оператора (не browser notification).
   * Появляется в углу, исчезает через 6 секунд, кликабельно.
   */
  _showNotificationToast(title, body, conversationId) {
    if (!this._toastContainer) {
      this._toastContainer = document.createElement('div');
      this._toastContainer.id = 'messenger-toast-container';
      this._toastContainer.style.cssText = 'position:fixed;top:16px;right:16px;z-index:10000;display:flex;flex-direction:column;gap:8px;max-width:360px;';
      document.body.appendChild(this._toastContainer);
      // Inject animation keyframes
      if (!document.getElementById('messenger-toast-keyframes')) {
        const style = document.createElement('style');
        style.id = 'messenger-toast-keyframes';
        style.textContent = '@keyframes slideInRight{from{transform:translateX(100%);opacity:0}to{transform:translateX(0);opacity:1}}';
        document.head.appendChild(style);
      }
    }

    const toast = document.createElement('div');
    toast.style.cssText = 'background:#1e293b;color:#f1f5f9;padding:12px 16px;border-radius:10px;box-shadow:0 8px 24px rgba(0,0,0,.25);cursor:pointer;display:flex;align-items:flex-start;gap:10px;animation:slideInRight .3s ease;border-left:4px solid #01948E;min-width:280px;';
    toast.innerHTML = `
      <div style="flex:1;min-width:0">
        <div style="font-weight:600;font-size:13px;margin-bottom:2px">${this.escapeHtml(title)}</div>
        <div style="font-size:12px;color:#94a3b8;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${this.escapeHtml(body)}</div>
      </div>
      <div style="font-size:11px;color:#64748b;white-space:nowrap">сейчас</div>
    `;
    toast.addEventListener('click', () => {
      if (conversationId) this.openConversation(conversationId);
      toast.remove();
    });

    this._toastContainer.prepend(toast);

    // Автоудаление через 6 секунд
    setTimeout(() => {
      toast.style.transition = 'opacity .3s, transform .3s';
      toast.style.opacity = '0';
      toast.style.transform = 'translateX(100%)';
      setTimeout(() => toast.remove(), 300);
    }, 6000);

    // Максимум 5 toast'ов
    while (this._toastContainer.children.length > 5) {
      this._toastContainer.lastChild.remove();
    }
  }

  getListQueryParams() {
    const searchInput = document.getElementById('conversationSearchInput');
    const statusSelect = document.getElementById('conversationStatusSelect');
    const mineInput = document.getElementById('mineInput');
    const params = new URLSearchParams();
    if (searchInput && searchInput.value.trim()) params.set('q', searchInput.value.trim());
    if (statusSelect && statusSelect.value) params.set('status', statusSelect.value);
    if (mineInput && mineInput.value) params.set('mine', '1');
    // пагинация/лимит — пусть DRF отдает дефолт; если есть page_size — попробуем
    params.set('page_size', '50');
    return params;
  }

  async refreshConversationList() {
    const listEl = document.getElementById('conversationsList');
    if (!listEl) return;

    const params = this.getListQueryParams();
    try {
      // Skeleton-загрузка, если список пустой (как в Chatwoot)
      if (!listEl.querySelector('.conversation-card')) {
        listEl.innerHTML = this.renderSkeletonList();
      }

      const response = await fetch(`/api/conversations/?${params.toString()}`, {
        credentials: 'same-origin',
        headers: { 'Accept': 'application/json' },
      });
      if (!response.ok) {
        if (!listEl.querySelector('.conversation-card')) {
          listEl.innerHTML = `
            <div class="empty-state">
              <p>Не удалось загрузить диалоги</p>
              <p class="text-xs mt-1">Попробуйте обновить страницу или изменить фильтры</p>
            </div>
          `;
        }
        return;
      }
      const data = await response.json();
      const items = Array.isArray(data) ? data : (Array.isArray(data.results) ? data.results : []);
      
      if (items.length === 0) {
        listEl.innerHTML = `
          <div class="empty-state">
            <p>Диалоги не найдены</p>
            <p class="text-xs mt-1">Попробуйте изменить фильтры</p>
          </div>
        `;
        return;
      }
      
      // Умное обновление без мерцания: обновляем карточки in-place,
      // переставляем порядок DOM, удаляем пропавшие — без innerHTML = ''
      const existingCards = new Map();
      Array.from(listEl.querySelectorAll('.conversation-card')).forEach(card => {
        const id = card.getAttribute('data-conversation-id');
        if (id) existingCards.set(parseInt(id), card);
      });

      const newIds = new Set();
      let prevCard = null;
      items.forEach(conversation => {
        const id = conversation.id;
        newIds.add(id);
        let card = existingCards.get(id);
        if (card) {
          // Обновить данные на месте
          this.updateConversationCardInPlace(card, conversation);
        } else {
          // Новая карточка
          const tempDiv = document.createElement('div');
          tempDiv.innerHTML = this.renderConversationCardHtml(conversation);
          card = tempDiv.firstElementChild;
        }
        // Переставить в правильную позицию (без удаления/пересоздания)
        if (prevCard) {
          if (prevCard.nextElementSibling !== card) {
            prevCard.after(card);
          }
        } else {
          if (listEl.firstElementChild !== card) {
            listEl.prepend(card);
          }
        }
        prevCard = card;
      });

      // Удалить карточки, которых больше нет в ответе API
      existingCards.forEach((card, id) => {
        if (!newIds.has(id)) card.remove();
      });

      // Обновить счётчик непрочитанных в заголовке вкладки (Chatwoot-style)
      const totalUnread = items.reduce((sum, c) => sum + (Number(c.unread_count) || 0), 0);
      this._updateTabTitle(totalUnread);

    } catch (e) {
      console.error('refreshConversationList failed:', e);
    }
  }

  _updateTabTitle(unreadCount) {
    const base = 'Мессенджер';
    document.title = unreadCount > 0 ? `(${unreadCount}) ${base}` : base;
    // Также обновим favicon badge (если поддерживается)
    this._updateFaviconBadge(unreadCount);
  }

  _updateFaviconBadge(count) {
    // Рисуем красный кружок с числом на favicon (canvas-based)
    if (!this._originalFavicon) {
      const link = document.querySelector('link[rel="icon"]') || document.querySelector('link[rel="shortcut icon"]');
      if (link) this._originalFavicon = link.href;
    }
    const link = document.querySelector('link[rel="icon"]') || document.querySelector('link[rel="shortcut icon"]');
    if (!link) return;

    if (count <= 0) {
      if (this._originalFavicon) link.href = this._originalFavicon;
      return;
    }

    const canvas = document.createElement('canvas');
    canvas.width = 32;
    canvas.height = 32;
    const ctx = canvas.getContext('2d');

    // Базовый favicon (зелёный чат-иконка)
    ctx.fillStyle = '#01948E';
    ctx.beginPath();
    ctx.roundRect(0, 0, 32, 32, 6);
    ctx.fill();
    ctx.fillStyle = '#fff';
    ctx.font = 'bold 16px sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('💬', 16, 16);

    // Красный badge
    ctx.fillStyle = '#ef4444';
    ctx.beginPath();
    ctx.arc(24, 8, 10, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = '#fff';
    ctx.font = 'bold 11px sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(count > 99 ? '99+' : String(count), 24, 8);

    link.href = canvas.toDataURL('image/png');
  }

  /**
   * Обновить карточку диалога на месте (без перерендера всей карточки)
   */
  updateConversationCardInPlace(cardEl, conversation) {
    if (!cardEl || !conversation) return;
    
    const id = conversation.id;
    const status = conversation.status || '';
    let bg = '#94a3b8';
    if (status === 'open') bg = '#01948E';
    else if (status === 'pending') bg = '#FDAD3A';
    else if (status === 'resolved') bg = '#22c55e';
    
    // Обновить аватар
    const avatar = cardEl.querySelector('.conversation-avatar');
    if (avatar) {
      avatar.style.background = bg;
      const name = conversation.contact_name || conversation.contact_email || conversation.contact_phone || 'Без имени';
      avatar.textContent = (name[0] || 'К').toUpperCase();
    }
    
    // Обновить имя
    const nameEl = cardEl.querySelector('.conversation-name');
    if (nameEl) {
      const name = conversation.contact_name || conversation.contact_email || conversation.contact_phone || 'Без имени';
      nameEl.textContent = name;
    }
    
    // Обновить время
    const timeEl = cardEl.querySelector('.conversation-time');
    if (timeEl && conversation.last_activity_at) {
      const lastAt = new Date(conversation.last_activity_at);
      const now = new Date();
      const timeStr = lastAt.toDateString() === now.toDateString()
        ? lastAt.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
        : lastAt.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' });
      timeEl.textContent = timeStr;
    }
    
    // Обновить превью
    const previewEl = cardEl.querySelector('.conversation-preview');
    if (previewEl) {
      const lastBody = (conversation.last_message_body || '').trim();
      previewEl.textContent = lastBody ? this.escapeHtml(lastBody).slice(0, 140) : 'Нет сообщений';
    }
    
    // Обновить мета (бейдж непрочитанных и статус)
    const unread = Number(conversation.unread_count || 0);
    const metaEl = cardEl.querySelector('.conversation-meta');
    if (metaEl) {
      let statusBadge = '';
      if (status === 'open') statusBadge = '<span class="badge badge-new badge-xs">Открыт</span>';
      else if (status === 'pending') statusBadge = '<span class="badge badge-progress badge-xs">Ожидание</span>';
      else if (status === 'resolved') statusBadge = '<span class="badge badge-done badge-xs">Решён</span>';
      else if (status === 'closed') statusBadge = '<span class="badge badge-cancel badge-xs">Закрыт</span>';

      metaEl.innerHTML = (unread > 0 ? `<span class="conversation-badge">${unread}</span>` : '') + statusBadge;
    }

    // Обновить активное состояние и unread
    const isActive = this.currentConversationId === id;
    const isUnread = unread > 0 && !isActive;
    if (isActive) {
      cardEl.classList.add('active');
      cardEl.classList.remove('unread');
    } else {
      cardEl.classList.remove('active');
      if (isUnread) {
        cardEl.classList.add('unread');
      } else {
        cardEl.classList.remove('unread');
      }
    }
  }

  startListPolling() {
    if (this.listPollingTimer) return;
    // Обновление списка диалогов каждые 10 секунд (для обновления превью/времени)
    this.listPollingTimer = setInterval(() => {
      if (!document.hidden) {
        this.refreshConversationList();
      }
    }, 10000);
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) {
        if (this.listPollingTimer) { clearInterval(this.listPollingTimer); this.listPollingTimer = null; }
        // Закрыть SSE при скрытии вкладки (включая таймер автозакрытия)
        this._cleanupSSE();
      } else {
        this.startListPolling();
        this.refreshConversationList();
        // Переподключить SSE если диалог открыт
        if (this.currentConversationId) {
          this.startPolling(this.currentConversationId);
        }
      }
    });
  }

  renderConversationCardHtml(conversation) {
    const id = conversation.id;
    const status = conversation.status || '';
    let bg = '#94a3b8';
    if (status === 'open') bg = '#01948E';
    else if (status === 'pending') bg = '#FDAD3A';
    else if (status === 'resolved') bg = '#22c55e';
    const name = conversation.contact_name || conversation.contact_email || conversation.contact_phone || 'Без имени';
    const initial = (name[0] || 'К').toUpperCase();

    const lastBody = (conversation.last_message_body || '').trim();
    const preview = lastBody ? this.escapeHtml(lastBody).slice(0, 140) : 'Нет сообщений';

    const lastAt = conversation.last_activity_at ? new Date(conversation.last_activity_at) : null;
    const now = new Date();
    const timeStr = lastAt
      ? (lastAt.toDateString() === now.toDateString()
          ? lastAt.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
          : lastAt.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' }))
      : '';

    const unread = Number(conversation.unread_count || 0);
    const isActive = this.currentConversationId === id;
    const isUnread = unread > 0 && !isActive;

    let statusBadge = '';
    if (status === 'open') statusBadge = '<span class="badge badge-new badge-xs">Открыт</span>';
    else if (status === 'pending') statusBadge = '<span class="badge badge-progress badge-xs">Ожидание</span>';
    else if (status === 'waiting_offline') statusBadge = '<span class="inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded-full bg-amber-100 text-amber-800 border border-amber-300" title="Заявка вне рабочих часов"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 14"/></svg>Ждёт связи</span>';
    else if (status === 'resolved') statusBadge = '<span class="badge badge-done badge-xs">Решён</span>';
    else if (status === 'closed') statusBadge = '<span class="badge badge-cancel badge-xs">Закрыт</span>';

    // Бейдж «Позван старший» (Plan 2 Task 12)
    const needsHelpBadge = conversation.needs_help
      ? '<span class="inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded-full bg-red-100 text-red-700 border border-red-300 animate-pulse" title="Позван старший"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/></svg>SOS</span>'
      : '';

    // Plan 3 Task 8: бейдж минут ожидания (warn/urgent/rop)
    const waitingMin = Number(conversation.waiting_minutes || 0);
    const thresholds = { warn: 3, urgent: 10, rop: 20 };
    let waitingBadge = '';
    if (waitingMin >= thresholds.rop) {
      waitingBadge = `<span class="inline-flex items-center px-2 py-0.5 text-xs rounded-full bg-red-600 text-white animate-pulse" title="Ждёт ${waitingMin} мин">${waitingMin}м</span>`;
    } else if (waitingMin >= thresholds.urgent) {
      waitingBadge = `<span class="inline-flex items-center px-2 py-0.5 text-xs rounded-full bg-orange-500 text-white" title="Ждёт ${waitingMin} мин">${waitingMin}м</span>`;
    } else if (waitingMin >= thresholds.warn) {
      waitingBadge = `<span class="inline-flex items-center px-2 py-0.5 text-xs rounded-full bg-yellow-400 text-yellow-900" title="Ждёт ${waitingMin} мин">${waitingMin}м</span>`;
    }

    const isAdmin = window.MESSENGER_CONTEXT && window.MESSENGER_CONTEXT.isAdmin === true;
    const deleteBtn = isAdmin ? `
      <button type="button" 
              class="conversation-delete-btn" 
              onclick="event.stopPropagation(); window.MessengerPanel.deleteConversation(${id});"
              title="Удалить диалог"
              aria-label="Удалить диалог">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M3 6h18M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/>
        </svg>
      </button>
    ` : '';

    const bulkChecked = this._bulkSelected.has(id);
    const bulkCheckbox = this._bulkMode ? `
      <label class="conversation-bulk-checkbox" onclick="event.stopPropagation();">
        <input type="checkbox" ${bulkChecked ? 'checked' : ''} onchange="window.MessengerPanel.toggleBulkSelect(${id}, this.checked)">
      </label>` : '';

    return `
      <div class="conversation-card ${isActive ? 'active' : ''} ${isUnread ? 'unread' : ''} ${bulkChecked ? 'bulk-selected' : ''}" data-conversation-id="${id}" onclick="window.MessengerPanel.openConversation(${id})">
        ${bulkCheckbox}
        <div class="conversation-avatar" style="background: ${bg};">${this.escapeHtml(initial)}</div>
        <div class="conversation-content">
          <div class="conversation-header">
            <div class="conversation-name">${this.escapeHtml(name)}</div>
            <div class="conversation-time">${this.escapeHtml(timeStr)}</div>
          </div>
          <div class="conversation-preview">${preview}</div>
          <div class="conversation-meta">
            ${unread > 0 ? `<span class="conversation-badge">${unread}</span>` : ''}
            ${statusBadge}
            ${needsHelpBadge}
            ${waitingBadge}
          </div>
        </div>
        ${deleteBtn}
      </div>
    `;
  }

  /**
   * Открыть диалог (загрузить через AJAX)
   */
  async openConversation(conversationId, opts = {}) {
    const force = Boolean(opts && opts.force);
    if (!force && this.currentConversationId === conversationId) {
      return; // Уже открыт
    }
    // Plan 4 Task 4 — инвалидация кэша контекста при смене диалога
    this.clearConversationContextCache();

    const prevConversationId = this.currentConversationId;
    if (prevConversationId && prevConversationId !== conversationId) {
      this.stopPolling(prevConversationId);
      this.stopTypingPolling();
    }
    
    // Обработка visibility change для SSE
    if (!document.hidden && this.eventSource && this.eventSource.readyState === EventSource.CLOSED) {
      // Переподключиться если соединение закрыто
      this.startSSEStream(conversationId);
    }

    this.currentConversationId = conversationId;
    this.selectedConversationId = conversationId;
    this.pendingNewMessagesCount = 0;
    // Plan 3 Task 6: сброс title-badge при открытии диалога
    this._pendingUnread = 0;
    this.updateTitleBadge(0);
    
    // Обновить URL hash (без перезагрузки страницы)
    if (window.location.hash !== `#conversation/${conversationId}`) {
      window.history.replaceState(null, '', `#conversation/${conversationId}`);
    }
    
    // Обновить активную карточку в списке
    document.querySelectorAll('.conversation-card').forEach(card => {
      if (parseInt(card.dataset.conversationId) === conversationId) {
        card.classList.add('active');
      } else {
        card.classList.remove('active');
      }
    });

    // Показать индикатор загрузки
    const contentArea = document.getElementById('conversationContent');
    const infoArea = document.getElementById('conversationInfo');
    
    if (contentArea) {
      contentArea.innerHTML = `
        <div class="flex flex-col h-full">
          <div class="border-b border-brand-soft/60 p-3 bg-white flex items-center gap-3">
            <div class="w-8 h-8 rounded-full animate-pulse bg-brand-soft/40"></div>
            <div class="flex-1 space-y-1.5">
              <div class="h-3.5 animate-pulse bg-brand-soft/40 rounded w-32"></div>
              <div class="h-2.5 animate-pulse bg-brand-soft/40 rounded w-20"></div>
            </div>
          </div>
          <div class="flex-1 overflow-hidden">${this.renderSkeletonMessages()}</div>
        </div>
      `;
    }
    if (infoArea) {
      const sh = 'animate-pulse bg-brand-soft/40 rounded';
      infoArea.innerHTML = `
        <div class="space-y-3 p-4">
          <div class="bg-white rounded-lg border border-brand-soft/60 p-3 space-y-2">
            <div class="h-4 ${sh} w-16"></div>
            <div class="h-3.5 ${sh} w-32"></div>
            <div class="h-3 ${sh} w-40"></div>
            <div class="h-3 ${sh} w-28"></div>
          </div>
          <div class="bg-white rounded-lg border border-brand-soft/60 p-3 space-y-2">
            <div class="h-4 ${sh} w-14"></div>
            <div class="h-3 ${sh} w-full"></div>
            <div class="h-3 ${sh} w-3/4"></div>
            <div class="h-3 ${sh} w-full"></div>
          </div>
          <div class="bg-white rounded-lg border border-brand-soft/60 p-3 space-y-2">
            <div class="h-4 ${sh} w-20"></div>
            <div class="h-8 ${sh} w-full"></div>
            <div class="h-8 ${sh} w-full"></div>
          </div>
        </div>
      `;
    }

    try {
      // Отменить предыдущие запросы при быстрой смене диалога
      this._abortAllFetches();

      // Загрузить диалог и сообщения параллельно
      const [response, messagesResponse] = await Promise.all([
        this._fetch(`/api/conversations/${conversationId}/`),
        this._fetch(`/api/conversations/${conversationId}/messages/?limit=${this.initialMessagesLimit}`),
      ]);

      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      if (!messagesResponse.ok) throw new Error(`HTTP ${messagesResponse.status}`);

      const conversation = await response.json();
      const messages = await messagesResponse.json();
      
      // Рендерить диалог
      this.renderConversation(conversation, messages);
      this.renderConversationInfo(conversation);

      // На мобилке — спрятать список диалогов
      this.hideSidebarOnMobile();

      // Пометить прочитанным (если текущий пользователь — assignee)
      this.markConversationRead(conversationId).catch(() => {});
      
      // Сохранить ID и timestamp последних сообщений
      this.lastMessageIds.clear();
      this.earliestMessageTimestamp = null;
      this.earliestMessageId = null;
      this.hasMoreOlderMessages = true;
      this.loadingOlderMessages = false;
      if (messages.length > 0) {
        messages.forEach(m => this.lastMessageIds.add(m.id));
        this.lastMessageTimestamp = messages[messages.length - 1].created_at;
        this.lastRenderedDate = new Date(messages[messages.length - 1].created_at).toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' });
        this.earliestMessageTimestamp = messages[0].created_at;
        this.earliestMessageId = messages[0].id;
        if (messages.length < this.initialMessagesLimit) {
          this.hasMoreOlderMessages = false;
        }
      } else {
        this.hasMoreOlderMessages = false;
      }
      
      // Начать SSE стрим для real-time обновлений
      this.startPolling(conversationId);
      // Typing polling больше не нужен (включен в SSE)
      // this.startTypingPolling(conversationId);
      
    } catch (error) {
      // AbortError — нормальная ситуация при быстрой смене диалога, молча игнорируем
      if (error.name === 'AbortError') return;
      console.error('Failed to load conversation:', error);
      if (contentArea) {
        contentArea.innerHTML = `
          <div class="p-4 text-center text-red-600">
            <p>Ошибка загрузки диалога</p>
            <p class="text-sm mt-2">${error.message}</p>
          </div>
        `;
      }
    }
  }

  async markConversationRead(conversationId) {
    try {
      await fetch(`/api/conversations/${conversationId}/read/`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'X-CSRFToken': this.getCsrfToken(),
        },
      });
      // обновим список (сбросит бейдж непрочитанных, если был)
      this.refreshConversationList();
    } catch (err) {
      console.warn('markConversationRead failed:', err);
    }
  }

  /**
   * Рендерить диалог в центральной колонке
   */
  renderConversation(conversation, messages) {
    const contentArea = document.getElementById('conversationContent');
    if (!contentArea) return;

    // Заголовок диалога
    const contactName = conversation.contact_name || conversation.contact_email || conversation.contact_phone || 'Без имени';
    
    let html = `
      <div class="flex flex-col h-full overflow-hidden">
      <div class="border-b border-brand-soft/60 p-4 bg-white flex-shrink-0">
        <div class="flex items-center justify-between">
          <div>
            <div class="flex items-center gap-2">
              <button type="button" id="mobileBackBtn" class="md:hidden inline-flex items-center justify-center w-8 h-8 rounded-full border border-brand-soft/80 text-brand-dark/60 hover:text-brand-dark hover:border-brand-soft" title="К списку">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M15 18l-6-6 6-6"/>
                </svg>
              </button>
              <h3 class="text-lg font-semibold">${this.escapeHtml(contactName)}</h3>
            </div>
            <p class="text-xs text-brand-dark/60">Диалог #${conversation.id}</p>
            <p class="text-xs text-brand-dark/40 mt-1 hidden" id="contactTypingIndicator">Клиент печатает…</p>
          </div>
          <div class="flex items-center gap-2" id="headerActions">
            <button type="button" id="mobileInfoBtn" class="lg:hidden inline-flex items-center justify-center w-8 h-8 rounded-full border border-brand-soft/80 text-brand-dark/60 hover:text-brand-dark hover:border-brand-soft" title="Инфо">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                <circle cx="12" cy="12" r="9"/><path d="M12 10v6"/><path d="M12 7h.01"/>
              </svg>
            </button>
            ${conversation.status === 'open' ? '<span class="badge badge-new">Открыт</span>' : ''}
            ${conversation.status === 'pending' ? '<span class="badge badge-progress">В ожидании</span>' : ''}
            ${conversation.status === 'resolved' ? '<span class="badge badge-done">Решён</span>' : ''}
            ${conversation.status === 'closed' ? '<span class="badge badge-cancel">Закрыт</span>' : ''}
            ${conversation.needs_help ? '<span class="inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded-full bg-red-100 text-red-700 border border-red-300" title="Позван старший"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/></svg>Позван старший</span>' : ''}
            <div id="headerActionsSlot" class="flex items-center gap-2"></div>
          </div>
        </div>
      </div>
      
      <div class="flex-1 min-h-0 relative">
        <div class="absolute right-4 bottom-4 z-20">
          <button type="button" id="scrollToBottomBtn" class="hidden w-10 h-10 rounded-full bg-white border border-brand-soft/80 shadow flex items-center justify-center text-brand-dark/60 hover:text-brand-dark hover:border-brand-soft" title="Вниз">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M12 5v14"/><path d="M19 12l-7 7-7-7"/>
            </svg>
          </button>
        </div>
        <div class="h-full overflow-y-auto p-4" id="messagesList">
          <div class="sticky top-2 z-10 flex justify-center pointer-events-none">
            <div id="stickyDateBadge" class="hidden px-3 py-1 rounded-full bg-brand-soft/60 text-xs text-brand-dark/70 backdrop-blur"> </div>
          </div>
          <div id="messagesHistoryLoader" class="hidden text-center text-xs text-brand-dark/50 py-2">Загрузка истории…</div>
    `;

    // Сообщения
    if (messages.length === 0) {
      html += '<div class="text-center py-8 text-brand-dark/60"><p>Сообщений пока нет</p></div>';
    } else {
      let currentDate = null;
      messages.forEach(message => {
        const messageDate = new Date(message.created_at).toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' });
        if (currentDate !== messageDate) {
          currentDate = messageDate;
          html += `
            <div class="text-center my-4" data-date-separator="${messageDate}">
              <span class="inline-block px-3 py-1 rounded-full bg-brand-soft/40 text-xs text-brand-dark/70">
                ${messageDate}
              </span>
            </div>
          `;
        }

        html += this.renderMessageHtml(message);
      });
    }

    // Закрываем messagesList и контейнер ленты
    html += '</div></div>';

    // Форма отправки сообщения
    html += `
      <div class="border-t border-brand-soft/60 p-3 bg-white flex-shrink-0">
        ${window.MESSENGER_CAN_REPLY ? `<div class="flex items-center justify-between mb-2">
          <div class="inline-flex rounded-xl border border-brand-soft/80 bg-white overflow-hidden">
            <button type="button" id="composeModeOut" class="px-3 py-1.5 text-xs font-medium ${this.composeMode === 'OUT' ? 'bg-brand-teal text-white' : 'text-brand-dark/70 hover:bg-brand-soft/30'}">Ответить</button>
            <button type="button" id="composeModeInternal" class="px-3 py-1.5 text-xs font-medium ${this.composeMode === 'INTERNAL' ? 'bg-brand-orange text-brand-dark' : 'text-brand-dark/70 hover:bg-brand-soft/30'}">Заметка</button>
          </div>
          <div class="flex items-center gap-2">
            <button type="button" id="newMessagesBtn" class="hidden text-xs px-2 py-1 rounded-full bg-brand-teal text-white hover:bg-brand-teal/90">Новые сообщения</button>
            <div class="text-[10px] text-brand-dark/40">Ctrl+Enter</div>
          </div>
        </div>
        <form id="messageForm" class="messenger-operator-form" onsubmit="window.MessengerPanel.sendMessage(event)" enctype="multipart/form-data">
          <input type="hidden" name="conversation_id" value="${conversation.id}">
          <div id="operatorEmojiPicker" class="messenger-operator-emoji-picker messenger-operator-emoji-picker-hidden"></div>
          <div id="messageInputWrapper" class="rounded-lg transition-colors">
          <div id="internalNoteHint" class="hidden flex items-center gap-2 px-3 py-2 text-xs text-yellow-800 bg-yellow-100 border-l-4 border-yellow-500 rounded-t">
            <svg class="w-4 h-4 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"/></svg>
            <span>Внутренняя заметка — клиент её не увидит</span>
          </div>
          <div id="quickRepliesRow" class="hidden flex flex-wrap gap-2 px-3 pt-2" aria-label="Быстрые ответы"></div>
          <div class="messenger-operator-form-row">
            <input type="file" name="attachments" id="messageAttachments" class="hidden" multiple accept="image/*,.pdf">
            <button type="button" id="messageAttachBtn" class="messenger-operator-icon-btn" title="Прикрепить файл">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48"/></svg>
            </button>
            <div id="messageBody" class="messenger-operator-input messenger-operator-input-contenteditable" contenteditable="true" data-placeholder="Введите сообщение..." role="textbox" aria-multiline="true" style="min-height:40px;max-height:120px;"></div>
            <input type="hidden" name="body" id="messageBodyHidden">
            <button type="button" id="messageEmojiBtn" class="messenger-operator-icon-btn" title="Эмодзи"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><line x1="9" y1="9" x2="9.01" y2="9"/><line x1="15" y1="9" x2="15.01" y2="9"/></svg></button>
            <div class="relative">
              <button type="button" id="macroBtn" class="messenger-operator-icon-btn" title="Макросы">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
              </button>
              <div id="macroDropdown" class="hidden absolute bottom-full right-0 mb-1 w-56 bg-white rounded-lg shadow-lg border border-brand-soft/40 z-50 max-h-60 overflow-y-auto">
                <div id="macroList" class="py-1 text-sm"></div>
              </div>
            </div>
            <button type="submit" id="messageSendBtn" class="messenger-operator-send-btn" title="Отправить (Ctrl+Enter)">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2L11 13"/><path d="M22 2l-7 20-4-9-9-4 20-7z"/></svg>
            </button>
          </div>
          </div>
          <div id="messageAttachmentsNames" class="text-xs text-brand-dark/60 mt-1 px-1"></div>
          <div id="composeModeHint" class="text-[10px] text-brand-dark/40 mt-1 px-1">Сообщение увидит клиент. Внутренние заметки доступны только сотрудникам.</div>
          <p class="text-[10px] text-brand-dark/40 mt-1.5 px-1">Макс. 5 МБ на файл • изображения и PDF • <span class="font-medium">/</span> — шаблоны ответов</p>
        </form>` : `<div class="text-center py-3 text-xs text-brand-dark/50 border-t border-brand-soft/40">
          <svg class="w-4 h-4 inline-block mr-1 opacity-50" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"/></svg>
          Только менеджеры могут отвечать в чатах
        </div>`}
      </div>
    `;

    html += `</div>`;
    contentArea.innerHTML = html;

    // Отрисовать контекстную primary CTA и меню ⋯ в шапке (Plan 2 Task 6)
    this.renderHeaderActions(conversation);

    // Автоскролл к последнему сообщению (при открытии всегда в самый низ)
    this.scrollToBottom(true);

    // Сбросить кнопку "Новые сообщения"
    this.pendingNewMessagesCount = 0;
    this.updateNewMessagesButton();

    const fileInput = document.getElementById('messageAttachments');
    const attachBtn = document.getElementById('messageAttachBtn');
    if (attachBtn && fileInput) {
      attachBtn.addEventListener('click', () => fileInput.click());
    }
    if (fileInput) {
      fileInput.addEventListener('change', function() {
        const MAX_SIZE = 5 * 1024 * 1024; // 5 МБ
        const ALLOWED_TYPES = ['image/png','image/jpeg','image/gif','image/webp','application/pdf'];
        const rejected = [];
        const accepted = [];
        Array.from(this.files).forEach(f => {
          if (f.size > MAX_SIZE) rejected.push(`${f.name}: > 5 МБ`);
          else if (ALLOWED_TYPES.length && !ALLOWED_TYPES.some(t => f.type.startsWith(t.split('/')[0])) && !f.name.toLowerCase().endsWith('.pdf')) rejected.push(`${f.name}: неподдерживаемый тип`);
          else accepted.push(f.name);
        });
        const namesEl = document.getElementById('messageAttachmentsNames');
        if (namesEl) {
          if (rejected.length > 0) {
            namesEl.innerHTML = `<span class="text-red-500">${rejected.join('; ')}</span>` + (accepted.length ? ` • ${accepted.join(', ')}` : '');
          } else {
            namesEl.textContent = accepted.join(', ') || '';
          }
        }
        if (rejected.length > 0 && window.MessengerPanel) {
          window.MessengerPanel.showNotification('Некоторые файлы отклонены: ' + rejected[0], 'error');
        }
      });
    }

    this.initOperatorEmojiPicker(conversation.id);
    this.initMacros(conversation.id);

    // Ctrl+Enter для отправки
    const messageBody = document.getElementById('messageBody');
    if (messageBody) {
      messageBody.addEventListener('keydown', function(e) {
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
          e.preventDefault();
          const form = document.getElementById('messageForm');
          if (form) form.requestSubmit ? form.requestSubmit() : form.dispatchEvent(new Event('submit'));
        } else if (e.key === 'Enter' && !e.shiftKey && messageBody.contentEditable === 'true') {
          e.preventDefault();
          const form = document.getElementById('messageForm');
          if (form) form.requestSubmit ? form.requestSubmit() : form.dispatchEvent(new Event('submit'));
        }
      });
      messageBody.addEventListener('input', () => {
        this.updateOperatorInputHeight(messageBody);
        this.sendOperatorTypingPing(conversation.id);
        // Plan 2 Task 9 — автосохранение черновика (debounce 300мс)
        this._scheduleDraftSave(conversation.id, this.composeMode, messageBody.textContent || '');
        // @mention autocomplete для внутренних заметок
        if (this.composeMode === 'INTERNAL') {
          this._handleMentionInput(messageBody);
        } else {
          this._hideMentionDropdown();
        }
        // Счётчик символов
        const charCount = (messageBody.textContent || '').length;
        const MAX_CHARS = 2000;
        const hintEl = document.getElementById('composeModeHint');
        if (hintEl) {
          if (charCount > MAX_CHARS * 0.9) {
            hintEl.innerHTML = `<span class="${charCount > MAX_CHARS ? 'text-red-500 font-medium' : 'text-orange-500'}">${charCount}/${MAX_CHARS}</span>`;
          } else if (charCount > 0) {
            hintEl.textContent = this.composeMode === 'INTERNAL' ? 'Заметка видна только сотрудникам' : 'Сообщение увидит клиент';
          }
        }
      });
      messageBody.addEventListener('paste', (e) => {
        e.preventDefault();
        const text = (e.clipboardData || window.clipboardData).getData('text/plain');
        const selection = window.getSelection();
        if (selection.rangeCount > 0) {
          const range = selection.getRangeAt(0);
          range.deleteContents();
          range.insertNode(document.createTextNode(text));
          range.collapse(false);
          selection.removeAllRanges();
          selection.addRange(range);
        }
        this.updateOperatorInputHeight(messageBody);
      });
      
      // Инициализация авто-роста поля ввода (только для textarea)
      if (messageBody.tagName === 'TEXTAREA') {
        this.initMessageInputAutogrow(messageBody);
      }
    }

    // Кнопка "Новые сообщения"
    const newMessagesBtn = document.getElementById('newMessagesBtn');
    if (newMessagesBtn) {
      newMessagesBtn.addEventListener('click', () => {
        this.pendingNewMessagesCount = 0;
        this.updateNewMessagesButton();
        this.scrollToBottom(true);
      });
    }

    // Если пользователь доскроллил вниз — скрываем кнопку
    const messagesList = document.getElementById('messagesList');
    if (messagesList) {
      messagesList.addEventListener('scroll', () => {
        // Ленивая подгрузка истории при прокрутке вверх
        if (messagesList.scrollTop < 120) {
          this.loadOlderMessages(conversation.id);
        }
        if (this.isScrolledToBottom()) {
          this.pendingNewMessagesCount = 0;
          this.updateNewMessagesButton();
        }
        this.updateScrollToBottomButton();
        this.updateStickyDateBadge();
      }, { passive: true });
    }

    // Кнопка "вниз"
    const scrollBtn = document.getElementById('scrollToBottomBtn');
    if (scrollBtn) {
      scrollBtn.addEventListener('click', () => {
        this.pendingNewMessagesCount = 0;
        this.updateNewMessagesButton();
        this.scrollToBottom(true);
        this.updateScrollToBottomButton();
      });
    }

    // Инициализация UI-элементов в списке
    this.updateScrollToBottomButton();
    this.updateStickyDateBadge(true);

    // Обработчик кликов по карточкам вложений
    if (messagesList) {
      messagesList.addEventListener('click', (e) => {
        const card = e.target.closest('.messenger-attachment-card');
        if (!card) return;
        e.preventDefault();
        e.stopPropagation();
        const openUrl = card.getAttribute('data-open') || '';
        const downloadUrl = card.getAttribute('data-download') || openUrl;
        const isImage = card.getAttribute('data-is-image') === '1';
        const isPdf = card.getAttribute('data-is-pdf') === '1';
        const title = card.querySelector('.messenger-attachment-card__name')?.textContent?.trim() || 'Файл';
        if (isImage && openUrl && typeof window.openMessengerImgModal === 'function') {
          window.openMessengerImgModal(openUrl, downloadUrl, title);
        } else if ((isImage || isPdf) && openUrl) {
          window.open(openUrl, '_blank', 'noopener');
        } else if (downloadUrl) {
          const a = document.createElement('a');
          a.href = downloadUrl;
          a.setAttribute('download', '');
          a.target = '_blank';
          document.body.appendChild(a);
          a.click();
          a.remove();
        }
      });
      messagesList.addEventListener('keydown', (e) => {
        if (e.key !== 'Enter' && e.key !== ' ') return;
        const card = e.target.closest('.messenger-attachment-card');
        if (!card) return;
        e.preventDefault();
        card.click();
      });
    }

    // Переключение режима (Ответить/Заметка)
    const btnOut = document.getElementById('composeModeOut');
    const btnInternal = document.getElementById('composeModeInternal');
    const composeHint = document.getElementById('composeModeHint');
    const applyModeUI = () => {
      if (!btnOut || !btnInternal) return;
      if (this.composeMode === 'OUT') {
        btnOut.className = 'px-3 py-1.5 text-xs font-medium bg-brand-teal text-white';
        btnInternal.className = 'px-3 py-1.5 text-xs font-medium text-brand-dark/70 hover:bg-brand-soft/30';
        if (messageBody) {
          messageBody.placeholder = 'Введите сообщение клиенту...';
        }
        if (composeHint) {
          composeHint.textContent = 'Сообщение увидит клиент. Внутренние заметки доступны только сотрудникам.';
        }
      } else {
        btnOut.className = 'px-3 py-1.5 text-xs font-medium text-brand-dark/70 hover:bg-brand-soft/30';
        btnInternal.className = 'px-3 py-1.5 text-xs font-medium bg-brand-orange text-brand-dark';
        if (messageBody) {
          messageBody.placeholder = 'Внутренняя заметка — клиент её не увидит';
        }
        if (composeHint) {
          composeHint.textContent = 'Эта заметка видна только сотрудникам и не отправляется клиенту.';
        }
      }
      // Plan 2 Task 10 — визуальный аффорданс: подсветка обёртки, плашка, кнопка Send
      this.applyComposeModeStyle(this.composeMode);
    };
    const switchComposeMode = (nextMode) => {
      if (this.composeMode === nextMode) return;
      // Сохранить текущий черновик под старым режимом перед переключением
      const prevMode = this.composeMode;
      if (messageBody) {
        this.saveDraft(conversation.id, prevMode, messageBody.textContent || '');
      }
      this.composeMode = nextMode;
      applyModeUI();
      // Загрузить черновик для нового режима
      this.applyDraftToInput(conversation.id, nextMode);
    };
    if (btnOut) btnOut.addEventListener('click', () => switchComposeMode('OUT'));
    if (btnInternal) btnInternal.addEventListener('click', () => switchComposeMode('INTERNAL'));

    // Plan 2 Task 9 — восстановить черновик для текущего режима при открытии диалога
    this.applyDraftToInput(conversation.id, this.composeMode);
    // Plan 2 Task 10 — применить визуальный стиль compose mode при открытии
    applyModeUI();

    // Мобильные кнопки
    const backBtn = document.getElementById('mobileBackBtn');
    if (backBtn) backBtn.addEventListener('click', () => this.showSidebarOnMobile());

    const infoBtn = document.getElementById('mobileInfoBtn');
    if (infoBtn) infoBtn.addEventListener('click', () => this.toggleInfoPanel());
  }

  updateNewMessagesButton() {
    const btn = document.getElementById('newMessagesBtn');
    if (!btn) return;
    if (this.pendingNewMessagesCount > 0) {
      btn.classList.remove('hidden');
      btn.textContent = `Новые сообщения (${this.pendingNewMessagesCount})`;
    } else {
      btn.classList.add('hidden');
      btn.textContent = 'Новые сообщения';
    }
  }

  updateScrollToBottomButton() {
    const btn = document.getElementById('scrollToBottomBtn');
    if (!btn) return;
    const show = !this.isScrolledToBottom(140);
    if (show) btn.classList.remove('hidden');
    else btn.classList.add('hidden');
  }

  updateStickyDateBadge(forceShow = false) {
    const badge = document.getElementById('stickyDateBadge');
    const messagesList = document.getElementById('messagesList');
    if (!badge || !messagesList) return;

    const seps = Array.from(messagesList.querySelectorAll('[data-date-separator]'));
    if (seps.length === 0) {
      badge.classList.add('hidden');
      return;
    }

    // Ищем последний разделитель даты, который "выше" текущего скролла
    const top = messagesList.scrollTop;
    let current = seps[0].getAttribute('data-date-separator') || '';
    for (const sep of seps) {
      const label = sep.getAttribute('data-date-separator') || '';
      if (sep.offsetTop - 16 <= top) current = label;
      else break;
    }

    if (!current) {
      badge.classList.add('hidden');
      return;
    }

    badge.textContent = current;
    if (forceShow || !badge.classList.contains('hidden')) {
      badge.classList.remove('hidden');
    } else {
      // Появляется только после небольшого скролла
      if (top > 80) badge.classList.remove('hidden');
      else badge.classList.add('hidden');
    }
  }

  async loadOlderMessages(conversationId) {
    if (!conversationId) return;
    if (!this.hasMoreOlderMessages) return;
    if (this.loadingOlderMessages) return;
    if (!this.earliestMessageTimestamp) return;
    if (document.hidden) return;

    const messagesList = document.getElementById('messagesList');
    if (!messagesList) return;

    const loader = document.getElementById('messagesHistoryLoader');
    if (loader) loader.classList.remove('hidden');

    this.loadingOlderMessages = true;
    try {
      const limit = 30;
      const beforeTs = encodeURIComponent(this.earliestMessageTimestamp);
      const beforeId = this.earliestMessageId ? `&before_id=${encodeURIComponent(this.earliestMessageId)}` : '';
      const url = `/api/conversations/${conversationId}/messages/?before=${beforeTs}${beforeId}&limit=${limit}`;
      const response = await fetch(url, {
        credentials: 'same-origin',
        headers: { 'Accept': 'application/json' },
      });
      if (!response.ok) return;
      const older = await response.json();
      const olderMessages = Array.isArray(older) ? older : (Array.isArray(older.results) ? older.results : []);

      if (olderMessages.length === 0) {
        this.hasMoreOlderMessages = false;
        return;
      }

      const prevScrollHeight = messagesList.scrollHeight;
      const prevScrollTop = messagesList.scrollTop;

      this.prependOldMessages(olderMessages);

      // Сохранить позицию вьюпорта (не прыгать)
      const newScrollHeight = messagesList.scrollHeight;
      messagesList.scrollTop = prevScrollTop + (newScrollHeight - prevScrollHeight);

      this.earliestMessageTimestamp = olderMessages[0].created_at;
      this.earliestMessageId = olderMessages[0].id;
      if (olderMessages.length < limit) {
        this.hasMoreOlderMessages = false;
      }
    } catch (e) {
      console.error('loadOlderMessages failed:', e);
    } finally {
      this.loadingOlderMessages = false;
      if (loader) loader.classList.add('hidden');
    }
  }

  prependOldMessages(messages) {
    const messagesList = document.getElementById('messagesList');
    if (!messagesList) return;
    if (!messages || messages.length === 0) return;

    // Дата первого разделителя (если он стоит самым верхним элементом)
    const firstEl = messagesList.firstElementChild;
    const topSeparatorDate = (firstEl && firstEl.getAttribute && firstEl.getAttribute('data-date-separator')) || null;

    const beforeFragment = document.createDocumentFragment();
    const sameTopDateFragment = document.createDocumentFragment();

    const ensureSeparator = (fragment, dateLabel) => {
      // для топовой даты разделитель уже есть в DOM
      if (topSeparatorDate && dateLabel === topSeparatorDate && fragment === sameTopDateFragment) return;

      // не добавляем второй разделитель подряд в одном фрагменте
      const last = fragment.lastChild;
      if (last && last.getAttribute && last.getAttribute('data-date-separator') === dateLabel) return;

      const sep = document.createElement('div');
      sep.className = 'text-center my-4';
      sep.setAttribute('data-date-separator', dateLabel);
      sep.innerHTML = `
        <span class="inline-block px-3 py-1 rounded-full bg-brand-soft/40 text-xs text-brand-dark/70">
          ${dateLabel}
        </span>
      `;
      fragment.appendChild(sep);
    };

    messages.forEach(message => {
      if (this.lastMessageIds.has(message.id)) return;

      const dateLabel = new Date(message.created_at).toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' });
      const target = (topSeparatorDate && dateLabel === topSeparatorDate) ? sameTopDateFragment : beforeFragment;
      ensureSeparator(target, dateLabel);

      const wrap = document.createElement('div');
      wrap.innerHTML = this.renderMessageHtml(message);
      const el = wrap.firstElementChild;
      target.appendChild(el);
      this.lastMessageIds.add(message.id);
    });

    if (beforeFragment.childNodes.length > 0) {
      messagesList.insertBefore(beforeFragment, messagesList.firstChild);
    }

    if (sameTopDateFragment.childNodes.length > 0 && topSeparatorDate) {
      // вставляем сообщения этой же даты сразу после разделителя, который уже вверху списка
      const topSep = messagesList.querySelector(`[data-date-separator="${topSeparatorDate}"]`);
      if (topSep && topSep.parentElement === messagesList) {
        messagesList.insertBefore(sameTopDateFragment, topSep.nextSibling);
      } else {
        messagesList.insertBefore(sameTopDateFragment, messagesList.firstChild);
      }
    }
  }

  /**
   * Рендерить информацию о диалоге в правой колонке
   */
  // Plan 4 Task 4 — загрузка контекста диалога (компания, история, аудит)
  async loadConversationContext(convId) {
    if (!convId) return null;
    if (this._contextCache && this._contextCache[convId]) {
      return this._contextCache[convId];
    }
    try {
      const resp = await fetch(`/api/conversations/${convId}/context/`, {
        credentials: 'same-origin',
        headers: { 'Accept': 'application/json' },
      });
      if (!resp.ok) return null;
      const data = await resp.json();
      this._contextCache = this._contextCache || {};
      this._contextCache[convId] = data;
      return data;
    } catch (e) {
      return null;
    }
  }

  clearConversationContextCache(convId) {
    if (!this._contextCache) return;
    if (convId) { delete this._contextCache[convId]; }
    else { this._contextCache = {}; }
  }

  _renderCompanyBlock(company) {
    const el = document.getElementById('panelCompanyBlock');
    if (!el) return;
    if (!company) {
      el.hidden = false;
      el.innerHTML = `
        <h3 class="text-sm font-semibold mb-2">Компания</h3>
        <p class="text-xs text-brand-dark/60">Не привязана</p>
      `;
      return;
    }
    el.hidden = false;
    const statusLine = company.status_name ? `<div class="text-xs text-brand-dark/60 mt-1">Статус: ${this.escapeHtml(company.status_name)}</div>` : '';
    const respLine = company.responsible_name ? `<div class="text-xs text-brand-dark/60">Ответственный: ${this.escapeHtml(company.responsible_name)}</div>` : '';
    const innLine = company.inn ? `<div class="text-xs text-brand-dark/60">ИНН: ${this.escapeHtml(company.inn)}</div>` : '';
    el.innerHTML = `
      <h3 class="text-sm font-semibold mb-2">Компания</h3>
      <div class="text-sm font-medium">${this.escapeHtml(company.name || '')}</div>
      ${statusLine}
      ${respLine}
      ${innLine}
      <a href="${this.escapeHtml(company.url || '#')}" target="_blank" rel="noopener" class="inline-block mt-2 text-xs text-brand-teal hover:underline">Открыть в CRM →</a>
    `;
  }

  _renderHistoryBlock(previous) {
    const el = document.getElementById('panelHistoryBlock');
    if (!el) return;
    if (!previous || !previous.length) {
      el.hidden = true;
      return;
    }
    el.hidden = false;
    const items = previous.slice(0, 10).map(p => {
      const date = p.created_at ? new Date(p.created_at).toLocaleDateString('ru-RU') : '';
      const status = this.escapeHtml(p.ui_status || p.status || '');
      return `<li><button type="button" class="text-left w-full px-2 py-1 hover:bg-brand-soft/30 rounded text-xs flex items-center gap-2" data-history-conv-id="${p.id}">
        <span class="text-brand-dark/60">${date}</span>
        <span class="inline-block px-1.5 rounded bg-brand-soft/50">${status}</span>
      </button></li>`;
    }).join('');
    el.innerHTML = `
      <h3 class="text-sm font-semibold mb-2">История обращений (${previous.length})</h3>
      <ul class="space-y-1">${items}</ul>
    `;
    el.querySelectorAll('[data-history-conv-id]').forEach(btn => {
      btn.addEventListener('click', () => {
        const id = btn.getAttribute('data-history-conv-id');
        if (id) this.openConversation(parseInt(id, 10));
      });
    });
  }

  _renderAuditBlock(audit) {
    const el = document.getElementById('panelAuditBlock');
    if (!el) return;
    if (!audit || !audit.length) {
      el.hidden = true;
      return;
    }
    el.hidden = false;
    const items = audit.slice(0, 10).map(a => {
      const date = a.created_at ? new Date(a.created_at).toLocaleString('ru-RU') : '';
      const kind = a.kind === 'transfer' ? 'Передача' : (a.kind === 'resolution' ? 'Резолюция' : '—');
      const from = a.from_user ? `от ${this.escapeHtml(a.from_user)}` : '';
      const to = a.to_user ? `к ${this.escapeHtml(a.to_user)}` : '';
      const text = this.escapeHtml(a.text || '');
      return `<li class="text-xs text-brand-dark/70">
        <div class="font-medium">${kind} <span class="text-brand-dark/50">· ${date}</span></div>
        ${(from || to) ? `<div class="text-brand-dark/50">${from} ${to}</div>` : ''}
        ${text ? `<div class="mt-0.5">${text}</div>` : ''}
      </li>`;
    }).join('');
    el.innerHTML = `
      <details class="group" open>
        <summary class="text-sm font-semibold cursor-pointer select-none">Аудит диалога (${audit.length})</summary>
        <ul class="space-y-2 mt-2">${items}</ul>
      </details>
    `;
  }

  renderConversationInfo(conversation) {
    const infoArea = document.getElementById('conversationInfo');
    if (!infoArea) return;

    const contactName = conversation.contact_name || conversation.contact_email || conversation.contact_phone || 'Без имени';
    const contactEmail = conversation.contact_email || '';
    const contactPhone = conversation.contact_phone || '';
    
    const ctx = window.MESSENGER_CONTEXT || {};
    const assignees = Array.isArray(ctx.assignees) ? ctx.assignees : [];
    const currentUserId = ctx.currentUserId;

    // SVG иконки
    const iconUser = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="4"/><path d="M4 20a8 8 0 0116 0"/></svg>';
    const iconMail = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4h16v16H4z"/><path d="M4 7l8 5 8-5"/></svg>';
    const iconPhone = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1.9.3 1.8.6 2.6a2 2 0 0 1-.5 2.1L8 9.9a16 16 0 0 0 6 6l1.5-1.2a2 2 0 0 1 2.1-.5c.8.3 1.7.5 2.6.6a2 2 0 0 1 1.8 2.1z"/></svg>';
    const iconBuilding = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 21h18"/><path d="M5 21V7l8-4v18"/><path d="M19 21V11l-6-4"/></svg>';
    const iconCalendar = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>';
    const iconUserPlus = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 11v6M19 14h6"/></svg>';
    const iconX = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6L6 18M6 6l12 12"/></svg>';

    let html = `
      <div class="space-y-3">
        <div class="lg:hidden flex items-center justify-between bg-white rounded-lg border border-brand-soft/60 p-3">
          <div class="text-sm font-semibold">Информация</div>
          <button type="button" id="closeInfoBtn" class="inline-flex items-center justify-center w-8 h-8 rounded-full border border-brand-soft/80 text-brand-dark/60 hover:text-brand-dark hover:border-brand-soft" title="Закрыть">
            ${iconX}
          </button>
        </div>
        <!-- Контакт -->
        <div class="bg-white rounded-lg border border-brand-soft/60 p-3">
          <div class="flex items-center gap-2 mb-2">
            <div class="text-brand-dark/60">${iconUser}</div>
            <h3 class="text-sm font-semibold">Контакт</h3>
          </div>
          <div class="space-y-1.5 text-sm">
            <div class="font-medium">${this.escapeHtml(contactName)}</div>
            ${contactEmail ? `
              <div class="flex items-center gap-1.5 text-xs text-brand-dark/60">
                <span class="text-brand-dark/40">${iconMail}</span>
                <a href="mailto:${this.escapeHtml(contactEmail)}" class="hover:text-brand-teal">${this.escapeHtml(contactEmail)}</a>
              </div>
            ` : ''}
            ${contactPhone ? `
              <div class="flex items-center gap-1.5 text-xs text-brand-dark/60">
                <span class="text-brand-dark/40">${iconPhone}</span>
                <a href="tel:${this.escapeHtml(contactPhone)}" class="hover:text-brand-teal">${this.escapeHtml(contactPhone)}</a>
              </div>
            ` : ''}
          </div>
        </div>

        <!-- Детали -->
        <div class="bg-white rounded-lg border border-brand-soft/60 p-3">
          <div class="flex items-center gap-2 mb-2">
            <div class="text-brand-dark/60">${iconBuilding}</div>
            <h3 class="text-sm font-semibold">Детали</h3>
          </div>
          <dl class="space-y-2 text-sm">
            <div class="flex items-center justify-between">
              <dt class="text-brand-dark/60 flex items-center gap-1.5">
                <span>${iconBuilding}</span>
                <span>Подразделение</span>
              </dt>
              <dd class="font-medium text-right">${this.escapeHtml(conversation.branch_name || '—')}</dd>
            </div>
            <div class="flex items-center justify-between">
              <dt class="text-brand-dark/60 flex items-center gap-1.5">
                <span>${iconCalendar}</span>
                <span>Создан</span>
              </dt>
              <dd class="font-medium text-right text-xs">${new Date(conversation.created_at).toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' })}</dd>
            </div>
            <div class="flex items-center justify-between">
              <dt class="text-brand-dark/60">Статус</dt>
              <dd>
                ${conversation.status === 'open' ? '<span class="badge badge-new badge-xs">Открыт</span>' : ''}
                ${conversation.status === 'pending' ? '<span class="badge badge-progress badge-xs">Ожидание</span>' : ''}
                ${conversation.status === 'resolved' ? '<span class="badge badge-done badge-xs">Решён</span>' : ''}
                ${conversation.status === 'closed' ? '<span class="badge badge-cancel badge-xs">Закрыт</span>' : ''}
              </dd>
            </div>
            <div class="flex items-center justify-between">
              <dt class="text-brand-dark/60">Приоритет</dt>
              <dd>
                ${conversation.priority === 10 ? '<span class="badge badge-xs">Низкий</span>' : ''}
                ${conversation.priority === 20 ? '<span class="badge badge-xs">Обычный</span>' : ''}
                ${conversation.priority === 30 ? '<span class="badge badge-warn badge-xs">Высокий</span>' : ''}
              </dd>
            </div>
          </dl>
        </div>
        
        <!-- Действия -->
        <div class="bg-white rounded-lg border border-brand-soft/60 p-3">
          <h3 class="text-sm font-semibold mb-3">Действия</h3>
          <div class="space-y-2.5">
            <div class="flex gap-2">
              ${window.MESSENGER_CAN_REPLY ? `<button type="button" class="btn btn-outline btn-sm flex-1 text-xs" id="assignMeBtn">
                <span class="inline-flex items-center gap-1.5">
                  ${iconUserPlus}
                  <span>Назначить меня</span>
                </span>
              </button>` : ''}
              <button type="button" class="btn btn-outline btn-sm text-xs" id="closeConvBtn" title="Закрыть диалог">
                ${iconX}
              </button>
            </div>
            <div>
              <label class="block text-xs text-brand-dark/70 mb-1">Статус</label>
              <select class="select text-sm w-full" id="convStatusSelect">
                <option value="open" ${conversation.status === 'open' ? 'selected' : ''}>Открыт</option>
                <option value="pending" ${conversation.status === 'pending' ? 'selected' : ''}>В ожидании</option>
                <option value="resolved" ${conversation.status === 'resolved' ? 'selected' : ''}>Решён</option>
                <option value="closed" ${conversation.status === 'closed' ? 'selected' : ''}>Закрыт</option>
              </select>
            </div>
            <div>
              <label class="block text-xs text-brand-dark/70 mb-1">Оператор</label>
              <select class="select text-sm w-full" id="convAssigneeSelect">
                <option value="">Не назначен</option>
                ${assignees.map(a => `<option value="${a.id}" ${conversation.assignee === a.id ? 'selected' : ''}>${this.escapeHtml(a.name)}</option>`).join('')}
              </select>
            </div>
            <div>
              <label class="block text-xs text-brand-dark/70 mb-1">Приоритет</label>
              <select class="select text-sm w-full" id="convPrioritySelect">
                <option value="10" ${conversation.priority === 10 ? 'selected' : ''}>Низкий</option>
                <option value="20" ${conversation.priority === 20 ? 'selected' : ''}>Обычный</option>
                <option value="30" ${conversation.priority === 30 ? 'selected' : ''}>Высокий</option>
              </select>
            </div>
          </div>
        </div>

        <!-- Метки (как в Chatwoot) -->
        <div class="bg-white rounded-lg border border-brand-soft/60 p-3">
          <div class="flex items-center justify-between mb-2">
            <h3 class="text-sm font-semibold">Метки</h3>
          </div>
          <div id="convLabelsContainer" class="flex flex-wrap gap-1.5">
            ${(conversation.label_names || []).map(l => `
              <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium text-white" style="background:${l.color}" data-label-id="${l.id}">
                ${this.escapeHtml(l.title)}
                <button type="button" class="hover:opacity-70 leading-none ml-0.5" data-remove-label="${l.id}" title="Убрать">&times;</button>
              </span>
            `).join('')}
            <button type="button" id="addLabelBtn" class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-brand-soft/30 text-[11px] text-brand-dark/60 hover:bg-brand-soft/50 transition">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 5v14M5 12h14"/></svg>
              Добавить
            </button>
          </div>
        </div>

        <!-- Plan 4 — контекстные блоки, заполняются асинхронно из /context/ API -->
        <div id="panelCompanyBlock" class="bg-white rounded-lg border border-brand-soft/60 p-3" hidden></div>
        <div id="panelHistoryBlock" class="bg-white rounded-lg border border-brand-soft/60 p-3" hidden></div>
        <div id="panelAuditBlock" class="bg-white rounded-lg border border-brand-soft/60 p-3" hidden></div>
      </div>
    `;

    infoArea.innerHTML = html;

    // Plan 4 Task 4 — асинхронно загружаем и рендерим контекст
    this.loadConversationContext(conversation.id).then(ctx => {
      if (!ctx) return;
      if (this.currentConversationId !== conversation.id) return; // переключились на другой
      this._renderCompanyBlock(ctx.company);
      this._renderHistoryBlock(ctx.previous_conversations || []);
      this._renderAuditBlock(ctx.audit_log || []);
    });

    const closeInfoBtn = document.getElementById('closeInfoBtn');
    if (closeInfoBtn) closeInfoBtn.addEventListener('click', () => this.closeInfoPanel());

    const statusSelect = document.getElementById('convStatusSelect');
    const assigneeSelect = document.getElementById('convAssigneeSelect');
    const prioritySelect = document.getElementById('convPrioritySelect');
    const assignMeBtn = document.getElementById('assignMeBtn');
    const closeBtn = document.getElementById('closeConvBtn');

    if (statusSelect) {
      statusSelect.addEventListener('change', () => {
        const prev = conversation.status;
        const next = statusSelect.value;
        this.patchConversation(conversation.id, { status: next }, () => {
          statusSelect.value = prev;
        });
      });
    }
    if (prioritySelect) {
      prioritySelect.addEventListener('change', () => {
        const prev = conversation.priority;
        const next = parseInt(prioritySelect.value);
        this.patchConversation(conversation.id, { priority: next }, () => {
          prioritySelect.value = String(prev);
        });
      });
    }
    if (assigneeSelect) {
      assigneeSelect.addEventListener('change', () => {
        const prev = conversation.assignee || null;
        const raw = assigneeSelect.value;
        const next = raw ? parseInt(raw) : null;
        this.patchConversation(conversation.id, { assignee: next }, () => {
          assigneeSelect.value = prev ? String(prev) : '';
        });
      });
    }
    if (assignMeBtn) {
      assignMeBtn.addEventListener('click', () => {
        if (!currentUserId) return;
        const prev = conversation.assignee || null;
        const next = currentUserId;
        this.patchConversation(conversation.id, { assignee: next }, () => {
          assigneeSelect.value = prev ? String(prev) : '';
        });
      });
    }
    if (closeBtn) {
      closeBtn.addEventListener('click', () => {
        const prev = conversation.status;
        this.patchConversation(conversation.id, { status: 'closed' }, () => {
          statusSelect.value = prev;
        });
      });
    }

    // Метки: удаление
    infoArea.querySelectorAll('[data-remove-label]').forEach(btn => {
      btn.addEventListener('click', () => {
        const labelId = parseInt(btn.getAttribute('data-remove-label'));
        const currentIds = (conversation.label_names || []).map(l => l.id).filter(id => id !== labelId);
        this.patchConversation(conversation.id, { label_ids: currentIds });
      });
    });

    // Метки: добавление
    const addLabelBtn = document.getElementById('addLabelBtn');
    if (addLabelBtn) {
      addLabelBtn.addEventListener('click', () => this.showAddLabelPopup(conversation));
    }
  }

  async showAddLabelPopup(conversation) {
    // Загружаем все доступные метки
    try {
      const response = await fetch('/api/conversation-labels/', {
        credentials: 'same-origin',
        headers: { 'Accept': 'application/json' },
      });
      if (!response.ok) return;
      const allLabels = await response.json();
      const currentIds = new Set((conversation.label_names || []).map(l => l.id));
      const available = (Array.isArray(allLabels) ? allLabels : allLabels.results || []).filter(l => !currentIds.has(l.id));

      const btn = document.getElementById('addLabelBtn');
      if (!btn) return;

      // Убираем предыдущий popup
      const existing = document.getElementById('addLabelPopup');
      if (existing) { existing.remove(); return; }

      if (available.length === 0) {
        this.showNotification('Все метки уже добавлены', 'info');
        return;
      }

      const popup = document.createElement('div');
      popup.id = 'addLabelPopup';
      popup.className = 'absolute z-50 bg-white border border-brand-soft/80 rounded-xl shadow-lg p-2 mt-1 w-48 max-h-40 overflow-y-auto';
      popup.innerHTML = available.map(l => `
        <button type="button" class="flex items-center gap-2 w-full px-2 py-1.5 rounded-lg text-xs hover:bg-brand-soft/30 transition" data-add-label="${l.id}">
          <span class="w-3 h-3 rounded-full flex-shrink-0" style="background:${l.color}"></span>
          <span>${this.escapeHtml(l.title)}</span>
        </button>
      `).join('');

      btn.parentElement.style.position = 'relative';
      btn.parentElement.appendChild(popup);

      // Закрыть popup при клике вне — один обработчик, удаляется при любом закрытии
      let closeHandler = null;
      const removePopup = () => {
        popup.remove();
        if (closeHandler) { document.removeEventListener('click', closeHandler); closeHandler = null; }
      };

      popup.querySelectorAll('[data-add-label]').forEach(item => {
        item.addEventListener('click', () => {
          const labelId = parseInt(item.getAttribute('data-add-label'));
          const newIds = [...currentIds, labelId];
          removePopup();
          this.patchConversation(conversation.id, { label_ids: newIds });
        });
      });

      setTimeout(() => {
        closeHandler = (e) => { if (!popup.contains(e.target) && e.target !== btn) removePopup(); };
        document.addEventListener('click', closeHandler);
      }, 10);
    } catch (e) {
      console.error('Failed to load labels:', e);
    }
  }

  async patchConversation(conversationId, payload, onError) {
    try {
      const response = await fetch(`/api/conversations/${conversationId}/`, {
        method: 'PATCH',
        credentials: 'same-origin',
        headers: {
          'Accept': 'application/json',
          'Content-Type': 'application/json',
          'X-CSRFToken': this.getCsrfToken(),
        },
        body: JSON.stringify(payload || {}),
      });
      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.detail || 'Ошибка сохранения');
      }
      const updated = await response.json();
      // Перерисовать правую колонку (и заголовок диалога) актуальными значениями
      await this.openConversation(conversationId, { force: true });
      this.updateConversationCard(updated);
    } catch (e) {
      console.error('patchConversation failed:', e);
      if (typeof onError === 'function') {
        try {
          onError();
        } catch {
          // ignore UI rollback errors
        }
      }
      alert('Не удалось сохранить: ' + (e.message || e));
    }
  }

  /**
   * Удалить диалог (только для администраторов)
   */
  async deleteConversation(conversationId) {
    if (!conversationId) return;
    
    // Проверка прав доступа
    const isAdmin = window.MESSENGER_CONTEXT && window.MESSENGER_CONTEXT.isAdmin === true;
    if (!isAdmin) {
      alert('У вас нет прав для удаления диалогов.');
      return;
    }

    // Подтверждение удаления
    if (!confirm('Вы уверены, что хотите удалить этот диалог? Это действие нельзя отменить.')) {
      return;
    }

    try {
      const response = await fetch(`/api/conversations/${conversationId}/`, {
        method: 'DELETE',
        credentials: 'same-origin',
        headers: {
          'Accept': 'application/json',
          'X-CSRFToken': this.getCsrfToken(),
        },
      });

      if (!response.ok) {
        const errData = await response.json().catch(() => ({}));
        alert(errData.detail || 'Не удалось удалить диалог.');
        return;
      }

      // Если удаляемый диалог был открыт - закрыть его
      if (this.currentConversationId === conversationId) {
        this.currentConversationId = null;
        this.selectedConversationId = null;
        this.stopPolling(conversationId);
        this.stopTypingPolling();
        
        // Очистить содержимое
        const contentArea = document.getElementById('conversationContent');
        const infoArea = document.getElementById('conversationInfo');
        if (contentArea) {
          contentArea.innerHTML = `
            <div class="flex items-center justify-center h-full text-brand-dark/40">
              <div class="text-center">
                <svg class="w-16 h-16 mx-auto mb-4 text-brand-dark/20" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"/>
                </svg>
                <h3 class="text-lg font-semibold mb-2 text-brand-dark/70">Выберите диалог</h3>
                <p class="text-sm mb-3">Выберите диалог из списка слева, чтобы начать общение</p>
              </div>
            </div>
          `;
        }
        if (infoArea) {
          infoArea.innerHTML = `
            <div class="flex items-center justify-center h-full text-brand-dark/40">
              <div class="text-center">
                <p class="text-sm mb-1">Информация о диалоге</p>
                <p class="text-xs">Выберите диалог для просмотра</p>
              </div>
            </div>
          `;
        }
        
        // Убрать hash из URL
        window.history.replaceState(null, '', window.location.pathname + window.location.search);
      }

      // Удалить карточку из списка
      const card = document.querySelector(`.conversation-card[data-conversation-id="${conversationId}"]`);
      if (card) {
        card.remove();
      }

      // Обновить список диалогов
      this.refreshConversationList();
    } catch (e) {
      console.error('deleteConversation failed:', e);
      this.showNotification('Ошибка при удалении диалога', 'error');
    }
  }

  updateConversationCard(conversation) {
    const card = document.querySelector(`.conversation-card[data-conversation-id="${conversation.id}"]`);
    if (!card) return;
    
    // Обновить цвет аватара по статусу
    const avatar = card.querySelector('.conversation-avatar');
    if (avatar) {
      let bg = '#94a3b8';
      if (conversation.status === 'open') bg = '#01948E';
      else if (conversation.status === 'pending') bg = '#FDAD3A';
      else if (conversation.status === 'resolved') bg = '#22c55e';
      avatar.style.background = bg;
    }
    
    // Обновить бейдж статуса
    const metaEl = card.querySelector('.conversation-meta');
    if (metaEl) {
      const existingBadge = metaEl.querySelector('.badge');
      if (existingBadge) {
        let statusBadge = '';
        if (conversation.status === 'open') statusBadge = '<span class="badge badge-new badge-xs">Открыт</span>';
        else if (conversation.status === 'pending') statusBadge = '<span class="badge badge-progress badge-xs">Ожидание</span>';
        else if (conversation.status === 'resolved') statusBadge = '<span class="badge badge-done badge-xs">Решён</span>';
        else if (conversation.status === 'closed') statusBadge = '<span class="badge badge-cancel badge-xs">Закрыт</span>';
        
        const unreadBadge = metaEl.querySelector('.conversation-badge');
        const unreadHtml = Number(conversation.unread_count || 0) > 0 
          ? `<span class="conversation-badge">${conversation.unread_count}</span>` 
          : '';
        metaEl.innerHTML = unreadHtml + statusBadge;
      }
    }
  }

  /**
   * Отправить сообщение через API
   */
  async sendMessage(event) {
    event.preventDefault();
    
    const form = event.target;
    const conversationId = form.querySelector('[name="conversation_id"]').value;
    const messageBodyEl = document.getElementById('messageBody');
    let body = '';
    if (messageBodyEl && messageBodyEl.contentEditable === 'true') {
      const clone = messageBodyEl.cloneNode(true);
      const emojiImgs = clone.querySelectorAll('img[data-emoji-char]');
      emojiImgs.forEach(img => {
        const emoji = img.getAttribute('data-emoji-char');
        const textNode = document.createTextNode(emoji);
        img.parentNode.replaceChild(textNode, img);
      });
      body = (clone.textContent || clone.innerText || '').trim();
    } else {
      body = form.querySelector('[name="body"]')?.value?.trim() || '';
    }
    const fileInput = form.querySelector('[name="attachments"]');
    const files = fileInput ? Array.from(fileInput.files) : [];
    
    if (!body && files.length === 0) return;

    // Клиентская валидация
    if (body.length > 2000) {
      this.showNotification('Сообщение слишком длинное (макс. 2000 символов)', 'error');
      return;
    }
    const MAX_FILE_SIZE = 5 * 1024 * 1024;
    const oversized = files.filter(f => f.size > MAX_FILE_SIZE);
    if (oversized.length > 0) {
      this.showNotification(`Файл «${oversized[0].name}» больше 5 МБ`, 'error');
      return;
    }

    const submitButton = form.querySelector('button[type="submit"]');
    if (submitButton) {
      submitButton.disabled = true;
      submitButton.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="animate-spin"><circle cx="12" cy="12" r="10" stroke-opacity="0.25"/><path d="M12 2a10 10 0 0 1 10 10" stroke-linecap="round"/></svg>';
    }

    try {
      const formData = new FormData();
      formData.append('body', body);
      formData.append('direction', this.composeMode === 'INTERNAL' ? 'internal' : 'out');
      
      files.forEach(file => {
        formData.append('attachments', file);
      });

      const response = await fetch(`/api/conversations/${conversationId}/messages/`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'X-CSRFToken': this.getCsrfToken(),
        },
        body: formData
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || 'Ошибка отправки сообщения');
      }

      const newMessage = await response.json();

      // Plan 2 Task 9 — очистить черновик после успешной отправки
      this.clearDraft(conversationId, this.composeMode);
      if (this._draftDebounceTimers) {
        const dk = conversationId + ':' + this.composeMode;
        if (this._draftDebounceTimers[dk]) {
          clearTimeout(this._draftDebounceTimers[dk]);
          delete this._draftDebounceTimers[dk];
        }
      }

      // Очистить форму
      if (messageBodyEl && messageBodyEl.contentEditable === 'true') {
        messageBodyEl.innerHTML = '';
        this.updateOperatorInputHeight(messageBodyEl);
      } else {
        const bodyInput = form.querySelector('[name="body"]');
        if (bodyInput) bodyInput.value = '';
      }
      if (fileInput) {
        fileInput.value = '';
        const namesEl = document.getElementById('messageAttachmentsNames');
        if (namesEl) namesEl.textContent = '';
      }
      
      // Добавить новое сообщение в диалог (без полного перерендера)
      this.appendNewMessages([newMessage]);
      
      // Обновить список диалогов (обновит превью/время)
      this.refreshConversationList();
      
      // Показать уведомление об успешной отправке
      this.showNotification('Сообщение отправлено', 'success');
      
    } catch (error) {
      console.error('Failed to send message:', error);
      this.showNotification('Ошибка отправки сообщения: ' + error.message, 'error');
    } finally {
      if (submitButton) {
        submitButton.disabled = false;
        submitButton.innerHTML = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2L11 13"/><path d="M22 2l-7 20-4-9-9-4 20-7z"/></svg>';
      }
    }
  }

  /**
   * Инициализация модалки завершения диалога (Plan 2 Task 7).
   * Навешивает обработчики на backdrop, кнопки и событие messenger:open-resolve-modal.
   * Идемпотентна — защищена флагом _resolveModalInitialized от дублей слушателей.
   */
  initResolveModal() {
    if (this._resolveModalInitialized) return;
    this._resolveModalInitialized = true;

    // Слушатель кастом-события от primary CTA «Завершить»
    window.addEventListener('messenger:open-resolve-modal', (e) => {
      const id = e && e.detail && e.detail.id;
      if (id) this.openResolveModal(id);
    });

    const modal = document.getElementById('resolveDialogModal');
    if (!modal) return;

    // Закрытие по backdrop / кнопкам с data-close-resolve-modal
    modal.querySelectorAll('[data-close-resolve-modal]').forEach((el) => {
      el.addEventListener('click', () => this.closeResolveModal());
    });

    // Submit — запуск undo-flow
    const submitBtn = document.getElementById('resolveDialogSubmit');
    if (submitBtn) {
      submitBtn.addEventListener('click', () => this.submitResolveModal());
    }

    // Escape закрывает модалку (но не отменяет pending resolve)
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && modal && !modal.classList.contains('hidden')) {
        this.closeResolveModal();
      }
    });
  }

  /**
   * Открыть модалку завершения диалога для conversationId.
   * Сбрасывает форму, ставит фокус на select. Если уже есть pending resolve —
   * отменяет предыдущий таймер (новая модалка подразумевает пересмотр решения).
   */
  openResolveModal(conversationId) {
    if (!conversationId) return;
    // Если был активный pending — отменить (юзер передумал и открыл новую модалку)
    if (this._pendingResolve && this._pendingResolve.timerId) {
      clearTimeout(this._pendingResolve.timerId);
      if (this._pendingResolve.toast && this._pendingResolve.toast.cancel) {
        this._pendingResolve.toast.cancel();
      }
      this._pendingResolve = null;
    }

    this._resolveModalConversationId = conversationId;
    const modal = document.getElementById('resolveDialogModal');
    const outcomeSel = document.getElementById('resolveDialogOutcome');
    const commentTa = document.getElementById('resolveDialogComment');
    if (outcomeSel) outcomeSel.value = '';
    if (commentTa) commentTa.value = '';
    if (modal) modal.classList.remove('hidden');
    if (outcomeSel) {
      setTimeout(() => outcomeSel.focus(), 50);
    }
  }

  /** Закрыть модалку завершения диалога (без отмены pending resolve). */
  closeResolveModal() {
    const modal = document.getElementById('resolveDialogModal');
    if (modal) modal.classList.add('hidden');
    this._resolveModalConversationId = null;
  }

  /**
   * Обработать нажатие «Завершить» в модалке. Валидация выбранного исхода,
   * закрытие модалки и запуск undo-flow через showUndoToast (5 сек).
   */
  submitResolveModal() {
    const outcomeSel = document.getElementById('resolveDialogOutcome');
    const commentTa = document.getElementById('resolveDialogComment');
    const outcome = outcomeSel ? outcomeSel.value : '';
    const comment = commentTa ? commentTa.value.trim() : '';
    const id = this._resolveModalConversationId;

    if (!outcome) {
      this.showNotification('Выберите исход', 'error');
      return;
    }
    if (!id) {
      this.closeResolveModal();
      return;
    }

    this.closeResolveModal();

    // Если был предыдущий pending — отменить (новый перетирает старый).
    if (this._pendingResolve && this._pendingResolve.timerId) {
      clearTimeout(this._pendingResolve.timerId);
      if (this._pendingResolve.toast && this._pendingResolve.toast.cancel) {
        this._pendingResolve.toast.cancel();
      }
      this._pendingResolve = null;
    }

    // Запустить undo-flow: 5с окно для отмены, затем PATCH status=resolved.
    const timerId = setTimeout(() => {
      const pending = this._pendingResolve;
      this._pendingResolve = null;
      if (!pending) return;
      const payload = {
        status: 'resolved',
        resolution: {
          outcome: pending.outcome,
          comment: pending.comment || '',
          resolved_at: new Date().toISOString(),
        },
      };
      this.patchConversation(pending.id, payload, () => {
        this.showNotification('Не удалось завершить диалог — проверьте статус', 'error');
      });
    }, 5000);

    const toast = this.showUndoToast('Диалог будет завершён', () => {
      // onUndo — пользователь нажал «Отменить» в тосте.
      if (this._pendingResolve && this._pendingResolve.timerId) {
        clearTimeout(this._pendingResolve.timerId);
      }
      this._pendingResolve = null;
      this.showNotification('Отменено', 'info');
    }, 5000);

    this._pendingResolve = { id, outcome, comment, timerId, toast };
  }

  // ============================================================
  // Plan 2 Task 8 — модалка передачи диалога оператору
  // ============================================================

  /**
   * Инициализация модалки передачи (Plan 2 Task 8).
   * Навешивает слушатель messenger:open-transfer-modal, кнопок и полей формы.
   * Идемпотентна — защищена флагом _transferModalInitialized.
   */
  initTransferModal() {
    if (this._transferModalInitialized) return;
    this._transferModalInitialized = true;

    window.addEventListener('messenger:open-transfer-modal', (e) => {
      const id = e && e.detail && e.detail.id;
      if (id) this.openTransferModal(id);
    });

    const modal = document.getElementById('transferDialogModal');
    if (!modal) return;

    modal.querySelectorAll('[data-close-transfer-modal]').forEach((el) => {
      el.addEventListener('click', () => this.closeTransferModal());
    });

    const submitBtn = document.getElementById('transferDialogSubmit');
    if (submitBtn) {
      submitBtn.addEventListener('click', () => this.submitTransferModal());
    }

    const branchSel = document.getElementById('transferBranchSelect');
    const agentSel = document.getElementById('transferAgentSelect');
    const offlineCb = document.getElementById('transferShowOffline');
    const reasonTa = document.getElementById('transferReason');
    const warn = document.getElementById('transferCrossBranchWarn');

    if (branchSel) {
      branchSel.addEventListener('change', () => {
        // Показ/скрытие предупреждения cross-branch
        if (warn) {
          const origin = this._pendingTransferOriginBranch;
          const isCross = origin != null && String(branchSel.value) !== String(origin);
          warn.classList.toggle('hidden', !isCross);
        }
        // Перезагрузить операторов для выбранного филиала
        this.loadTransferAgents(branchSel.value, !(offlineCb && offlineCb.checked));
      });
    }
    if (offlineCb && branchSel) {
      offlineCb.addEventListener('change', () => {
        this.loadTransferAgents(branchSel.value, !offlineCb.checked);
      });
    }
    if (agentSel) {
      agentSel.addEventListener('change', () => this._updateTransferSubmitState());
    }
    if (reasonTa) {
      reasonTa.addEventListener('input', () => this._updateTransferSubmitState());
    }

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && modal && !modal.classList.contains('hidden')) {
        this.closeTransferModal();
      }
    });
  }

  /** Обновляет состояние disabled у кнопки «Передать». */
  _updateTransferSubmitState() {
    const submitBtn = document.getElementById('transferDialogSubmit');
    const agentSel = document.getElementById('transferAgentSelect');
    const reasonTa = document.getElementById('transferReason');
    if (!submitBtn) return;
    const agentOk = !!(agentSel && agentSel.value);
    const reasonOk = !!(reasonTa && reasonTa.value.trim().length >= 5);
    submitBtn.disabled = !(agentOk && reasonOk);
  }

  /**
   * Открыть модалку передачи для conversationId.
   * Загружает филиалы и операторов, сбрасывает форму.
   */
  async openTransferModal(conversationId) {
    if (!conversationId) return;

    this._pendingTransferId = conversationId;
    this._pendingTransferOriginBranch = null;

    const modal = document.getElementById('transferDialogModal');
    const branchSel = document.getElementById('transferBranchSelect');
    const agentSel = document.getElementById('transferAgentSelect');
    const reasonTa = document.getElementById('transferReason');
    const offlineCb = document.getElementById('transferShowOffline');
    const warn = document.getElementById('transferCrossBranchWarn');

    if (reasonTa) reasonTa.value = '';
    if (offlineCb) offlineCb.checked = false;
    if (warn) warn.classList.add('hidden');
    if (agentSel) agentSel.innerHTML = '<option value="">— загрузка —</option>';
    if (branchSel) branchSel.innerHTML = '<option value="">— загрузка —</option>';
    this._updateTransferSubmitState();

    if (modal) modal.classList.remove('hidden');

    // Загружаем детали диалога, чтобы узнать текущий branch
    let originBranchId = null;
    try {
      const convResp = await fetch(`/api/conversations/${conversationId}/`, { credentials: 'same-origin' });
      if (convResp.ok) {
        const conv = await convResp.json();
        originBranchId = conv.branch || null;
      }
    } catch (err) {
      console.error('openTransferModal: не удалось загрузить диалог:', err);
    }
    this._pendingTransferOriginBranch = originBranchId;

    // Загружаем филиалы
    try {
      const brResp = await fetch('/api/messenger/branches/', { credentials: 'same-origin' });
      if (brResp.ok && branchSel) {
        const branches = await brResp.json();
        branchSel.innerHTML = '';
        const emptyOpt = document.createElement('option');
        emptyOpt.value = '';
        emptyOpt.textContent = '— не указан —';
        branchSel.appendChild(emptyOpt);
        (branches || []).forEach((b) => {
          const opt = document.createElement('option');
          opt.value = String(b.id);
          opt.textContent = b.name + (b.code ? ` (${b.code})` : '');
          branchSel.appendChild(opt);
        });
        if (originBranchId != null) {
          branchSel.value = String(originBranchId);
        }
      }
    } catch (err) {
      console.error('openTransferModal: не удалось загрузить филиалы:', err);
    }

    // Загружаем операторов
    const currentBranch = branchSel ? branchSel.value : '';
    await this.loadTransferAgents(currentBranch, true);

    if (agentSel) {
      setTimeout(() => agentSel.focus(), 50);
    }
  }

  /**
   * Загружает список операторов из /api/conversations/agents/ и заполняет select.
   * Исключает текущего пользователя (нельзя передать самому себе).
   */
  async loadTransferAgents(branchId, onlineOnly) {
    const agentSel = document.getElementById('transferAgentSelect');
    if (!agentSel) return;

    agentSel.innerHTML = '<option value="">— загрузка —</option>';
    this._updateTransferSubmitState();

    const params = new URLSearchParams();
    if (branchId) params.append('branch_id', String(branchId));
    if (onlineOnly) params.append('online', '1');

    let agents = [];
    try {
      const resp = await fetch('/api/conversations/agents/?' + params.toString(), { credentials: 'same-origin' });
      if (resp.ok) {
        agents = await resp.json();
      } else {
        console.error('loadTransferAgents: HTTP', resp.status);
      }
    } catch (err) {
      console.error('loadTransferAgents failed:', err);
    }

    const ctx = window.MESSENGER_CONTEXT || {};
    const currentUserId = ctx.currentUserId;

    const filtered = (agents || []).filter((a) => a && a.id !== currentUserId);

    agentSel.innerHTML = '';
    if (filtered.length === 0) {
      const opt = document.createElement('option');
      opt.value = '';
      opt.textContent = 'Нет подходящих операторов';
      opt.disabled = true;
      opt.selected = true;
      agentSel.appendChild(opt);
    } else {
      const placeholder = document.createElement('option');
      placeholder.value = '';
      placeholder.textContent = '— выберите оператора —';
      agentSel.appendChild(placeholder);
      filtered.forEach((a) => {
        const opt = document.createElement('option');
        opt.value = String(a.id);
        const name = a.full_name || a.name || a.username || ('User #' + a.id);
        const statusMark = a.messenger_online ? ' ●' : ' ○';
        opt.textContent = name + statusMark;
        agentSel.appendChild(opt);
      });
    }
    this._updateTransferSubmitState();
  }

  /** Закрыть модалку передачи. */
  closeTransferModal() {
    const modal = document.getElementById('transferDialogModal');
    if (modal) modal.classList.add('hidden');
    this._pendingTransferId = null;
    this._pendingTransferOriginBranch = null;
  }

  /**
   * Отправить передачу через POST /api/messenger/conversations/{id}/transfer/.
   * Серверный endpoint обрабатывает смену branch (через .update(), обходит
   * запрет Conversation.save()) и сохраняет запись в ConversationTransfer
   * с причиной — аудит реализован на бэке.
   */
  async submitTransferModal() {
    const id = this._pendingTransferId;
    const agentSel = document.getElementById('transferAgentSelect');
    const reasonTa = document.getElementById('transferReason');
    const submitBtn = document.getElementById('transferDialogSubmit');

    const agentId = agentSel ? parseInt(agentSel.value, 10) : NaN;
    const reason = reasonTa ? reasonTa.value.trim() : '';

    if (!id) {
      this.closeTransferModal();
      return;
    }
    if (!agentId) {
      this.showNotification('Выберите оператора', 'error');
      return;
    }
    if (reason.length < 5) {
      this.showNotification('Укажите причину (минимум 5 символов)', 'error');
      return;
    }

    if (submitBtn) submitBtn.disabled = true;

    try {
      const resp = await fetch(`/api/messenger/conversations/${id}/transfer/`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Accept': 'application/json',
          'Content-Type': 'application/json',
          'X-CSRFToken': this.getCsrfToken(),
        },
        body: JSON.stringify({ to_user_id: agentId, reason }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || err.error || `HTTP ${resp.status}`);
      }
      this.closeTransferModal();
      this.showNotification('Диалог передан', 'success');
      // Plan 4 Task 4 — сбросить кэш контекста, чтобы аудит обновился
      this.clearConversationContextCache(id);
      // Обновить карточку/список
      try {
        await this.openConversation(id, { force: true });
      } catch {}
      this.refreshConversationList && this.refreshConversationList();
    } catch (e) {
      console.error('submitTransferModal failed:', e);
      this.showNotification('Не удалось передать диалог: ' + (e.message || e), 'error');
      if (submitBtn) submitBtn.disabled = false;
    }
  }

  /**
   * Показать undo-тост с кнопкой «Отменить» и прогресс-баром.
   * Возвращает {cancel} — cancel() принудительно удаляет тост (без вызова onUndo).
   * Через timeoutMs тост удаляется автоматически (callback отправки вызывается в
   * таймере submitResolveModal, а НЕ здесь).
   */
  showUndoToast(message, onUndo, timeoutMs = 5000) {
    // Контейнер снизу по центру (отдельный от toastContainer)
    let container = this._undoToastContainer;
    if (!container) {
      container = document.createElement('div');
      container.className = 'fixed bottom-6 left-1/2 z-[9999] pointer-events-none';
      container.style.transform = 'translateX(-50%)';
      document.body.appendChild(container);
      this._undoToastContainer = container;
    }

    const toast = document.createElement('div');
    toast.className = 'pointer-events-auto bg-brand-dark text-white rounded-xl shadow-2xl overflow-hidden';
    toast.style.cssText = 'min-width:320px;max-width:420px;opacity:0;transform:translateY(12px);transition:opacity .25s ease,transform .25s ease;';
    toast.innerHTML = `
      <div class="flex items-center gap-3 px-4 py-3">
        <span class="text-sm flex-1">${this.escapeHtml(message)}</span>
        <button type="button" class="text-sm font-semibold text-brand-orange hover:text-brand-orange/80 whitespace-nowrap" data-undo-btn>Отменить</button>
      </div>
      <div class="h-1 bg-white/10">
        <div class="h-full bg-brand-orange" data-undo-progress style="width:100%;transition:width ${timeoutMs}ms linear;"></div>
      </div>
    `;
    container.appendChild(toast);

    requestAnimationFrame(() => {
      toast.style.opacity = '1';
      toast.style.transform = 'translateY(0)';
      const bar = toast.querySelector('[data-undo-progress]');
      if (bar) bar.style.width = '0%';
    });

    let removed = false;
    const remove = () => {
      if (removed) return;
      removed = true;
      toast.style.opacity = '0';
      toast.style.transform = 'translateY(12px)';
      setTimeout(() => {
        if (toast.parentNode) toast.parentNode.removeChild(toast);
      }, 300);
    };

    const undoBtn = toast.querySelector('[data-undo-btn]');
    if (undoBtn) {
      undoBtn.addEventListener('click', () => {
        if (removed) return;
        remove();
        if (typeof onUndo === 'function') {
          try { onUndo(); } catch (e) { console.error('undo callback failed:', e); }
        }
      });
    }

    // Автоудаление тоста по таймауту (callback отправки — снаружи).
    setTimeout(remove, timeoutMs);

    return { cancel: remove };
  }

  /**
   * Показать уведомление (toast) — стекируемые, анимированные
   */
  showNotification(message, type = 'info') {
    // Создать контейнер при первом вызове
    if (!this._toastContainer) {
      this._toastContainer = document.createElement('div');
      this._toastContainer.className = 'fixed top-4 right-4 z-[9999] flex flex-col gap-2 pointer-events-none';
      this._toastContainer.style.maxWidth = '360px';
      document.body.appendChild(this._toastContainer);
    }

    const icons = {
      success: '<svg class="w-4 h-4 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/></svg>',
      error: '<svg class="w-4 h-4 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/></svg>',
      info: '<svg class="w-4 h-4 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>',
    };
    const colors = {
      success: 'bg-green-600 text-white',
      error: 'bg-red-600 text-white',
      info: 'bg-brand-dark text-white',
    };

    const toast = document.createElement('div');
    toast.className = `pointer-events-auto flex items-center gap-2 px-4 py-3 rounded-xl shadow-xl text-sm font-medium ${colors[type] || colors.info}`;
    toast.style.cssText = 'opacity:0;transform:translateX(20px);transition:opacity .25s ease,transform .25s ease;';
    toast.innerHTML = `${icons[type] || icons.info}<span>${this.escapeHtml(message)}</span>`;

    this._toastContainer.appendChild(toast);
    // Trigger анимация входа
    requestAnimationFrame(() => { toast.style.opacity = '1'; toast.style.transform = 'translateX(0)'; });

    // Удаление через 4с с анимацией
    const dismiss = () => {
      toast.style.opacity = '0';
      toast.style.transform = 'translateX(20px)';
      setTimeout(() => toast.remove(), 300);
    };
    toast.addEventListener('click', dismiss);
    setTimeout(dismiss, 4000);

    // Ограничить до 4 тостов
    while (this._toastContainer.children.length > 4) {
      this._toastContainer.firstChild.remove();
    }
  }

  /**
   * Алиас для showNotification — часть кода использует showToast().
   */
  showToast(message, type = 'info') {
    return this.showNotification(message, type);
  }

  /**
   * Определить эффективный ui_status диалога.
   * Fallback-логика для старых диалогов до миграции: повторяет Conversation.ui_status property.
   */
  getEffectiveUiStatus(conversation) {
    if (conversation && conversation.ui_status) return conversation.ui_status;
    if (!conversation) return 'in_progress';
    const s = conversation.status;
    if (s === 'resolved' || s === 'closed') return 'closed';
    if (!conversation.assignee) return 'new';
    return 'in_progress';
  }

  /**
   * Получить конфиг контекстной primary CTA для шапки диалога.
   * Возвращает {label, onClick} в зависимости от ui_status.
   */
  getPrimaryCtaConfig(conversation) {
    const uiStatus = this.getEffectiveUiStatus(conversation);
    const id = conversation.id;
    const ctx = window.MESSENGER_CONTEXT || {};
    const currentUserId = ctx.currentUserId || null;

    if (uiStatus === 'new') {
      return {
        label: 'Взять в работу',
        onClick: () => {
          if (!currentUserId) {
            this.showToast('Не удалось определить текущего пользователя', 'error');
            return;
          }
          this.patchConversation(id, { assignee: currentUserId });
        },
      };
    }
    if (uiStatus === 'waiting') {
      return {
        label: 'Ответить',
        onClick: () => {
          const body = document.getElementById('messageBody');
          if (body) {
            body.focus();
          }
          this.scrollToBottom(true);
        },
      };
    }
    if (uiStatus === 'closed') {
      return {
        label: 'Переоткрыть',
        onClick: () => this.patchConversation(id, { status: 'open' }),
      };
    }
    // in_progress (или fallback)
    return {
      label: 'Завершить',
      onClick: () => {
        window.dispatchEvent(new CustomEvent('messenger:open-resolve-modal', { detail: { id } }));
      },
    };
  }

  /**
   * Отрисовать primary CTA + меню ⋯ в слот #headerActionsSlot.
   * Идемпотентен: каждый вызов перетирает innerHTML и навешивает свежие обработчики.
   */
  renderHeaderActions(conversation) {
    const slot = document.getElementById('headerActionsSlot');
    if (!slot || !conversation) return;

    // Снять предыдущий document listener меню, если был
    if (this._headerMenuDocListener) {
      document.removeEventListener('click', this._headerMenuDocListener);
      this._headerMenuDocListener = null;
    }
    if (this._headerMenuKeyListener) {
      document.removeEventListener('keydown', this._headerMenuKeyListener);
      this._headerMenuKeyListener = null;
    }

    const cta = this.getPrimaryCtaConfig(conversation);
    slot.innerHTML = `
      <button type="button" id="headerPrimaryCta" class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-brand-teal text-white text-sm font-medium hover:bg-brand-teal/90 shadow-sm">
        ${this.escapeHtml(cta.label)}
      </button>
      <div class="relative">
        <button type="button" id="headerMenuBtn" class="inline-flex items-center justify-center w-8 h-8 rounded-full border border-brand-soft/80 text-brand-dark/60 hover:text-brand-dark hover:border-brand-soft" title="Действия" aria-haspopup="true" aria-expanded="false">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="5" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="19" cy="12" r="1.2"/>
          </svg>
        </button>
        <div id="headerMenuDropdown" class="hidden absolute right-0 top-full mt-1 w-56 bg-white rounded-lg shadow-lg border border-brand-soft/40 z-50 py-1 text-sm">
          ${conversation.status === 'waiting_offline' ? '<button type="button" data-action="contacted-back" class="w-full text-left px-3 py-2 hover:bg-emerald-50 text-emerald-700 font-medium">✓ Я связался</button>' : ''}
          <button type="button" data-action="transfer" class="w-full text-left px-3 py-2 hover:bg-brand-soft/30 text-brand-dark">Передать оператору</button>
          <button type="button" data-action="needs-help" class="w-full text-left px-3 py-2 hover:bg-brand-soft/30 text-brand-dark">Позвать старшего</button>
          <button type="button" data-action="unassign" class="w-full text-left px-3 py-2 hover:bg-brand-soft/30 text-brand-dark">Вернуть в очередь</button>
        </div>
      </div>
    `;

    const ctaBtn = document.getElementById('headerPrimaryCta');
    if (ctaBtn) {
      ctaBtn.addEventListener('click', (e) => {
        e.preventDefault();
        try {
          cta.onClick();
        } catch (err) {
          console.error('Primary CTA failed:', err);
        }
      });
    }

    const menuBtn = document.getElementById('headerMenuBtn');
    const dropdown = document.getElementById('headerMenuDropdown');
    if (menuBtn && dropdown) {
      menuBtn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        this.toggleHeaderMenu();
      });
      dropdown.querySelectorAll('button[data-action]').forEach((btn) => {
        btn.addEventListener('click', (e) => {
          e.preventDefault();
          e.stopPropagation();
          const action = btn.getAttribute('data-action');
          this.closeHeaderMenu();
          this.handleHeaderMenuAction(action, conversation.id);
        });
      });
    }
  }

  toggleHeaderMenu() {
    const dropdown = document.getElementById('headerMenuDropdown');
    const btn = document.getElementById('headerMenuBtn');
    if (!dropdown) return;
    const isHidden = dropdown.classList.contains('hidden');
    if (isHidden) {
      dropdown.classList.remove('hidden');
      if (btn) btn.setAttribute('aria-expanded', 'true');
      // Навесить listeners на document (закрытие по клику вне / Escape)
      this._headerMenuDocListener = (ev) => {
        const dd = document.getElementById('headerMenuDropdown');
        const bb = document.getElementById('headerMenuBtn');
        if (!dd) return;
        if (dd.contains(ev.target)) return;
        if (bb && bb.contains(ev.target)) return;
        this.closeHeaderMenu();
      };
      this._headerMenuKeyListener = (ev) => {
        if (ev.key === 'Escape') this.closeHeaderMenu();
      };
      // setTimeout чтобы не поймать текущий click
      setTimeout(() => {
        document.addEventListener('click', this._headerMenuDocListener);
        document.addEventListener('keydown', this._headerMenuKeyListener);
      }, 0);
    } else {
      this.closeHeaderMenu();
    }
  }

  closeHeaderMenu() {
    const dropdown = document.getElementById('headerMenuDropdown');
    const btn = document.getElementById('headerMenuBtn');
    if (dropdown) dropdown.classList.add('hidden');
    if (btn) btn.setAttribute('aria-expanded', 'false');
    if (this._headerMenuDocListener) {
      document.removeEventListener('click', this._headerMenuDocListener);
      this._headerMenuDocListener = null;
    }
    if (this._headerMenuKeyListener) {
      document.removeEventListener('keydown', this._headerMenuKeyListener);
      this._headerMenuKeyListener = null;
    }
  }

  handleHeaderMenuAction(action, conversationId) {
    if (action === 'transfer') {
      window.dispatchEvent(new CustomEvent('messenger:open-transfer-modal', { detail: { id: conversationId } }));
      return;
    }
    if (action === 'needs-help') {
      this.handleNeedsHelp(conversationId);
      return;
    }
    if (action === 'unassign') {
      this.patchConversation(conversationId, { assignee: null });
      return;
    }
    if (action === 'contacted-back') {
      this.handleContactedBack(conversationId);
      return;
    }
  }

  async handleContactedBack(conversationId) {
    try {
      const response = await fetch(`/api/conversations/${conversationId}/contacted-back/`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Accept': 'application/json',
          'X-CSRFToken': this.getCsrfToken(),
        },
      });
      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.detail || 'Ошибка запроса');
      }
      this.showToast('Диалог взят в работу', 'success');
      // Перезагрузим конверсацию, чтобы статус/assignee обновились в UI.
      if (typeof this.reloadCurrentConversation === 'function') {
        this.reloadCurrentConversation();
      } else {
        window.location.reload();
      }
    } catch (e) {
      console.error('handleContactedBack failed:', e);
      this.showToast('Не удалось отметить связь: ' + (e.message || e), 'error');
    }
  }

  async handleNeedsHelp(conversationId) {
    try {
      const response = await fetch(`/api/conversations/${conversationId}/needs-help/`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Accept': 'application/json',
          'X-CSRFToken': this.getCsrfToken(),
        },
      });
      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.detail || 'Ошибка запроса');
      }
      this.showToast('Руководитель уведомлён', 'success');
    } catch (e) {
      console.error('handleNeedsHelp failed:', e);
      this.showToast('Не удалось позвать старшего: ' + (e.message || e), 'error');
    }
  }

  /**
   * Рендерить HTML одного сообщения
   */
  renderMessageHtml(message) {
    const dir = (message.direction || '').toLowerCase();
    const isOutgoing = dir === 'out' || dir === 'internal';
    const isInternal = dir === 'internal';
    const senderName = isOutgoing
      ? (message.sender_user_name || message.sender_user_username || 'Оператор')
      : (message.sender_contact_name || 'Клиент');
    const avatarInitial = isOutgoing
      ? (senderName[0] || 'О').toUpperCase()
      : (senderName[0] || 'К').toUpperCase();
    
    let attachmentsHtml = '';
    if (message.attachments && message.attachments.length > 0) {
      attachmentsHtml = '<div class="messenger-attachment-cards">';
      message.attachments.forEach(att => {
        const fileUrl = att.file || att.url || '';
        const fileName = att.original_name || fileUrl.split('/').pop() || 'Файл';
        const contentType = (att.content_type || '').toLowerCase();
        const fileExt = fileName.split('.').pop()?.toUpperCase() || '';
        const isImage = contentType.indexOf('image/') === 0 || ['PNG', 'JPG', 'JPEG', 'GIF', 'WEBP'].includes(fileExt);
        const isPdf = contentType === 'application/pdf' || fileExt === 'PDF';
        
        let previewHtml = '';
        let iconClass = 'file';
        if (isImage && fileUrl) {
          previewHtml = `<div class="messenger-attachment-card__preview"><img src="${this.escapeHtml(fileUrl)}" alt="" loading="lazy" /></div>`;
        } else {
          if (isPdf) iconClass = 'pdf';
          else if (['DOC', 'DOCX'].includes(fileExt)) iconClass = 'doc';
          else if (['XLS', 'XLSX'].includes(fileExt)) iconClass = 'xls';
          else if (['PPT', 'PPTX'].includes(fileExt)) iconClass = 'ppt';
          
          const iconSvg = {
            pdf: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6zm-1 2 5 5h-5V4zm-2 8v4H9v-4H7v6h10v-6h-2zm-2-2h2v2H9v-2z"/></svg>',
            doc: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6zm4 16H6V4h7v5h5v9zm-3-5H9v2h2v2H9v2h2v-2h2v-2h-2v-2z"/></svg>',
            xls: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6zm1 11h-4v2h4v2h-4v2h2v-1h2v-4h-2v1h-2v-2zm-2-5V4h5l-5 5z"/></svg>',
            ppt: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6zm1 9h-2v4h2v-1h1c.55 0 1-.45 1-1v-1c0-.55-.45-1-1-1h-2v-1zm0-5V4h5l-5 5z"/></svg>',
            file: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6z"/><path d="M14 2v6h6"/></svg>'
          };
          previewHtml = `<div class="messenger-attachment-card__preview"><div class="messenger-attachment-card__icon messenger-attachment-card__icon--${iconClass}">${iconSvg[iconClass] || iconSvg.file}</div></div>`;
        }
        
        attachmentsHtml += `
          <div class="messenger-attachment-card" 
               data-open="${this.escapeHtml(fileUrl)}"
               data-download="${this.escapeHtml(fileUrl)}"
               data-is-image="${isImage ? '1' : '0'}"
               data-is-pdf="${isPdf ? '1' : '0'}"
               title="${this.escapeHtml(fileName)}"
               role="button" tabindex="0">
            ${previewHtml}
            <div class="messenger-attachment-card__name">${this.escapeHtml(fileName)}</div>
          </div>
        `;
      });
      attachmentsHtml += '</div>';
    }
    
    const internalBadge = isInternal
      ? '<span class="inline-flex items-center px-2 py-0.5 rounded-full bg-brand-orange/10 text-[10px] text-brand-orange font-semibold mr-2">Заметка</span>'
      : '';

    return `
      <div class="flex gap-3 mb-4 msg-appear ${isOutgoing ? 'flex-row-reverse' : 'flex-row'}" data-message-id="${message.id}">
        <div class="flex-shrink-0">
          <div class="w-8 h-8 rounded-full flex items-center justify-center text-sm font-semibold ${
            isInternal
              ? 'bg-brand-orange/20 text-brand-orange'
              : (isOutgoing ? 'bg-brand-teal/20 text-brand-teal' : 'bg-brand-teal/20 text-brand-teal')
          }">
            ${avatarInitial}
          </div>
        </div>
        <div class="flex-1 ${isOutgoing ? 'text-right' : 'text-left'}">
          <div class="inline-block max-w-[80%] rounded-lg px-4 py-2 ${
            isInternal
              ? 'bg-brand-orange/5 border border-dashed border-brand-orange/40'
              : (isOutgoing ? 'bg-brand-teal/10' : 'bg-brand-soft/40')
          }">
            <div class="text-sm font-medium mb-1">${internalBadge}${this.escapeHtml(senderName)}</div>
            <div class="text-sm text-brand-dark whitespace-pre-wrap">${this.renderMessageBodyWithEmojis(message.body || '')}</div>
            ${attachmentsHtml}
            <div class="flex items-center justify-between mt-1">
              <div class="text-xs text-brand-dark/50">
                ${new Date(message.created_at).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}
              </div>
              ${isOutgoing && !isInternal ? `
                <div class="flex items-center gap-0.5 ml-2">
                  ${message.read_at ? `
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" class="text-brand-teal">
                      <path d="M20 6L9 17l-5-5"/>
                    </svg>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" class="text-brand-teal -ml-2">
                      <path d="M20 6L9 17l-5-5"/>
                    </svg>
                  ` : message.delivered_at ? `
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" class="text-brand-dark/40">
                      <path d="M20 6L9 17l-5-5"/>
                    </svg>
                  ` : `
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" class="text-brand-dark/20">
                      <circle cx="12" cy="12" r="10"/>
                    </svg>
                  `}
                </div>
              ` : ''}
            </div>
          </div>
        </div>
      </div>
    `;
  }

  /**
   * Проверить, находится ли пользователь внизу списка сообщений
   */
  isScrolledToBottom(threshold = 100) {
    const messagesList = document.getElementById('messagesList');
    if (!messagesList) return false;
    return messagesList.scrollHeight - messagesList.scrollTop - messagesList.clientHeight < threshold;
  }

  /**
   * Автоскролл к последнему сообщению (только если пользователь внизу)
   */
  scrollToBottom(force = false) {
    const messagesList = document.getElementById('messagesList');
    if (!messagesList) return;
    
    if (force || this.isScrolledToBottom()) {
      setTimeout(() => {
        messagesList.scrollTop = messagesList.scrollHeight;
      }, 50);
    }
  }

  /**
   * Добавить новые сообщения в диалог (без полного перерендера)
   */
  appendNewMessages(messages) {
    if (!messages || messages.length === 0) return;
    
    const messagesList = document.getElementById('messagesList');
    if (!messagesList) return;
    
    const wasAtBottom = this.isScrolledToBottom();
    let currentDate = this.lastRenderedDate;
    let appendedCount = 0;
    
    messages.forEach(message => {
      // Пропускаем уже отображённые сообщения
      if (this.lastMessageIds.has(message.id)) return;
      
      const messageDate = new Date(message.created_at).toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' });
      
      // Добавляем сепаратор даты, если дата изменилась
      if (currentDate !== messageDate) {
        currentDate = messageDate;
        this.lastRenderedDate = messageDate;
        
        // Проверяем, нет ли уже такого сепаратора
        const existingSeparator = messagesList.querySelector(`[data-date-separator="${messageDate}"]`);
        if (!existingSeparator) {
          const separatorDiv = document.createElement('div');
          separatorDiv.className = 'text-center my-4';
          separatorDiv.setAttribute('data-date-separator', messageDate);
          const dateSpan = document.createElement('span');
          dateSpan.className = 'inline-block px-3 py-1 rounded-full bg-brand-soft/40 text-xs text-brand-dark/70';
          dateSpan.textContent = messageDate;
          separatorDiv.appendChild(dateSpan);
          messagesList.appendChild(separatorDiv);
        }
      }
      
      // Добавляем сообщение
      const messageDiv = document.createElement('div');
      messageDiv.innerHTML = this.renderMessageHtml(message);
      const messageEl = messageDiv.firstElementChild;
      messagesList.appendChild(messageEl);
      
      this.lastMessageIds.add(message.id);
      appendedCount += 1;

    // Уведомление для новых входящих сообщений
    const dir = (message.direction || '').toLowerCase();
    const isIncoming = dir === 'in';
    if (isIncoming) {
      const shouldNotify = document.hidden || !wasAtBottom;
      if (shouldNotify) {
        this.notifyNewIncomingMessage(message);
      }
    }
    });
    
    // Обновляем timestamp последнего сообщения
    if (messages.length > 0) {
      this.lastMessageTimestamp = messages[messages.length - 1].created_at;
    }
    
    // Автоскролл только если пользователь был внизу
    if (wasAtBottom) {
      this.scrollToBottom(true);
    } else if (appendedCount > 0) {
      this.pendingNewMessagesCount += appendedCount;
      this.updateNewMessagesButton();
    }
    
    // Обновляем карточку диалога в списке (превью/время)
    this.updateConversationCardPreview(this.currentConversationId, messages[messages.length - 1]);
  }

  /**
   * Обновить превью/время в карточке диалога (точечно, без перерендера всего списка)
   */
  updateConversationCardPreview(conversationId, lastMessage) {
    if (!conversationId || !lastMessage) return;
    
    const card = document.querySelector(`.conversation-card[data-conversation-id="${conversationId}"]`);
    if (!card) return;
    
    // Обновляем превью
    const previewEl = card.querySelector('.conversation-preview');
    if (previewEl && lastMessage.body) {
      const preview = this.escapeHtml(lastMessage.body.trim()).slice(0, 140);
      previewEl.textContent = preview || 'Нет сообщений';
    }
    
    // Обновляем время
    const timeEl = card.querySelector('.conversation-time');
    if (timeEl && lastMessage.created_at) {
      const lastAt = new Date(lastMessage.created_at);
      const now = new Date();
      const timeStr = lastAt.toDateString() === now.toDateString()
        ? lastAt.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
        : lastAt.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' });
      timeEl.textContent = timeStr;
    }
  }

  notifyNewIncomingMessage(message) {
    try {
      this.playIncomingSound();
    } catch (e) {
      // звук не критичен
    }

    if (!this.notificationsEnabled) return;
    if (typeof Notification === 'undefined') return;
    if (Notification.permission !== 'granted') return;

    const body = (message.body || '').trim();
    const preview = body ? body.slice(0, 140) : 'Новое сообщение';

    const n = new Notification('Новое сообщение в чате', {
      body: preview,
      tag: `messenger-${message.id}`,
    });

    // Авто-закрытие через несколько секунд
    setTimeout(() => n.close(), 5000);
  }

  playIncomingSound() {
    if (!this._soundEnabled) return;
    this.playIncomingSoundV2();
  }

  /**
   * Plan 3 Task 6 — унифицированный API уведомлений.
   * Короткий WebAudio beep для входящих сообщений (независимая реализация
   * от playIncomingSoundV2 — не требует _soundEnabled, управляется _soundMuted).
   */
  playNotificationSound() {
    try {
      if (this._soundMuted) return;
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (!AudioCtx) return;
      const ctx = this._audioCtx || (this._audioCtx = new AudioCtx());
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'sine';
      osc.frequency.value = 880;
      gain.gain.value = 0.08;
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start();
      osc.frequency.exponentialRampToValueAtTime(440, ctx.currentTime + 0.1);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.15);
      osc.stop(ctx.currentTime + 0.18);
    } catch (e) { /* ignore */ }
  }

  /**
   * Plan 3 Task 6 — запрос разрешения Desktop Notification API.
   * Вызывается один раз при первом клике (user gesture), чтобы не получать
   * ошибку "permission was denied" в Chrome.
   */
  requestNotificationPermission() {
    if (!('Notification' in window)) return;
    if (Notification.permission === 'default') {
      try { Notification.requestPermission(); } catch (_) { /* ignore */ }
    }
  }

  /**
   * Plan 3 Task 6 — Desktop notification для входящих сообщений.
   * Не показывает уведомление, если вкладка активна и это текущий диалог.
   */
  showDesktopNotification(conv, message) {
    if (!('Notification' in window)) return;
    if (Notification.permission !== 'granted') return;
    if (!conv) return;
    const currentId = this.currentConversationId || (this.currentConversation && this.currentConversation.id);
    if (!document.hidden && currentId === conv.id) return;
    try {
      const contactName = conv.contact_name || (conv.contact && conv.contact.name) || 'Клиент';
      const text = (message && (message.text || message.body)) || '';
      const notif = new Notification(`Новое сообщение — ${contactName}`, {
        body: text.slice(0, 120),
        icon: '/static/img/notification-icon.png',
        tag: `conv-${conv.id}`,
      });
      notif.onclick = () => {
        window.focus();
        this.openConversation(conv.id);
        notif.close();
      };
    } catch (e) { /* ignore */ }
  }

  /**
   * Plan 3 Task 6 — счётчик непрочитанных в заголовке вкладки.
   * Используется для per-conversation SSE, когда вкладка в фоне.
   * Основной счётчик по всем диалогам — _updateTabTitle (вызывается
   * из refreshConversationList).
   */
  updateTitleBadge(unreadCount) {
    const base = this._titleBase || (this._titleBase = document.title.replace(/^\(\d+\)\s*/, ''));
    document.title = unreadCount > 0 ? `(${unreadCount}) ${base}` : base;
    if (typeof window.setFaviconBadge === 'function') {
      window.setFaviconBadge(unreadCount);
    }
  }

  startTypingPolling(conversationId) {
    this.stopTypingPolling();
    if (!conversationId) return;

    this.typingPollTimer = setInterval(async () => {
      if (document.hidden) return;
      if (this.currentConversationId !== conversationId) return;
      try {
        const response = await fetch(`/api/conversations/${conversationId}/typing/`, {
          credentials: 'same-origin',
          headers: { 'Accept': 'application/json' },
        });
        if (!response.ok) return;
        const data = await response.json();
        const contactTyping = Boolean(data && data.contact_typing);
        this.setContactTypingIndicator(contactTyping);
      } catch (e) {
        // typing не критичен
      }
    }, 2000);
  }

  stopTypingPolling() {
    if (this.typingPollTimer) {
      clearInterval(this.typingPollTimer);
      this.typingPollTimer = null;
    }
    this.setContactTypingIndicator(false);
  }

  setContactTypingIndicator(isTyping) {
    const el = document.getElementById('contactTypingIndicator');
    if (!el) return;
    if (isTyping) el.classList.remove('hidden');
    else el.classList.add('hidden');
  }

  async sendOperatorTypingPing(conversationId) {
    const now = Date.now();
    if (!conversationId) return;
    if (now - this.lastOperatorTypingSentAt < 2500) return;
    this.lastOperatorTypingSentAt = now;
    try {
      await fetch(`/api/conversations/${conversationId}/typing/`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'X-CSRFToken': this.getCsrfToken(),
        },
      });
    } catch (e) {
      // не критично
    }
  }

  /**
   * Начать SSE стрим для real-time обновлений (по образцу Chatwoot)
   */
  startPolling(conversationId) {
    // Остановить предыдущий SSE или polling
    this.stopPolling(conversationId);
    
    // Используем SSE если доступен, иначе fallback на polling
    if (typeof EventSource !== 'undefined') {
      this.startSSEStream(conversationId);
    } else {
      this.startPollingFallback(conversationId);
    }
  }

  /**
   * SSE стрим для real-time обновлений (по образцу Chatwoot).
   * Корректная очистка ресурсов: EventSource + таймер автозакрытия.
   */
  startSSEStream(conversationId) {
    // Полная очистка предыдущего SSE
    this._cleanupSSE();

    if (this.currentConversationId !== conversationId) return;
    if (document.hidden) return;

    try {
      const es = new EventSource(`/api/conversations/${conversationId}/stream/`);
      this.eventSource = es;

      es.addEventListener('ready', () => {
        this.sseReconnectAttempts = 0;
        this.sseReconnectDelayMs = 1000;
      });

      es.addEventListener('message.created', (e) => {
        try {
          const message = JSON.parse(e.data || '{}');
          if (message && message.id && !this.lastMessageIds.has(message.id)) {
            this._safeAppendMessages([message]);
            this.refreshConversationList();
            if (document.hidden && this.notificationsEnabled) {
              this.showNotification(`Новое сообщение от ${message.sender_contact_name || 'контакта'}`);
            }
            // Plan 3 Task 6: звук + desktop notification + title-badge для входящих
            const dir = (message.direction || '').toString().toLowerCase();
            if (dir === 'in') {
              this.playNotificationSound();
              const conv = {
                id: conversationId,
                contact_name: message.sender_contact_name,
              };
              this.showDesktopNotification(conv, { text: message.body || message.text || '' });
              if (document.hidden) {
                this._pendingUnread = (this._pendingUnread || 0) + 1;
                this.updateTitleBadge(this._pendingUnread);
              }
            }
          }
        } catch (err) {
          console.warn('SSE message.created parse error:', err);
        }
      });

      es.addEventListener('conversation.updated', (e) => {
        try {
          const data = JSON.parse(e.data || '{}');
          if (data && data.id === conversationId) {
            this.refreshConversationList();
            if (this.currentConversationId === conversationId) {
              this._fetch(`/api/conversations/${conversationId}/`)
                .then(r => r.ok ? r.json() : null)
                .then(conv => conv && this.renderConversationInfo(conv))
                .catch(() => {});
            }
          }
        } catch (err) {
          console.warn('SSE conversation.updated parse error:', err);
        }
      });

      es.addEventListener('conversation.typing_started', () => this.setContactTypingIndicator(true));
      es.addEventListener('conversation.typing_stopped', () => this.setContactTypingIndicator(false));

      es.onerror = () => {
        this._cleanupSSE();
        // Экспоненциальный backoff
        if (this.sseReconnectAttempts < this.maxSseReconnectAttempts &&
            this.currentConversationId === conversationId &&
            !document.hidden) {
          this.sseReconnectAttempts++;
          const delay = Math.min(this.sseReconnectDelayMs * Math.pow(2, this.sseReconnectAttempts - 1), 15000);
          setTimeout(() => {
            if (this.currentConversationId === conversationId && !document.hidden) {
              this.startSSEStream(conversationId);
            }
          }, delay);
        } else {
          this.startPollingFallback(conversationId);
        }
      };

      // Автозакрытие через 25с (сервер через 30с) — чистый reconnect цикл
      this._sseTimeoutId = setTimeout(() => {
        if (this.eventSource === es && this.currentConversationId === conversationId) {
          this._cleanupSSE();
          if (!document.hidden) this.startSSEStream(conversationId);
        }
      }, 25000);

    } catch (err) {
      console.error('SSE start error:', err);
      this.startPollingFallback(conversationId);
    }
  }

  /** Безопасная очистка SSE — EventSource + таймер */
  _cleanupSSE() {
    if (this._sseTimeoutId) {
      clearTimeout(this._sseTimeoutId);
      this._sseTimeoutId = null;
    }
    if (this.eventSource) {
      try { this.eventSource.close(); } catch (_) {}
      this.eventSource = null;
    }
  }

  /** Append с защитой от race condition (SSE + polling одновременно) */
  _safeAppendMessages(messages) {
    if (this._appendLock) return;
    this._appendLock = true;
    try {
      const newMessages = messages.filter(m => m && m.id && !this.lastMessageIds.has(m.id));
      if (newMessages.length > 0) {
        this.appendNewMessages(newMessages);
      }
    } finally {
      this._appendLock = false;
    }
  }

  /**
   * Fallback polling (если SSE недоступен)
   */
  startPollingFallback(conversationId) {
    this.pollingIntervals[conversationId] = setInterval(async () => {
      if (this.currentConversationId !== conversationId) {
        this.stopPolling(conversationId);
        return;
      }
      if (document.hidden) return;
      try {
        const url = `/api/conversations/${conversationId}/messages/` +
                    (this.lastMessageTimestamp ? `?since=${encodeURIComponent(this.lastMessageTimestamp)}` : '');
        const response = await this._fetch(url);
        if (response.ok) {
          const messages = await response.json();
          if (messages.length > 0) {
            this._safeAppendMessages(messages);
            this.refreshConversationList();
          }
        }
      } catch (error) {
        if (error.name !== 'AbortError') console.warn('Polling error:', error);
      }
    }, 3000);
  }

  /**
   * Остановить polling/SSE — полная очистка
   */
  stopPolling(conversationId) {
    this._cleanupSSE();
    if (this.pollingIntervals[conversationId]) {
      clearInterval(this.pollingIntervals[conversationId]);
      delete this.pollingIntervals[conversationId];
    }
  }

  /**
   * Эмодзи-пикер в форме сообщения оператора
   */
  /**
   * Макросы — загрузить список и привязать dropdown
   */
  initMacros(conversationId) {
    const macroBtn = document.getElementById('macroBtn');
    const dropdown = document.getElementById('macroDropdown');
    const macroList = document.getElementById('macroList');
    if (!macroBtn || !dropdown || !macroList) return;

    // Toggle dropdown
    macroBtn.addEventListener('click', async () => {
      if (!dropdown.classList.contains('hidden')) {
        dropdown.classList.add('hidden');
        return;
      }
      // Загрузить макросы
      macroList.innerHTML = '<div class="px-3 py-2 text-brand-dark/50">Загрузка...</div>';
      dropdown.classList.remove('hidden');

      try {
        const resp = await fetch('/api/macros/', {
          credentials: 'same-origin',
        });
        if (!resp.ok) throw new Error(resp.status);
        const macros = await resp.json();
        const items = Array.isArray(macros) ? macros : (macros.results || []);

        if (items.length === 0) {
          macroList.innerHTML = '<div class="px-3 py-2 text-brand-dark/50 text-xs">Нет макросов</div>';
          return;
        }

        macroList.innerHTML = '';
        for (const m of items) {
          const item = document.createElement('button');
          item.type = 'button';
          item.className = 'w-full text-left px-3 py-2 hover:bg-brand-soft/30 flex items-center gap-2 text-brand-dark';
          item.innerHTML = `
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="text-brand-teal shrink-0"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
            <span class="truncate">${this.escapeHtml(m.name)}</span>
            <span class="ml-auto text-[10px] text-brand-dark/40">${m.actions?.length || 0} действий</span>
          `;
          item.addEventListener('click', () => {
            dropdown.classList.add('hidden');
            this.executeMacro(m.id, conversationId);
          });
          macroList.appendChild(item);
        }
      } catch (e) {
        macroList.innerHTML = '<div class="px-3 py-2 text-red-500 text-xs">Ошибка загрузки</div>';
      }
    });

    // Закрыть dropdown при клике вне
    document.addEventListener('click', (e) => {
      if (!macroBtn.contains(e.target) && !dropdown.contains(e.target)) {
        dropdown.classList.add('hidden');
      }
    });
  }

  async executeMacro(macroId, conversationId) {
    try {
      const resp = await fetch(`/api/macros/${macroId}/execute/`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': this.getCsrfToken(),
        },
        body: JSON.stringify({ conversation_id: conversationId }),
      });
      if (!resp.ok) throw new Error(resp.status);
      const data = await resp.json();
      // Обновить диалог после выполнения макроса
      this.openConversation(conversationId, { force: true });
    } catch (e) {
      console.error('[MessengerPanel] Macro execution failed:', e);
    }
  }

  /**
   * @mention autocomplete — обнаружение @query в тексте
   */
  _handleMentionInput(inputEl) {
    const text = inputEl.textContent || '';
    // Найти последнюю незакрытую @mention (от позиции курсора назад)
    const sel = window.getSelection();
    if (!sel || !sel.rangeCount) { this._hideMentionDropdown(); return; }
    const range = sel.getRangeAt(0);
    const textBefore = text.substring(0, range.startOffset);
    const match = textBefore.match(/@(\w*)$/);
    if (!match) { this._hideMentionDropdown(); return; }

    const query = match[1].toLowerCase();
    this._showMentionDropdown(inputEl, query);
  }

  async _showMentionDropdown(inputEl, query) {
    // Загрузить пользователей если ещё не загружены
    if (!this._mentionUsers) {
      try {
        const resp = await fetch('/api/conversations/agents/', {
          credentials: 'same-origin',
        });
        if (resp.ok) {
          this._mentionUsers = await resp.json();
        } else {
          this._mentionUsers = [];
        }
      } catch (e) {
        this._mentionUsers = [];
      }
    }

    const filtered = query
      ? this._mentionUsers.filter(u =>
          u.name.toLowerCase().includes(query) || u.username.toLowerCase().includes(query)
        )
      : this._mentionUsers;

    if (filtered.length === 0) { this._hideMentionDropdown(); return; }

    let dropdown = document.getElementById('mentionDropdown');
    if (!dropdown) {
      dropdown = document.createElement('div');
      dropdown.id = 'mentionDropdown';
      dropdown.className = 'absolute bottom-full left-0 mb-1 w-56 bg-white rounded-lg shadow-lg border border-brand-soft/40 z-50 max-h-48 overflow-y-auto';
      inputEl.parentElement.style.position = 'relative';
      inputEl.parentElement.appendChild(dropdown);
    }

    dropdown.innerHTML = '';
    dropdown.classList.remove('hidden');
    for (const user of filtered.slice(0, 8)) {
      const item = document.createElement('button');
      item.type = 'button';
      item.className = 'w-full text-left px-3 py-2 hover:bg-brand-soft/30 flex items-center gap-2 text-sm text-brand-dark';
      item.innerHTML = `
        <span class="w-6 h-6 rounded-full bg-brand-teal/20 text-brand-teal flex items-center justify-center text-xs font-bold">${this.escapeHtml((user.name || '?')[0])}</span>
        <span>${this.escapeHtml(user.name)}</span>
      `;
      item.addEventListener('mousedown', (e) => {
        e.preventDefault();
        this._insertMention(inputEl, user.username || user.name);
      });
      dropdown.appendChild(item);
    }
  }

  _hideMentionDropdown() {
    const dropdown = document.getElementById('mentionDropdown');
    if (dropdown) dropdown.classList.add('hidden');
  }

  _insertMention(inputEl, username) {
    // Заменить @query на @username
    const sel = window.getSelection();
    if (!sel || !sel.rangeCount) return;

    const text = inputEl.textContent || '';
    const range = sel.getRangeAt(0);
    const textBefore = text.substring(0, range.startOffset);
    const atIndex = textBefore.lastIndexOf('@');
    if (atIndex === -1) return;

    // Перестроить содержимое
    const before = text.substring(0, atIndex);
    const after = text.substring(range.startOffset);
    inputEl.textContent = before + '@' + username + ' ' + after;

    // Установить курсор после вставленного mention
    const newRange = document.createRange();
    const textNode = inputEl.firstChild;
    if (textNode) {
      const pos = (before + '@' + username + ' ').length;
      newRange.setStart(textNode, Math.min(pos, textNode.length));
      newRange.collapse(true);
      sel.removeAllRanges();
      sel.addRange(newRange);
    }

    this._hideMentionDropdown();
  }

  initOperatorEmojiPicker(conversationId) {
    const pickerEl = document.getElementById('operatorEmojiPicker');
    const emojiBtn = document.getElementById('messageEmojiBtn');
    const messageBody = document.getElementById('messageBody');
    if (!pickerEl || !emojiBtn || !messageBody) return;
    if (pickerEl.querySelector('.messenger-operator-emoji-picker-item')) return; // уже инициализирован
    OPERATOR_EMOJI_LIST.forEach((emoji) => {
      const span = document.createElement('span');
      span.className = 'messenger-operator-emoji-picker-item';
      const img = document.createElement('img');
      img.src = OPERATOR_EMOJI_APPLE_CDN + operatorEmojiToCodepoint(emoji) + '.png';
      img.alt = emoji;
      img.className = 'messenger-operator-emoji-picker-img';
      img.loading = 'lazy';
      img.onerror = function() { this.style.display = 'none'; if (this.nextSibling) this.nextSibling.style.display = 'inline'; };
      span.appendChild(img);
      const fallback = document.createElement('span');
      fallback.className = 'messenger-operator-emoji-picker-fallback';
      fallback.style.display = 'none';
      fallback.textContent = emoji;
      span.appendChild(fallback);
      span.addEventListener('click', (e) => {
        e.preventDefault();
        const imgUrl = OPERATOR_EMOJI_APPLE_CDN + operatorEmojiToCodepoint(emoji) + '.png';
        const img = document.createElement('img');
        img.src = imgUrl;
        img.alt = emoji;
        img.className = 'messenger-operator-emoji-inline';
        img.setAttribute('data-emoji-char', emoji);
        img.style.width = '20px';
        img.style.height = '20px';
        img.style.verticalAlign = 'middle';
        img.style.display = 'inline-block';
        img.onerror = function() { this.style.display = 'none'; this.outerHTML = emoji; };
        
        const selection = window.getSelection();
        if (selection.rangeCount > 0) {
          const range = selection.getRangeAt(0);
          range.deleteContents();
          range.insertNode(img);
          range.collapse(false);
          selection.removeAllRanges();
          selection.addRange(range);
        } else {
          messageBody.appendChild(img);
        }
        messageBody.focus();
        pickerEl.classList.add('messenger-operator-emoji-picker-hidden');
        this.updateOperatorInputHeight(messageBody);
      });
      pickerEl.appendChild(span);
    });
    emojiBtn.addEventListener('click', (e) => {
      e.preventDefault();
      const hidden = pickerEl.classList.toggle('messenger-operator-emoji-picker-hidden');
      if (!hidden && this.operatorEmojiPickerCloseHandler) {
        document.removeEventListener('click', this.operatorEmojiPickerCloseHandler);
        this.operatorEmojiPickerCloseHandler = null;
      }
      if (!hidden) {
        this.operatorEmojiPickerCloseHandler = (ev) => {
          if (!pickerEl.contains(ev.target) && ev.target !== emojiBtn && !emojiBtn.contains(ev.target)) {
            pickerEl.classList.add('messenger-operator-emoji-picker-hidden');
            document.removeEventListener('click', this.operatorEmojiPickerCloseHandler);
            this.operatorEmojiPickerCloseHandler = null;
          }
        };
        setTimeout(() => document.addEventListener('click', this.operatorEmojiPickerCloseHandler), 0);
      }
    });
  }

  /**
   * Инициализация авто-роста поля ввода сообщения и обработка вставки изображений через Ctrl+V
   */
  initMessageInputAutogrow(textarea) {
    if (!textarea || !textarea.classList.contains('messenger-input-autogrow')) return;
    if (textarea.getAttribute('data-messenger-autogrow-init')) return;
    textarea.setAttribute('data-messenger-autogrow-init', '1');
    
    const form = textarea.closest('form');
    if (!form) return;
    const fileInput = form.querySelector('input[name="attachments"]');
    
    // Функция авто-роста
    function autogrow() {
      textarea.style.height = 'auto';
      textarea.style.height = textarea.scrollHeight + 'px';
    }
    
    textarea.addEventListener('input', autogrow);
    autogrow(); // Инициализация при загрузке
    
    // Обработка вставки изображений через Ctrl+V
    if (fileInput) {
      textarea.addEventListener('paste', function(e) {
        const items = e.clipboardData && e.clipboardData.items;
        if (!items) return;
        
        const toAdd = [];
        for (let i = 0; i < items.length; i++) {
          if (items[i].type && items[i].type.indexOf('image/') === 0) {
            const f = items[i].getAsFile();
            if (f) toAdd.push(f);
          }
        }
        
        if (toAdd.length === 0) return;
        e.preventDefault();
        
        // Убеждаемся, что у файлов есть имена
        for (let k = 0; k < toAdd.length; k++) {
          const f = toAdd[k];
          if (!f.name || !String(f.name).trim()) {
            toAdd[k] = new File([f], 'image.png', { type: f.type || 'image/png' });
          }
        }
        
        // Добавляем файлы в file input через DataTransfer
        if (typeof DataTransfer !== 'undefined') {
          const dt = new DataTransfer();
          // Сохраняем существующие файлы
          for (let j = 0; j < (fileInput.files || []).length; j++) {
            dt.items.add(fileInput.files[j]);
          }
          // Добавляем новые файлы
          for (let k = 0; k < toAdd.length; k++) {
            dt.items.add(toAdd[k]);
          }
          fileInput.files = dt.files;
          fileInput.dispatchEvent(new Event('change', { bubbles: true }));
        }
      });
    }
  }

  // =========================================================================
  // Canned Responses — загрузка и "/" триггер (как в Chatwoot)
  // =========================================================================

  async loadCannedResponses() {
    try {
      const response = await fetch('/api/canned-responses/', {
        credentials: 'same-origin',
        headers: { 'Accept': 'application/json' },
      });
      if (response.ok) {
        const data = await response.json();
        this._cannedResponses = Array.isArray(data) ? data : (data.results || []);
      }
    } catch (e) {
      // не критично
    }
  }

  // Plan 2 Task 11 — быстрые кнопки ответов над полем ввода
  async loadQuickReplies() {
    try {
      const response = await fetch('/api/canned-responses/?quick=1', {
        credentials: 'same-origin',
        headers: { 'Accept': 'application/json' },
      });
      if (response.ok) {
        const data = await response.json();
        this._quickReplies = Array.isArray(data) ? data : (data.results || []);
      } else {
        this._quickReplies = [];
      }
    } catch (e) {
      this._quickReplies = [];
    }
    this.renderQuickReplies();
  }

  renderQuickReplies() {
    const row = document.getElementById('quickRepliesRow');
    if (!row) return;
    row.innerHTML = '';
    const items = (this._quickReplies || []).slice(0, 8);
    if (!items.length || this.composeMode === 'INTERNAL') {
      row.classList.add('hidden');
      return;
    }
    items.forEach((cr) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'text-xs px-3 py-1 rounded-full bg-brand-soft/50 hover:bg-brand-soft text-brand-teal border border-brand-teal/30';
      btn.title = cr.body || '';
      btn.textContent = cr.title || '';
      btn.addEventListener('click', () => this.insertQuickReply(cr));
      row.appendChild(btn);
    });
    row.classList.remove('hidden');
  }

  insertQuickReply(cr) {
    const input = document.getElementById('messageBody');
    if (!input || !cr) return;
    const body = cr.body || '';
    const current = (input.innerText || '').trim();
    input.focus();
    let ok = false;
    try {
      if (!current) {
        input.innerText = '';
        ok = document.execCommand('insertText', false, body);
      } else {
        ok = document.execCommand('insertText', false, ' ' + body);
      }
    } catch (e) {
      ok = false;
    }
    if (!ok) {
      input.innerText = current ? (current + ' ' + body) : body;
    }
    // Каретка в конец
    try {
      const range = document.createRange();
      range.selectNodeContents(input);
      range.collapse(false);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
    } catch (e) { /* noop */ }
    // Триггер input — автосейв черновика и пересчёт высоты
    input.dispatchEvent(new Event('input', { bubbles: true }));
  }

  initCannedResponsesTrigger() {
    // Отслеживаем ввод "/" в поле сообщения
    document.addEventListener('input', (e) => {
      const el = e.target;
      if (!el || el.id !== 'messageBody') return;
      const text = el.innerText || '';
      if (text.startsWith('/')) {
        this._cannedFilterText = text.slice(1).toLowerCase();
        this.showCannedDropdown();
      } else {
        this.hideCannedDropdown();
      }
    });
    document.addEventListener('keydown', (e) => {
      if (!this._cannedDropdownVisible) return;
      const dropdown = document.getElementById('cannedResponseDropdown');
      if (!dropdown) return;
      if (e.key === 'Escape') {
        e.preventDefault();
        this.hideCannedDropdown();
        return;
      }
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        const items = dropdown.querySelectorAll('[data-canned-id]');
        if (!items.length) return;
        let activeIdx = -1;
        items.forEach((item, idx) => { if (item.classList.contains('bg-brand-teal/10')) activeIdx = idx; });
        items.forEach(i => i.classList.remove('bg-brand-teal/10'));
        if (e.key === 'ArrowDown') activeIdx = (activeIdx + 1) % items.length;
        else activeIdx = activeIdx <= 0 ? items.length - 1 : activeIdx - 1;
        items[activeIdx].classList.add('bg-brand-teal/10');
        items[activeIdx].scrollIntoView({ block: 'nearest' });
        return;
      }
      if (e.key === 'Enter') {
        const active = dropdown.querySelector('[data-canned-id].bg-brand-teal\\/10');
        if (active) {
          e.preventDefault();
          this.applyCannedResponse(active.getAttribute('data-canned-id'));
        }
      }
    });
  }

  showCannedDropdown() {
    const input = document.getElementById('messageBody');
    if (!input) return;
    let dropdown = document.getElementById('cannedResponseDropdown');
    if (!dropdown) {
      dropdown = document.createElement('div');
      dropdown.id = 'cannedResponseDropdown';
      dropdown.className = 'absolute bottom-full left-0 right-0 mb-1 bg-white border border-brand-soft/80 rounded-xl shadow-lg max-h-48 overflow-y-auto z-50';
      dropdown.style.display = 'none';
      const row = input.closest('.messenger-operator-form-row') || input.parentElement;
      if (row) { row.style.position = 'relative'; row.appendChild(dropdown); }
    }
    const filter = this._cannedFilterText;
    const filtered = this._cannedResponses.filter(cr => {
      const t = (cr.title || '').toLowerCase();
      const b = (cr.body || '').toLowerCase();
      return !filter || t.includes(filter) || b.includes(filter);
    });
    if (filtered.length === 0) {
      dropdown.innerHTML = '<div class="px-3 py-2 text-xs text-brand-dark/50">Шаблоны не найдены</div>';
    } else {
      dropdown.innerHTML = filtered.map((cr, idx) => `
        <div data-canned-id="${cr.id}" class="px-3 py-2 cursor-pointer hover:bg-brand-teal/10 transition ${idx === 0 ? 'bg-brand-teal/10' : ''}">
          <div class="text-sm font-medium text-brand-dark">${this.escapeHtml(cr.title)}</div>
          <div class="text-xs text-brand-dark/60 truncate">${this.escapeHtml((cr.body || '').slice(0, 80))}</div>
        </div>
      `).join('');
    }
    dropdown.style.display = '';
    this._cannedDropdownVisible = true;
    dropdown.querySelectorAll('[data-canned-id]').forEach(item => {
      item.addEventListener('click', () => this.applyCannedResponse(item.getAttribute('data-canned-id')));
    });
  }

  hideCannedDropdown() {
    const dropdown = document.getElementById('cannedResponseDropdown');
    if (dropdown) dropdown.style.display = 'none';
    this._cannedDropdownVisible = false;
  }

  applyCannedResponse(id) {
    const cr = this._cannedResponses.find(r => String(r.id) === String(id));
    if (!cr) return;
    const input = document.getElementById('messageBody');
    if (input) {
      input.innerText = cr.body;
      input.focus();
      // Поставить каретку в конец
      const range = document.createRange();
      range.selectNodeContents(input);
      range.collapse(false);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
    }
    this.hideCannedDropdown();
  }

  // =========================================================================
  // Skeleton Loaders (как в Chatwoot / JivoChat)
  // =========================================================================

  renderSkeletonList() {
    const shimmer = 'animate-pulse bg-brand-soft/40 rounded';
    let html = '';
    for (let i = 0; i < 8; i++) {
      html += `
        <div class="flex items-start gap-3 px-3 py-3 border-b border-brand-soft/30">
          <div class="w-10 h-10 rounded-full ${shimmer} flex-shrink-0"></div>
          <div class="flex-1 space-y-2">
            <div class="flex items-center justify-between">
              <div class="h-3.5 ${shimmer}" style="width:${60 + Math.random() * 40}%"></div>
              <div class="h-3 ${shimmer} w-10"></div>
            </div>
            <div class="h-3 ${shimmer}" style="width:${50 + Math.random() * 30}%"></div>
          </div>
        </div>
      `;
    }
    return html;
  }

  renderSkeletonMessages() {
    const shimmer = 'animate-pulse bg-brand-soft/40 rounded';
    let html = '<div class="space-y-4 p-4">';
    const patterns = ['left', 'left', 'right', 'left', 'right', 'right', 'left'];
    patterns.forEach(side => {
      const w = 40 + Math.random() * 35;
      html += `
        <div class="flex ${side === 'right' ? 'justify-end' : ''}">
          <div class="${shimmer}" style="width:${w}%;height:${32 + Math.random() * 24}px;border-radius:16px;"></div>
        </div>
      `;
    });
    html += '</div>';
    return html;
  }

  // =========================================================================
  // Drag-and-drop файлов в зону чата
  // =========================================================================

  initDragDrop() {
    const mainArea = document.querySelector('.messenger-unified-main');
    if (!mainArea) return;

    mainArea.addEventListener('dragenter', (e) => {
      e.preventDefault();
      this._dragCounter++;
      this.showDragOverlay();
    });
    mainArea.addEventListener('dragleave', (e) => {
      e.preventDefault();
      this._dragCounter--;
      if (this._dragCounter <= 0) {
        this._dragCounter = 0;
        this.hideDragOverlay();
      }
    });
    mainArea.addEventListener('dragover', (e) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'copy';
    });
    mainArea.addEventListener('drop', (e) => {
      e.preventDefault();
      this._dragCounter = 0;
      this.hideDragOverlay();
      if (!this.currentConversationId) return;
      const files = e.dataTransfer.files;
      if (!files || !files.length) return;
      const fileInput = document.getElementById('messageAttachments');
      if (fileInput) {
        const dt = new DataTransfer();
        Array.from(files).forEach(f => dt.items.add(f));
        fileInput.files = dt.files;
        fileInput.dispatchEvent(new Event('change', { bubbles: true }));
      }
    });
  }

  showDragOverlay() {
    if (!this.currentConversationId || !window.MESSENGER_CAN_REPLY) return;
    let overlay = document.getElementById('messengerDragOverlay');
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.id = 'messengerDragOverlay';
      overlay.className = 'absolute inset-0 z-40 flex items-center justify-center pointer-events-none';
      overlay.innerHTML = `
        <div class="bg-brand-teal/10 border-2 border-dashed border-brand-teal rounded-2xl p-8 text-center backdrop-blur-sm">
          <svg class="w-12 h-12 mx-auto mb-3 text-brand-teal" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"/>
          </svg>
          <div class="text-sm font-medium text-brand-teal">Перетащите файлы сюда</div>
          <div class="text-xs text-brand-dark/50 mt-1">Изображения и PDF, до 5 МБ</div>
        </div>
      `;
      const main = document.querySelector('.messenger-unified-main');
      if (main) { main.style.position = 'relative'; main.appendChild(overlay); }
    }
    overlay.style.display = '';
  }

  hideDragOverlay() {
    const overlay = document.getElementById('messengerDragOverlay');
    if (overlay) overlay.style.display = 'none';
  }

  // =========================================================================
  // Улучшенный звук уведомления (двухтональный chime)
  // =========================================================================

  playIncomingSoundV2() {
    const Ctor = window.AudioContext || window.webkitAudioContext;
    if (!Ctor) return;
    try {
      const ctx = new Ctor();
      const now = ctx.currentTime;

      // Нота 1: C6 (1046 Hz)
      const osc1 = ctx.createOscillator();
      const gain1 = ctx.createGain();
      osc1.type = 'sine';
      osc1.frequency.value = 1046;
      gain1.gain.setValueAtTime(0.0001, now);
      gain1.gain.exponentialRampToValueAtTime(0.35, now + 0.02);
      gain1.gain.exponentialRampToValueAtTime(0.0001, now + 0.35);
      osc1.connect(gain1);
      gain1.connect(ctx.destination);
      osc1.start(now);
      osc1.stop(now + 0.4);

      // Нота 2: E6 (1318 Hz), задержка 0.12s
      const osc2 = ctx.createOscillator();
      const gain2 = ctx.createGain();
      osc2.type = 'sine';
      osc2.frequency.value = 1318;
      gain2.gain.setValueAtTime(0.0001, now + 0.12);
      gain2.gain.exponentialRampToValueAtTime(0.30, now + 0.14);
      gain2.gain.exponentialRampToValueAtTime(0.0001, now + 0.5);
      osc2.connect(gain2);
      gain2.connect(ctx.destination);
      osc2.start(now + 0.12);
      osc2.stop(now + 0.55);

      // Нота 3: G6 (1568 Hz), задержка 0.24s — мажорный аккорд
      const osc3 = ctx.createOscillator();
      const gain3 = ctx.createGain();
      osc3.type = 'sine';
      osc3.frequency.value = 1568;
      gain3.gain.setValueAtTime(0.0001, now + 0.24);
      gain3.gain.exponentialRampToValueAtTime(0.25, now + 0.26);
      gain3.gain.exponentialRampToValueAtTime(0.0001, now + 0.65);
      osc3.connect(gain3);
      gain3.connect(ctx.destination);
      osc3.start(now + 0.24);
      osc3.stop(now + 0.7);

      // Закрыть контекст после завершения
      setTimeout(() => { try { ctx.close(); } catch(_) {} }, 1000);
    } catch (e) {
      // fallback: без звука
    }
  }

  /**
   * Получить CSRF токен
   */
  getCsrfToken() {
    const cookieMatch = document.cookie.match(/csrftoken=([^;]+)/);
    return cookieMatch ? cookieMatch[1] : '';
  }

  /**
   * Экранировать HTML
   */
  escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  renderMessageBodyWithEmojis(text) {
    let html = this.escapeHtml(text || '');
    // Замена Unicode эмодзи на картинки Apple
    html = html.replace(/[\u{1F300}-\u{1F9FF}]|[\u{2600}-\u{26FF}]|[\u{2700}-\u{27BF}]/gu, (emoji) => {
      const codepoint = operatorEmojiToCodepoint(emoji);
      const imgUrl = OPERATOR_EMOJI_APPLE_CDN + codepoint + '.png';
      return `<img src="${imgUrl}" alt="${emoji}" class="messenger-operator-emoji-inline" style="width:18px;height:18px;vertical-align:middle;display:inline-block;margin:0 1px;">`;
    });
    return html;
  }

  updateOperatorInputHeight(element) {
    if (!element || element.tagName !== 'DIV') return;
    element.style.height = 'auto';
    element.style.height = Math.min(element.scrollHeight, 120) + 'px';
  }

  // ── Bulk Actions ──────────────────────────────────────────────

  initBulkActions() {
    // Inject bulk action bar into the left column header
    const listCol = document.querySelector('.messenger-col-left');
    if (!listCol) return;

    const bar = document.createElement('div');
    bar.id = 'bulk-action-bar';
    bar.className = 'bulk-action-bar hidden';
    bar.innerHTML = `
      <div class="bulk-bar-info">
        <button type="button" class="bulk-bar-close" onclick="window.MessengerPanel.exitBulkMode()" title="Отмена">&times;</button>
        <span class="bulk-bar-count">0 выбрано</span>
      </div>
      <div class="bulk-bar-actions">
        <button type="button" class="bulk-btn bulk-btn-close" onclick="window.MessengerPanel.bulkAction('close')" title="Закрыть выбранные">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6L6 18M6 6l12 12"/></svg>
          Закрыть
        </button>
        <button type="button" class="bulk-btn bulk-btn-reopen" onclick="window.MessengerPanel.bulkAction('reopen')" title="Переоткрыть">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 12a9 9 0 019-9 9 9 0 010 18 9 9 0 01-9-9z"/><path d="M12 8v4l3 3"/></svg>
          Открыть
        </button>
      </div>
    `;
    listCol.insertBefore(bar, listCol.firstChild);

    // Long-press / right-click to enter bulk mode on conversation cards
    const listContainer = document.getElementById('conversation-list');
    if (listContainer) {
      listContainer.addEventListener('contextmenu', (e) => {
        const card = e.target.closest('.conversation-card');
        if (!card) return;
        e.preventDefault();
        const id = parseInt(card.dataset.conversationId);
        if (!this._bulkMode) {
          this._bulkMode = true;
          this._bulkSelected.clear();
        }
        this._bulkSelected.add(id);
        this.updateBulkUI();
        this.refreshListUI();
      });
    }
  }

  toggleBulkSelect(conversationId, checked) {
    if (checked) {
      this._bulkSelected.add(conversationId);
    } else {
      this._bulkSelected.delete(conversationId);
    }
    this.updateBulkUI();
    // Update card highlight
    const card = document.querySelector(`.conversation-card[data-conversation-id="${conversationId}"]`);
    if (card) card.classList.toggle('bulk-selected', checked);
  }

  updateBulkUI() {
    const bar = document.getElementById('bulk-action-bar');
    if (!bar) return;
    const count = this._bulkSelected.size;
    if (this._bulkMode && count > 0) {
      bar.classList.remove('hidden');
      bar.querySelector('.bulk-bar-count').textContent = `${count} выбрано`;
    } else if (this._bulkMode && count === 0) {
      // Keep bar visible but show 0
      bar.classList.remove('hidden');
      bar.querySelector('.bulk-bar-count').textContent = '0 выбрано';
    } else {
      bar.classList.add('hidden');
    }
  }

  exitBulkMode() {
    this._bulkMode = false;
    this._bulkSelected.clear();
    this.updateBulkUI();
    this.refreshListUI();
  }

  refreshListUI() {
    // Re-render the conversation list to add/remove checkboxes
    const listContainer = document.getElementById('conversation-list');
    if (!listContainer) return;
    const cards = listContainer.querySelectorAll('.conversation-card');
    cards.forEach(card => {
      const id = parseInt(card.dataset.conversationId);
      const existing = card.querySelector('.conversation-bulk-checkbox');
      if (this._bulkMode && !existing) {
        const label = document.createElement('label');
        label.className = 'conversation-bulk-checkbox';
        label.onclick = (e) => e.stopPropagation();
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.checked = this._bulkSelected.has(id);
        cb.onchange = () => this.toggleBulkSelect(id, cb.checked);
        label.appendChild(cb);
        card.insertBefore(label, card.firstChild);
      } else if (!this._bulkMode && existing) {
        existing.remove();
      } else if (existing) {
        existing.querySelector('input').checked = this._bulkSelected.has(id);
      }
      card.classList.toggle('bulk-selected', this._bulkSelected.has(id));
    });
  }

  async bulkAction(actionType) {
    const ids = Array.from(this._bulkSelected);
    if (ids.length === 0) return;

    const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]')?.value
      || document.cookie.split('; ').find(c => c.startsWith('csrftoken='))?.split('=')[1]
      || '';

    try {
      const resp = await this._fetch('/api/conversations/bulk/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
        body: JSON.stringify({ ids, action: actionType }),
      });
      const data = await resp.json();
      if (resp.ok) {
        this.showToast(`Обновлено: ${data.updated} диалогов`, 'success');
        this.exitBulkMode();
        // Refresh list
        this.fetchConversations();
      } else {
        this.showToast(data.detail || 'Ошибка', 'error');
      }
    } catch (err) {
      if (err.name !== 'AbortError') {
        this.showToast('Ошибка сети', 'error');
      }
    }
  }

  /**
   * Plan 3 Task 9: подсветить карточку диалога в списке (при эскалации/нотификации).
   * Добавляет красное кольцо на 3 секунды.
   */
  highlightConversation(convId) {
    if (!convId) return;
    const el = document.querySelector(`[data-conversation-id="${convId}"]`);
    if (!el) return;
    el.classList.add('ring-2', 'ring-red-500');
    setTimeout(() => el.classList.remove('ring-2', 'ring-red-500'), 3000);
  }
}

// Глобальный экземпляр
const panel = new MessengerOperatorPanel();

// Экспорт для использования в других скриптах
window.MessengerPanel = panel;
