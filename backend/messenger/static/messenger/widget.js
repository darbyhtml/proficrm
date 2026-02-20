/**
 * Messenger Widget - минимальный production-safe JS клиент для Widget API
 * 
 * Использование:
 * <script src="/static/messenger/widget.js" data-widget-token="YOUR_TOKEN"></script>
 */
(function() {
  'use strict';

  // Конфигурация
  const CONFIG = {
    POLL_INTERVAL: 3000, // 3 секунды
    API_BASE_URL: '', // Относительный путь (текущий домен)
    MAX_MESSAGE_LENGTH: 2000,
    STORAGE_PREFIX: 'messenger_widget::',
  };

  /**
   * Генерация UUIDv4
   */
  function generateUUID() {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
      const r = Math.random() * 16 | 0;
      const v = c === 'x' ? r : (r & 0x3 | 0x8);
      return v.toString(16);
    });
  }

  /**
   * Класс MessengerWidget
   */
  class MessengerWidget {
    constructor(widgetToken) {
      if (!widgetToken) {
        console.error('[MessengerWidget] widget_token is required');
        return;
      }

      this.widgetToken = widgetToken;
      this.sessionToken = null;
      this.sinceId = null;
      this.contactId = null;
      this.pollInterval = null; // legacy name; now we use pollTimer (setTimeout)
      this.pollTimer = null;
      this.pollInFlight = false;
      this.pollBackoffMs = 0;
      this.lastPoll429LogAt = 0;
      this.eventSource = null;
      this.sseReconnectDelayMs = 1000;  // Начальная задержка переподключения SSE
      this.sseReconnectTimer = null;
      this.sseReconnectAttempts = 0;
      this.isOpen = false;
      this.isSending = false;
      this.receivedMessageIds = new Set(); // Anti-duplicate: Set для отслеживания полученных сообщений
      this.typingSendTimer = null;
      this.offlineMode = false;
      this.offlineMessage = '';
      this.initialMessages = [];
      this.ratingRequested = false;
      this.ratingType = 'stars';
      this.ratingMaxScore = 5;
      this.title = 'Чат с поддержкой';
      this.greeting = '';
      this.color = '#01948E';
      this.unreadCount = 0;
      this.privacyUrl = '';
      this.privacyText = '';
      this.captchaRequired = false;
      this.captchaToken = '';
      this.captchaQuestion = '';
      this.sseEnabled = true;
      this.attachmentsEnabled = true;
      this.maxFileSizeBytes = 5 * 1024 * 1024;
      this.allowedContentTypes = ['image/jpeg', 'image/png', 'image/gif', 'image/webp', 'application/pdf'];
      this.pendingFiles = [];
      this.fileInput = null;
      this.pendingFilesEl = null;
      this.markReadTimer = null;

      // DOM элементы (будут созданы в render)
      this.button = null;
      this.offlineBanner = null;
      this.ratingBlock = null;
      this.ratingForm = null;
      this.popup = null;
      this.messagesContainer = null;
      this.typingIndicator = null;
      this.input = null;
      this.sendButton = null;
      this.closeButton = null;
      this.captchaRow = null;
      this.captchaInput = null;
    }

    /**
     * Экранирование HTML
     */
    escapeHtml(str) {
      return String(str || '').replace(/[&<>"']/g, function(ch) {
        switch (ch) {
          case '&': return '&amp;';
          case '<': return '&lt;';
          case '>': return '&gt;';
          case '"': return '&quot;';
          case "'": return '&#39;';
          default: return ch;
        }
      });
    }

    /**
     * Рендер простого Markdown-подобного форматирования:
     * - **жирный**;
     * - ссылки http(s)://...;
     * - переводы строк.
     * HTML всегда предварительно экранируется.
     */
    renderFormattedBody(text) {
      let html = this.escapeHtml(text || '');
      // **bold**
      html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
      // ссылки
      html = html.replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>');
      // переводы строк
      html = html.replace(/\r\n/g, '\n').replace(/\n/g, '<br>');
      return html;
    }

    /**
     * Получить ключ для localStorage с namespace по widget_token
     */
    _storageKey(key) {
      return CONFIG.STORAGE_PREFIX + this.widgetToken + '::' + key;
    }

    /**
     * Загрузить данные из localStorage
     */
    loadFromStorage() {
      try {
        this.sessionToken = localStorage.getItem(this._storageKey('session_token'));
        const sinceIdStr = localStorage.getItem(this._storageKey('since_id'));
        this.sinceId = sinceIdStr ? parseInt(sinceIdStr, 10) : null;
        this.contactId = localStorage.getItem(this._storageKey('contact_id'));
        this.prechatSubmitted = localStorage.getItem(this._storageKey('prechat_done')) === '1';
        if (localStorage.getItem(this._storageKey('prechat_required')) === '1') {
          this.prechatRequired = true;
        }
        const whDisplay = localStorage.getItem(this._storageKey('working_hours_display'));
        if (whDisplay) this.workingHoursDisplay = whDisplay;

        // Если нет contact_id - генерируем и сохраняем
        if (!this.contactId) {
          this.contactId = generateUUID();
          localStorage.setItem(this._storageKey('contact_id'), this.contactId);
        }
      } catch (e) {
        console.error('[MessengerWidget] Error loading from storage:', e);
      }
    }

    /**
     * Сохранить данные в localStorage
     */
    saveToStorage() {
      try {
        if (this.sessionToken) {
          localStorage.setItem(this._storageKey('session_token'), this.sessionToken);
        }
        if (this.sinceId !== null) {
          localStorage.setItem(this._storageKey('since_id'), String(this.sinceId));
        }
        if (this.contactId) {
          localStorage.setItem(this._storageKey('contact_id'), this.contactId);
        }
        if (this.prechatRequired !== undefined) {
          localStorage.setItem(this._storageKey('prechat_required'), this.prechatRequired ? '1' : '0');
        }
        if (this.workingHoursDisplay) {
          localStorage.setItem(this._storageKey('working_hours_display'), this.workingHoursDisplay);
        }
      } catch (e) {
        console.error('[MessengerWidget] Error saving to storage:', e);
      }
    }

    /**
     * Очистить данные из localStorage
     */
    clearStorage() {
      try {
        localStorage.removeItem(this._storageKey('session_token'));
        localStorage.removeItem(this._storageKey('since_id'));
        // contact_id НЕ удаляем - он должен быть стабильным
      } catch (e) {
        console.error('[MessengerWidget] Error clearing storage:', e);
      }
    }

    /**
     * Инициализация виджета
     */
    async init() {
      // Загрузить сохранённые данные
      this.loadFromStorage();

      // Если нет сессии - bootstrap
      if (!this.sessionToken) {
        const success = await this.bootstrap();
        if (!success) {
          // Bootstrap не удался (404) - виджет не активируется
          return;
        }
      }

      // Рендерить UI
      this.render();

      // Реалтайм (SSE) с fallback на poll
      if (!this.sseEnabled || !this.startRealtime()) {
        this.startPolling();
      }
    }

    /**
     * Bootstrap: создание/получение сессии виджета
     */
    async bootstrap() {
      try {
        const response = await fetch(CONFIG.API_BASE_URL + '/api/widget/bootstrap/', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            widget_token: this.widgetToken,
            contact_external_id: this.contactId,
          }),
        });

        if (response.status === 404) {
          // Invalid/inactive inbox - виджет не активируется
          console.warn('[MessengerWidget] Bootstrap failed: invalid widget_token or inactive inbox');
          return false;
        }

        if (!response.ok) {
          console.error('[MessengerWidget] Bootstrap failed:', response.status, response.statusText);
          return false;
        }

        const data = await response.json();
        this.sessionToken = data.widget_session_token;
        this.sinceId = null; // Сбросить since_id при bootstrap
        this.offlineMode = data.offline_mode === true;
        this.offlineMessage = data.offline_message || '';
        this.workingHoursDisplay = data.working_hours_display || '';
        this.title = data.title || 'Чат с поддержкой';
        this.greeting = data.greeting || '';
        this.color = data.color || '#01948E';
        this.privacyUrl = data.privacy_url || '';
        this.privacyText = data.privacy_text || '';
        this.prechatRequired = data.prechat_required === true;
        this.captchaRequired = data.captcha_required === true;
        this.captchaToken = data.captcha_token || '';
        this.captchaQuestion = data.captcha_question || '';
        if (data.sse_enabled !== undefined) this.sseEnabled = !!data.sse_enabled;
        if (data.attachments_enabled !== undefined) this.attachmentsEnabled = !!data.attachments_enabled;
        if (typeof data.max_file_size_bytes === 'number') this.maxFileSizeBytes = data.max_file_size_bytes;
        if (Array.isArray(data.allowed_content_types)) this.allowedContentTypes = data.allowed_content_types;

        // Обработать initial_messages
        if (data.initial_messages && Array.isArray(data.initial_messages)) {
          // Найти максимальный ID для since_id
          let maxId = null;
          for (const msg of data.initial_messages) {
            if (msg.id && (maxId === null || msg.id > maxId)) {
              maxId = msg.id;
            }
            // Добавить в Set для anti-duplicate
            if (msg.id) {
              this.receivedMessageIds.add(msg.id);
            }
          }
          this.sinceId = maxId;
          this.initialMessages = data.initial_messages;
        } else {
          this.initialMessages = [];
        }

        // Сохранить в localStorage
        this.saveToStorage();

        return true;
      } catch (error) {
        console.error('[MessengerWidget] Bootstrap error:', error);
        return false;
      }
    }

    /**
     * Проверка допустимости файла по размеру и типу
     */
    isFileAllowed(file) {
      if (file.size > this.maxFileSizeBytes) return false;
      const ct = (file.type || '').toLowerCase();
      if (!ct) return true;
      for (const allowed of this.allowedContentTypes) {
        const a = allowed.toLowerCase();
        if (a === ct) return true;
        if (a === 'image/*' && ct.indexOf('image/') === 0) return true;
      }
      return false;
    }

    /**
     * Отправка сообщения (текст и/или файлы)
     */
    async sendMessage(body, files) {
      const trimmedBody = (body || '').trim();
      const fileList = files && files.length ? Array.from(files) : [];
      if (!trimmedBody && !fileList.length) {
        return false;
      }
      if (trimmedBody.length > CONFIG.MAX_MESSAGE_LENGTH) {
        console.warn('[MessengerWidget] Message too long, max length:', CONFIG.MAX_MESSAGE_LENGTH);
        return false;
      }

      if (this.isSending) {
        return false;
      }

      if (!this.sessionToken) {
        const success = await this.bootstrap();
        if (!success) return false;
      }

      this.isSending = true;
      this.updateSendButton();

      try {
        let response;
        if (fileList.length > 0 && this.attachmentsEnabled) {
          const formData = new FormData();
          formData.append('widget_token', this.widgetToken);
          formData.append('widget_session_token', this.sessionToken);
          formData.append('body', trimmedBody);
          if (this.captchaRequired && this.captchaToken && this.captchaInput && this.captchaInput.value) {
            formData.append('captcha_token', this.captchaToken);
            formData.append('captcha_answer', this.captchaInput.value.trim());
          }
          fileList.forEach((f, i) => {
            formData.append('files', f);
          });
          response = await fetch(CONFIG.API_BASE_URL + '/api/widget/send/', {
            method: 'POST',
            body: formData,
          });
        } else {
          response = await fetch(CONFIG.API_BASE_URL + '/api/widget/send/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              widget_token: this.widgetToken,
              widget_session_token: this.sessionToken,
              body: trimmedBody,
              captcha_token: (this.captchaRequired ? this.captchaToken : ''),
              captcha_answer: (this.captchaRequired && this.captchaInput ? this.captchaInput.value.trim() : ''),
            }),
          });
        }

        if (response.status === 401) {
          this.clearStorage();
          this.sessionToken = null;
          await this.bootstrap();
          this.isSending = false;
          this.updateSendButton();
          return false;
        }

        if (response.status === 403) {
          this.clearStorage();
          this.sessionToken = null;
          this.stopPolling();
          this.stopRealtime();
          await this.bootstrap();
          this.isSending = false;
          this.updateSendButton();
          return false;
        }

        if (!response.ok) {
          console.error('[MessengerWidget] Send failed:', response.status, response.statusText);
          // Если сервер требует капчу — покажем строку ввода
          try {
            const errData = await response.json();
            if (errData && errData.captcha_required === true) {
              this.captchaRequired = true;
              this.renderCaptchaRow();
            }
          } catch (e) {}
          this.isSending = false;
          this.updateSendButton();
          return false;
        }

        const data = await response.json();
        if (this.captchaInput) this.captchaInput.value = '';
        const attachmentsPayload = Array.isArray(data.attachments) ? data.attachments : [];
        this.addMessageToUI({
          id: data.id,
          body: trimmedBody,
          direction: 'in',
          created_at: data.created_at,
          attachments: attachmentsPayload,
        });

        if (this.input) this.input.value = '';
        this.pendingFiles = [];
        this.renderPendingFiles();
        this.isSending = false;
        this.updateSendButton();
        return true;
      } catch (error) {
        console.error('[MessengerWidget] Send error:', error);
        this.isSending = false;
        this.updateSendButton();
        return false;
      }
    }

    renderCaptchaRow() {
      if (!this.captchaRequired) return;
      if (!this.captchaRow) return;
      this.captchaRow.classList.remove('hidden');
      if (this.captchaQuestion && this.captchaRow.querySelector('.messenger-widget-captcha-q')) {
        this.captchaRow.querySelector('.messenger-widget-captcha-q').textContent = this.captchaQuestion;
      }
    }

    /**
     * Poll: получение новых сообщений
     */
    async poll() {
      if (!this.sessionToken) {
        return { ok: false, status: 0 };
      }
      if (this.pollInFlight) {
        return { ok: false, status: 0 };
      }

      try {
        this.pollInFlight = true;
        const params = new URLSearchParams({
          widget_token: this.widgetToken,
          widget_session_token: this.sessionToken,
        });
        if (this.sinceId !== null) {
          params.append('since_id', String(this.sinceId));
        }

        const response = await fetch(CONFIG.API_BASE_URL + '/api/widget/poll/?' + params.toString(), {
          method: 'GET',
          headers: {
            'Content-Type': 'application/json',
          },
        });

        if (response.status === 401) {
          // Expired session - re-bootstrap и повторить poll
          this.clearStorage();
          this.sessionToken = null;
          const success = await this.bootstrap();
          if (success) {
            // Повторить poll после re-bootstrap
            setTimeout(() => this.poll(), 100);
          }
          return { ok: false, status: 401 };
        }

        if (response.status === 403) {
          // Mismatch inbox - re-bootstrap и stop
          this.clearStorage();
          this.sessionToken = null;
          this.stopPolling();
          this.stopRealtime();
          await this.bootstrap();
          return { ok: false, status: 403 };
        }

        if (!response.ok) {
          if (response.status === 429) {
            const now = Date.now();
            if (!this.lastPoll429LogAt || now - this.lastPoll429LogAt > 60000) {
              this.lastPoll429LogAt = now;
              console.warn('[MessengerWidget] Poll throttled (429). Backing off.');
            }
            return { ok: false, status: 429 };
          }
          console.error('[MessengerWidget] Poll failed:', response.status, response.statusText);
          return { ok: false, status: response.status };
        }

        const data = await response.json();
        if (data.operator_typing !== undefined) {
          this.setOperatorTypingVisible(data.operator_typing === true);
        }
        if (data.rating_requested === true) {
          this.ratingRequested = true;
          this.ratingType = data.rating_type || 'stars';
          this.ratingMaxScore = typeof data.rating_max_score === 'number' ? data.rating_max_score : 5;
          this.showRatingBlock();
        }
        if (data.messages && Array.isArray(data.messages)) {
          // Фильтруем дубликаты через Set
          const newMessages = data.messages.filter(msg => {
            if (!msg.id) return false;
            if (this.receivedMessageIds.has(msg.id)) {
              return false; // Уже получено
            }
            this.receivedMessageIds.add(msg.id);
            return true;
          });

          // Обновить since_id
          for (const msg of newMessages) {
            if (msg.id && (this.sinceId === null || msg.id > this.sinceId)) {
              this.sinceId = msg.id;
            }
          }

          // Сохранить since_id
          if (this.sinceId !== null) {
            localStorage.setItem(this._storageKey('since_id'), String(this.sinceId));
          }

          // Добавить сообщения в UI
          for (const msg of newMessages) {
            this.addMessageToUI(msg);
          }

          this.scheduleMarkOutgoingRead();
        }
        return { ok: true, status: 200 };
      } catch (error) {
        console.error('[MessengerWidget] Poll error:', error);
        return { ok: false, status: 0 };
      } finally {
        this.pollInFlight = false;
      }
    }

    /**
     * Начать polling
     */
    startPolling() {
      if (this.pollTimer || this.pollInterval) {
        return; // Уже запущен
      }
      if (!this.sessionToken) {
        return; // Нет сессии
      }
      const tick = async () => {
        if (!this.sessionToken) {
          this.stopPolling();
          return;
        }
        const res = await this.poll();

        // Backoff при 429/сетевых ошибках, чтобы не спамить запросами и консолью
        if (res && res.status === 429) {
          this.pollBackoffMs = Math.min(Math.max(this.pollBackoffMs * 2, 10000), 60000);
        } else if (!res || res.status === 0) {
          this.pollBackoffMs = Math.min(Math.max(this.pollBackoffMs * 2, 5000), 30000);
        } else {
          this.pollBackoffMs = 0;
        }

        const delay = this.pollBackoffMs || CONFIG.POLL_INTERVAL;
        this.pollTimer = setTimeout(() => {
          this.pollTimer = null;
          tick();
        }, delay);
      };

      // Запустить цикл (первый poll сразу)
      tick();
    }

    /**
     * Остановить polling
     */
    stopPolling() {
      if (this.pollInterval) {
        clearInterval(this.pollInterval);
        this.pollInterval = null;
      }
      if (this.pollTimer) {
        clearTimeout(this.pollTimer);
        this.pollTimer = null;
      }
      this.pollBackoffMs = 0;
    }

    startRealtime() {
      if (this.eventSource) return true;
      if (!this.sessionToken) return false;
      if (typeof EventSource === 'undefined') return false;
      
      // Сбросить счётчик попыток при успешном старте
      this.sseReconnectAttempts = 0;
      this.sseReconnectDelayMs = 1000;

      const params = new URLSearchParams({
        widget_token: this.widgetToken,
        widget_session_token: this.sessionToken,
      });
      if (this.sinceId !== null) {
        params.append('since_id', String(this.sinceId));
      }

      try {
        const es = new EventSource(CONFIG.API_BASE_URL + '/api/widget/stream/?' + params.toString());
        this.eventSource = es;

        es.addEventListener('update', (e) => {
          try {
            const data = JSON.parse(e.data || '{}');
            if (data.operator_typing !== undefined) {
              this.setOperatorTypingVisible(data.operator_typing === true);
            }
            if (data.rating_requested === true) {
              this.ratingRequested = true;
              this.ratingType = data.rating_type || 'stars';
              this.ratingMaxScore = typeof data.rating_max_score === 'number' ? data.rating_max_score : 5;
              this.showRatingBlock();
            }
            if (Array.isArray(data.messages)) {
              const newMessages = data.messages.filter(msg => {
                if (!msg.id) return false;
                if (this.receivedMessageIds.has(msg.id)) return false;
                this.receivedMessageIds.add(msg.id);
                return true;
              });
              for (const msg of newMessages) {
                if (msg.id && (this.sinceId === null || msg.id > this.sinceId)) {
                  this.sinceId = msg.id;
                }
                this.addMessageToUI(msg);
              }
              if (this.sinceId !== null) {
                localStorage.setItem(this._storageKey('since_id'), String(this.sinceId));
              }
              if (newMessages.length > 0) {
                this.scheduleMarkOutgoingRead();
              }
            }
          } catch (err) {
            // ignore
          }
        });

        es.onerror = () => {
          // SSE ошибка: попробуем переподключиться с экспоненциальным backoff
          this.stopRealtime();
          
          // Если слишком много попыток — fallback на poll
          if (this.sseReconnectAttempts >= 5) {
            console.warn('[MessengerWidget] SSE failed after 5 attempts, falling back to poll');
            this.sseReconnectAttempts = 0;
            this.sseReconnectDelayMs = 1000;
            this.startPolling();
            return;
          }
          
          // Экспоненциальный backoff: 1s, 2s, 4s, 8s, 16s
          this.sseReconnectAttempts++;
          const delay = Math.min(this.sseReconnectDelayMs, 16000);
          this.sseReconnectDelayMs *= 2;
          
          this.sseReconnectTimer = setTimeout(() => {
            this.sseReconnectTimer = null;
            // Попробовать переподключиться к SSE
            if (this.sessionToken && !this.eventSource) {
              this.startRealtime();
            } else {
              // Если не получилось — fallback на poll
              this.startPolling();
            }
          }, delay);
        };
        return true;
      } catch (e) {
        this.eventSource = null;
        return false;
      }
    }

    stopRealtime() {
      if (this.eventSource) {
        try { this.eventSource.close(); } catch (e) {}
        this.eventSource = null;
      }
      if (this.sseReconnectTimer) {
        clearTimeout(this.sseReconnectTimer);
        this.sseReconnectTimer = null;
      }
    }

    /**
     * Открыть popup
     */
    open() {
      if (!this.popup) {
        return;
      }
      this.isOpen = true;
      this.unreadCount = 0;
      this.updateBadge && this.updateBadge();
      this.popup.classList.add('messenger-widget-popup-open');
      // Фокус: пре-чат или поле ввода
      if (this.prechatRequired && !this.prechatSubmitted && this.prechatName) {
        setTimeout(() => this.prechatName.focus(), 100);
      } else if (this.input) {
        setTimeout(() => this.input.focus(), 100);
      }
      // Автоскролл вниз
      this.scrollToBottom();
      this.scheduleMarkOutgoingRead();
    }

    /**
     * Закрыть popup
     */
    close() {
      if (!this.popup) {
        return;
      }
      this.isOpen = false;
      this.popup.classList.remove('messenger-widget-popup-open');
    }

    /**
     * Показать пре-чат или чат в зависимости от состояния
     */
    updatePrechatVisibility() {
      if (!this.prechatContainer || !this.chatBody) return;
      const showPrechat = this.prechatRequired && !this.prechatSubmitted;
      if (showPrechat) {
        this.prechatContainer.classList.remove('messenger-widget-prechat-hidden');
        this.chatBody.classList.add('messenger-widget-body-hidden');
      } else {
        this.prechatContainer.classList.add('messenger-widget-prechat-hidden');
        this.chatBody.classList.remove('messenger-widget-body-hidden');
      }
    }

    /**
     * Отправить данные пре-чата и переключиться в режим чата
     */
    async submitPrechat() {
      if (!this.prechatConsent || !this.prechatConsent.checked || !this.sessionToken) return;
      const name = (this.prechatName && this.prechatName.value) ? this.prechatName.value.trim() : '';
      const email = (this.prechatEmail && this.prechatEmail.value) ? this.prechatEmail.value.trim() : '';
      const phone = (this.prechatPhone && this.prechatPhone.value) ? this.prechatPhone.value.trim() : '';
      if (this.prechatSubmitBtn) {
        this.prechatSubmitBtn.disabled = true;
        this.prechatSubmitBtn.textContent = 'Применяем…';
      }
      try {
        const response = await fetch(CONFIG.API_BASE_URL + '/api/widget/contact/', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            widget_token: this.widgetToken,
            widget_session_token: this.sessionToken,
            name: name || undefined,
            email: email || undefined,
            phone: phone || undefined,
          }),
        });
        if (response.ok) {
          this.prechatSubmitted = true;
          try {
            localStorage.setItem(this._storageKey('prechat_done'), '1');
          } catch (e) {}
          this.updatePrechatVisibility();
          if (this.input) setTimeout(() => this.input.focus(), 100);
          this.scrollToBottom();
        } else {
          if (this.prechatSubmitBtn) {
            this.prechatSubmitBtn.disabled = false;
            this.prechatSubmitBtn.textContent = 'Применить и открыть чат';
          }
        }
      } catch (e) {
        if (this.prechatSubmitBtn) {
          this.prechatSubmitBtn.disabled = false;
          this.prechatSubmitBtn.textContent = 'Применить и открыть чат';
        }
      }
    }

    /**
     * Показать/скрыть кнопку запуска виджета (launcher)
     */
    showLauncher() {
      if (this.button) {
        this.button.style.display = '';
      }
    }

    hideLauncher() {
      if (this.button) {
        this.button.style.display = 'none';
      }
    }

    toggle() {
      if (this.isOpen) this.close();
      else this.open();
    }

    /**
     * Скролл вниз ленты сообщений
     */
    scrollToBottom() {
      if (this.messagesContainer) {
        this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
      }
    }

    updateBadge() {
      if (!this.badgeEl) return;
      if (this.unreadCount > 0 && !this.isOpen) {
        this.badgeEl.textContent = this.unreadCount > 9 ? '9+' : String(this.unreadCount);
        this.badgeEl.style.display = 'flex';
      } else {
        this.badgeEl.style.display = 'none';
      }
    }

    /**
     * Обновить состояние кнопки отправки
     */
    updateSendButton() {
      if (!this.sendButton) {
        return;
      }
      if (this.isSending) {
        this.sendButton.disabled = true;
        this.sendButton.textContent = 'Отправка...';
      } else {
        this.sendButton.disabled = false;
        this.sendButton.textContent = 'Отправить';
      }
    }

    /**
     * Добавить сообщение в UI
     */
    addMessageToUI(message) {
      if (!this.messagesContainer) {
        return;
      }

      const messageEl = document.createElement('div');
      messageEl.className = 'messenger-widget-message';
      messageEl.classList.add('messenger-widget-message-' + message.direction);
      if (typeof message.id === 'number') {
        messageEl.setAttribute('data-message-id', String(message.id));
      }

      const bodyEl = document.createElement('div');
      bodyEl.className = 'messenger-widget-message-body';
      bodyEl.innerHTML = this.renderFormattedBody(message.body || '');
      messageEl.appendChild(bodyEl);

      const attachments = message.attachments || [];
      if (attachments.length > 0) {
        const attWrap = document.createElement('div');
        attWrap.className = 'messenger-widget-attachment-cards';
        attachments.forEach(att => {
          const fileUrl = att.url || att.file || '';
          const fileName = att.original_name || fileUrl.split('/').pop() || 'Файл';
          const contentType = (att.content_type || '').toLowerCase();
          const fileExt = fileName.split('.').pop()?.toUpperCase() || '';
          const isImage = contentType.indexOf('image/') === 0 || ['PNG', 'JPG', 'JPEG', 'GIF', 'WEBP'].includes(fileExt);
          const isPdf = contentType === 'application/pdf' || fileExt === 'PDF';
          
          const card = document.createElement('div');
          card.className = 'messenger-widget-attachment-card';
          card.setAttribute('data-open', fileUrl);
          card.setAttribute('data-download', fileUrl);
          card.setAttribute('data-is-image', isImage ? '1' : '0');
          card.setAttribute('data-is-pdf', isPdf ? '1' : '0');
          card.setAttribute('title', fileName);
          card.setAttribute('role', 'button');
          card.setAttribute('tabindex', '0');
          
          const preview = document.createElement('div');
          preview.className = 'messenger-widget-attachment-card__preview';
          
          if (isImage && fileUrl) {
            const img = document.createElement('img');
            img.src = fileUrl;
            img.alt = fileName;
            img.loading = 'lazy';
            preview.appendChild(img);
          } else {
            const icon = document.createElement('div');
            let iconClass = 'file';
            if (isPdf) iconClass = 'pdf';
            else if (['DOC', 'DOCX'].includes(fileExt)) iconClass = 'doc';
            else if (['XLS', 'XLSX'].includes(fileExt)) iconClass = 'xls';
            else if (['PPT', 'PPTX'].includes(fileExt)) iconClass = 'ppt';
            
            icon.className = `messenger-widget-attachment-card__icon messenger-widget-attachment-card__icon--${iconClass}`;
            const iconSvg = {
              pdf: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6zm-1 2 5 5h-5V4zm-2 8v4H9v-4H7v6h10v-6h-2zm-2-2h2v2H9v-2z"/></svg>',
              doc: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6zm4 16H6V4h7v5h5v9zm-3-5H9v2h2v2H9v2h2v-2h2v-2h-2v-2z"/></svg>',
              xls: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6zm1 11h-4v2h4v2h-4v2h2v-1h2v-4h-2v1h-2v-2zm-2-5V4h5l-5 5z"/></svg>',
              ppt: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6zm1 9h-2v4h2v-1h1c.55 0 1-.45 1-1v-1c0-.55-.45-1-1-1h-2v-1zm0-5V4h5l-5 5z"/></svg>',
              file: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6z"/><path d="M14 2v6h6"/></svg>'
            };
            icon.innerHTML = iconSvg[iconClass] || iconSvg.file;
            preview.appendChild(icon);
          }
          
          card.appendChild(preview);
          
          const name = document.createElement('div');
          name.className = 'messenger-widget-attachment-card__name';
          name.textContent = fileName;
          card.appendChild(name);
          
          attWrap.appendChild(card);
        });
        messageEl.appendChild(attWrap);
      }

      const metaEl = document.createElement('div');
      metaEl.className = 'messenger-widget-message-meta';

      const timeEl = document.createElement('span');
      timeEl.className = 'messenger-widget-message-time';
      if (message.created_at) {
        const date = new Date(message.created_at);
        timeEl.textContent = date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
      }
      metaEl.appendChild(timeEl);

      if (message.direction === 'out') {
        const statusEl = document.createElement('span');
        statusEl.className = 'messenger-widget-message-status';
        if (message.read_at) {
          statusEl.textContent = '✓✓';
          statusEl.title = 'Прочитано';
        } else {
          statusEl.textContent = '✓';
          statusEl.title = 'Доставлено';
        }
        metaEl.appendChild(statusEl);
      }

      messageEl.appendChild(metaEl);
      this.messagesContainer.appendChild(messageEl);

      this.scrollToBottom();

      // Непрочитанные: считаем только исходящие сообщения, когда виджет закрыт
      if (message.direction === 'out' && !this.isOpen) {
        this.unreadCount = (this.unreadCount || 0) + 1;
        this.updateBadge();
      }
    }

    markOutgoingMessagesRead() {
      if (!this.sessionToken || !this.messagesContainer) return;
      const outNodes = this.messagesContainer.querySelectorAll('.messenger-widget-message-out[data-message-id]');
      let maxId = null;
      outNodes.forEach(el => {
        const raw = el.getAttribute('data-message-id');
        const id = raw ? parseInt(raw, 10) : NaN;
        if (!isNaN(id) && (maxId === null || id > maxId)) {
          maxId = id;
        }
      });
      if (maxId === null) return;

      fetch(CONFIG.API_BASE_URL + '/api/widget/mark_read/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          widget_token: this.widgetToken,
          widget_session_token: this.sessionToken,
          last_message_id: maxId,
        }),
      }).catch(() => {});
    }

    scheduleMarkOutgoingRead() {
      if (!this.sessionToken) return;
      if (!this.isOpen) return;
      if (this.markReadTimer) {
        clearTimeout(this.markReadTimer);
      }
      this.markReadTimer = setTimeout(() => {
        this.markOutgoingMessagesRead();
      }, 500);
    }

    setOperatorTypingVisible(visible) {
      if (!this.typingIndicator) return;
      if (visible) {
        this.typingIndicator.classList.remove('messenger-widget-typing-hidden');
      } else {
        this.typingIndicator.classList.add('messenger-widget-typing-hidden');
      }
    }

    sendContactTyping() {
      if (!this.sessionToken) return;
      fetch(CONFIG.API_BASE_URL + '/api/widget/typing/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          widget_token: this.widgetToken,
          widget_session_token: this.sessionToken,
        }),
      }).catch(() => {});
    }

    initInputAutogrow() {
      if (!this.input || !this.input.classList.contains('messenger-widget-input-autogrow')) return;
      if (this.input.getAttribute('data-widget-autogrow-init')) return;
      this.input.setAttribute('data-widget-autogrow-init', '1');
      
      // Функция авто-роста
      const autogrow = () => {
        this.input.style.height = 'auto';
        this.input.style.height = this.input.scrollHeight + 'px';
      };
      
      this.input.addEventListener('input', autogrow);
      autogrow(); // Инициализация при загрузке
    }

    openImageModal(url, downloadUrl, title) {
      const u = (url || '').toString().trim();
      if (!u) return;
      // У клиента на сайте открываем в новой вкладке, чтобы не ломать страницу и не перехватывать фокус
      if (typeof window !== 'undefined') {
        window.open(u, '_blank', 'noopener,noreferrer');
      }
    }

    closeImageModal() {
      if (!this.imageModal) return;
      this.imageModal.classList.add('hidden');
      if (this.imageModalImg) {
        this.imageModalImg.classList.add('hidden');
        this.imageModalImg.removeAttribute('src');
        this.imageModalImg.alt = '';
      }
      if (this.imageModalLoader) this.imageModalLoader.style.display = '';
      if (this.imageModalTitle) this.imageModalTitle.textContent = 'Изображение';
      if (this.imageModalOpenLink) this.imageModalOpenLink.setAttribute('href', '#');
      if (this.imageModalDownloadLink) this.imageModalDownloadLink.setAttribute('href', '#');
    }

    renderPendingFiles() {
      if (!this.pendingFilesEl) return;
      this.pendingFilesEl.innerHTML = '';
      this.pendingFiles.forEach((file, index) => {
        const span = document.createElement('span');
        span.className = 'messenger-widget-pending-file';
        span.textContent = file.name || 'Файл';
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'messenger-widget-pending-file-remove';
        remove.textContent = '×';
        remove.addEventListener('click', () => {
          this.pendingFiles.splice(index, 1);
          this.renderPendingFiles();
        });
        span.appendChild(remove);
        this.pendingFilesEl.appendChild(span);
      });
    }

    showRatingBlock() {
      if (!this.ratingBlock) return;
      this.ratingBlock.classList.remove('messenger-widget-rating-hidden');
      if (this.ratingForm) this.ratingForm.classList.add('messenger-widget-rating-hidden');
      this.buildRatingButtons();
    }

    hideRatingBlock() {
      if (this.ratingBlock) this.ratingBlock.classList.add('messenger-widget-rating-hidden');
      if (this.ratingForm) this.ratingForm.classList.remove('messenger-widget-rating-hidden');
      this.ratingRequested = false;
    }

    buildRatingButtons() {
      const wrap = this.ratingBlock.querySelector('.messenger-widget-rating-buttons');
      if (!wrap) return;
      wrap.innerHTML = '';
      const minScore = this.ratingType === 'nps' ? 0 : 1;
      for (let i = minScore; i <= this.ratingMaxScore; i++) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'messenger-widget-rating-btn';
        btn.textContent = this.ratingType === 'stars' ? '★' : i;
        btn.dataset.score = String(i);
        btn.addEventListener('click', () => this.submitRating(i));
        wrap.appendChild(btn);
      }
    }

    async submitRating(score) {
      if (!this.sessionToken) return;
      try {
        const body = { widget_token: this.widgetToken, widget_session_token: this.sessionToken, score: score };
        const commentEl = this.ratingBlock && this.ratingBlock.querySelector('.messenger-widget-rating-comment');
        if (commentEl && commentEl.value && commentEl.value.trim()) {
          body.comment = commentEl.value.trim().slice(0, 2000);
        }
        const r = await fetch(CONFIG.API_BASE_URL + '/api/widget/rate/', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        if (r.ok) this.hideRatingBlock();
      } catch (e) { /* ignore */ }
    }

    /**
     * Рендеринг UI
     */
    render() {
      // Проверить, не создан ли уже виджет
      if (document.getElementById('messenger-widget-container')) {
        return;
      }

      // Создать контейнер
      const container = document.createElement('div');
      container.id = 'messenger-widget-container';

      // Кнопка чата
      this.button = document.createElement('button');
      this.button.className = 'messenger-widget-button';
      this.button.setAttribute('aria-label', 'Открыть чат');
      this.button.innerHTML = `
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
        </svg>
      `;
      if (this.color) {
        this.button.style.backgroundColor = this.color;
      }
      const badge = document.createElement('span');
      badge.className = 'messenger-widget-badge';
      badge.style.display = 'none';
      this.button.appendChild(badge);
      this.badgeEl = badge;
      this.button.addEventListener('click', () => {
        if (this.isOpen) {
          this.close();
        } else {
          this.open();
        }
      });

      // Popup окно
      this.popup = document.createElement('div');
      this.popup.className = 'messenger-widget-popup';

      // Заголовок
      const header = document.createElement('div');
      header.className = 'messenger-widget-header';
      if (this.color) {
        header.style.backgroundColor = this.color;
      }
      const headerText = document.createElement('div');
      headerText.style.display = 'flex';
      headerText.style.flexDirection = 'column';

      const titleSpan = document.createElement('span');
      titleSpan.textContent = this.title || 'Чат с поддержкой';
      headerText.appendChild(titleSpan);

      if (this.greeting) {
        const subtitle = document.createElement('div');
        subtitle.className = 'messenger-widget-header-subtitle';
        subtitle.textContent = this.greeting;
        headerText.appendChild(subtitle);
      }
      if (this.workingHoursDisplay && this.workingHoursDisplay !== 'Обычно отвечаем в течение нескольких минут') {
        const hoursLine = document.createElement('div');
        hoursLine.className = 'messenger-widget-header-hours';
        hoursLine.textContent = this.workingHoursDisplay;
        headerText.appendChild(hoursLine);
      }

      header.appendChild(headerText);

      this.closeButton = document.createElement('button');
      this.closeButton.className = 'messenger-widget-close';
      this.closeButton.setAttribute('aria-label', 'Закрыть');
      this.closeButton.innerHTML = '×';
      this.closeButton.addEventListener('click', () => this.close());

      header.appendChild(this.closeButton);
      this.popup.appendChild(header);

      // Пре-чат: ввод данных и согласие перед началом диалога
      this.prechatContainer = document.createElement('div');
      this.prechatContainer.className = 'messenger-widget-prechat';
      this.prechatContainer.innerHTML = '<div class="messenger-widget-prechat-title">Перед началом диалога</div>';
      const prechatForm = document.createElement('div');
      prechatForm.className = 'messenger-widget-prechat-form';
      const nameLabel = document.createElement('label');
      nameLabel.className = 'messenger-widget-prechat-label';
      nameLabel.textContent = 'Имя';
      this.prechatName = document.createElement('input');
      this.prechatName.type = 'text';
      this.prechatName.className = 'messenger-widget-prechat-input';
      this.prechatName.placeholder = 'Как к вам обращаться?';
      this.prechatName.autocomplete = 'name';
      nameLabel.appendChild(this.prechatName);
      prechatForm.appendChild(nameLabel);
      const emailLabel = document.createElement('label');
      emailLabel.className = 'messenger-widget-prechat-label';
      emailLabel.textContent = 'Email';
      this.prechatEmail = document.createElement('input');
      this.prechatEmail.type = 'email';
      this.prechatEmail.className = 'messenger-widget-prechat-input';
      this.prechatEmail.placeholder = 'example@mail.ru';
      this.prechatEmail.autocomplete = 'email';
      emailLabel.appendChild(this.prechatEmail);
      prechatForm.appendChild(emailLabel);
      const phoneLabel = document.createElement('label');
      phoneLabel.className = 'messenger-widget-prechat-label';
      phoneLabel.textContent = 'Телефон';
      this.prechatPhone = document.createElement('input');
      this.prechatPhone.type = 'tel';
      this.prechatPhone.className = 'messenger-widget-prechat-input';
      this.prechatPhone.placeholder = '+7 (999) 123-45-67';
      this.prechatPhone.autocomplete = 'tel';
      phoneLabel.appendChild(this.prechatPhone);
      prechatForm.appendChild(phoneLabel);
      const consentWrap = document.createElement('div');
      consentWrap.className = 'messenger-widget-prechat-consent';
      this.prechatConsent = document.createElement('input');
      this.prechatConsent.type = 'checkbox';
      this.prechatConsent.id = 'messenger-widget-prechat-consent';
      this.prechatConsent.className = 'messenger-widget-prechat-checkbox';
      const consentLabel = document.createElement('label');
      consentLabel.htmlFor = 'messenger-widget-prechat-consent';
      consentLabel.className = 'messenger-widget-prechat-consent-label';
      consentLabel.innerHTML = (this.privacyText || 'Я согласен с обработкой персональных данных.') + (this.privacyUrl ? ' <a href="' + this.privacyUrl + '" target="_blank" rel="noopener" class="messenger-widget-prechat-link">Политика конфиденциальности</a>' : '');
      consentWrap.appendChild(this.prechatConsent);
      consentWrap.appendChild(consentLabel);
      prechatForm.appendChild(consentWrap);
      this.prechatSubmitBtn = document.createElement('button');
      this.prechatSubmitBtn.type = 'button';
      this.prechatSubmitBtn.className = 'messenger-widget-prechat-submit';
      this.prechatSubmitBtn.textContent = 'Применить и открыть чат';
      this.prechatSubmitBtn.disabled = true;
      const updatePrechatButton = () => {
        this.prechatSubmitBtn.disabled = !this.prechatConsent.checked;
      };
      this.prechatConsent.addEventListener('change', updatePrechatButton);
      this.prechatSubmitBtn.addEventListener('click', () => this.submitPrechat());
      prechatForm.appendChild(this.prechatSubmitBtn);
      this.prechatContainer.appendChild(prechatForm);
      this.popup.appendChild(this.prechatContainer);

      // Обёртка чата (лента + форма)
      this.chatBody = document.createElement('div');
      this.chatBody.className = 'messenger-widget-body';

      // Лента сообщений
      this.messagesContainer = document.createElement('div');
      this.messagesContainer.className = 'messenger-widget-messages';
      this.chatBody.appendChild(this.messagesContainer);

      // Офлайн-баннер (настраиваемое сообщение)
      this.offlineBanner = document.createElement('div');
      this.offlineBanner.className = 'messenger-widget-offline';
      this.offlineBanner.textContent = this.offlineMessage || 'Сейчас никого нет. Оставьте заявку — мы ответим в рабочее время.';
      if (this.offlineMode) {
        this.offlineBanner.classList.remove('messenger-widget-offline-hidden');
      } else {
        this.offlineBanner.classList.add('messenger-widget-offline-hidden');
      }
      this.chatBody.appendChild(this.offlineBanner);

      // Предзагруженные сообщения после bootstrap
      if (this.initialMessages && this.initialMessages.length) {
        for (const msg of this.initialMessages) {
          this.addMessageToUI(msg);
        }
      }
      this.scheduleMarkOutgoingRead();

      // Индикатор «Оператор печатает»
      this.typingIndicator = document.createElement('div');
      this.typingIndicator.className = 'messenger-widget-typing messenger-widget-typing-hidden';
      this.typingIndicator.innerHTML = 'Оператор печатает<span class="messenger-widget-typing-dots"><span class="messenger-widget-typing-dot"></span><span class="messenger-widget-typing-dot"></span><span class="messenger-widget-typing-dot"></span></span>';
      this.chatBody.appendChild(this.typingIndicator);

      // Форма отправки
      const form = document.createElement('div');
      form.className = 'messenger-widget-form';

      // CAPTCHA (показываем только если требуется)
      this.captchaRow = document.createElement('div');
      this.captchaRow.className = 'messenger-widget-captcha hidden';
      this.captchaRow.innerHTML = '<div class="messenger-widget-captcha-q"></div><input class="messenger-widget-captcha-input" placeholder="Ответ" inputmode="numeric">';
      this.captchaInput = this.captchaRow.querySelector('.messenger-widget-captcha-input');
      form.appendChild(this.captchaRow);

      this.pendingFilesEl = document.createElement('div');
      this.pendingFilesEl.className = 'messenger-widget-pending-files';
      form.appendChild(this.pendingFilesEl);

      const inputRow = document.createElement('div');
      inputRow.className = 'messenger-widget-form-row messenger-widget-form-row-modern';

      this.input = document.createElement('textarea');
      this.input.className = 'messenger-widget-input messenger-widget-input-autogrow';
      this.input.placeholder = 'Введите сообщение... (Enter — отправить, Shift+Enter — перенос строки)';
      this.input.rows = 1;
      this.input.maxLength = CONFIG.MAX_MESSAGE_LENGTH;
      this.input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          const body = this.input.value.trim();
          if (body || this.pendingFiles.length > 0) {
            this.sendMessage(body, this.pendingFiles);
          }
        }
      });
      this.input.addEventListener('input', () => {
        clearTimeout(this.typingSendTimer);
        this.typingSendTimer = setTimeout(() => this.sendContactTyping(), 400);
      });
      this.input.addEventListener('paste', (e) => {
        if (!this.attachmentsEnabled) return;
        const items = e.clipboardData && e.clipboardData.items;
        if (!items) return;
        
        // Проверяем изображения из буфера обмена (для авто-роста и вставки)
        const imageItems = [];
        for (let i = 0; i < items.length; i++) {
          if (items[i].type && items[i].type.indexOf('image/') === 0) {
            const file = items[i].getAsFile();
            if (file && this.isFileAllowed(file) && this.pendingFiles.length < 5) {
              imageItems.push(file);
            }
          } else if (items[i].kind === 'file') {
            const file = items[i].getAsFile();
            if (file && this.isFileAllowed(file) && this.pendingFiles.length < 5) {
              this.pendingFiles.push(file);
              this.renderPendingFiles();
              e.preventDefault();
            }
            break;
          }
        }
        
        // Обрабатываем изображения из буфера обмена
        if (imageItems.length > 0) {
          e.preventDefault();
          for (let k = 0; k < imageItems.length; k++) {
            const f = imageItems[k];
            if (!f.name || !String(f.name).trim()) {
              imageItems[k] = new File([f], 'image.png', { type: f.type || 'image/png' });
            }
            this.pendingFiles.push(imageItems[k]);
          }
          this.renderPendingFiles();
        }
      });

      // Инициализация авто-роста поля ввода
      this.initInputAutogrow();

      if (this.attachmentsEnabled) {
        this.fileInput = document.createElement('input');
        this.fileInput.type = 'file';
        this.fileInput.multiple = true;
        this.fileInput.accept = this.allowedContentTypes.join(',');
        this.fileInput.style.display = 'none';
        this.fileInput.addEventListener('change', () => {
          const files = this.fileInput.files;
          if (!files) return;
          for (let i = 0; i < files.length && this.pendingFiles.length < 5; i++) {
            if (this.isFileAllowed(files[i])) {
              this.pendingFiles.push(files[i]);
            }
          }
          this.renderPendingFiles();
          this.fileInput.value = '';
        });
        form.appendChild(this.fileInput);
      }

      inputRow.appendChild(this.input);

      const emojiBtn = document.createElement('button');
      emojiBtn.type = 'button';
      emojiBtn.className = 'messenger-widget-emoji';
      emojiBtn.setAttribute('aria-label', 'Эмодзи');
      emojiBtn.innerHTML = '😊';
      emojiBtn.title = 'Эмодзи (скоро)';
      inputRow.appendChild(emojiBtn);

      const attachBtn = this.attachmentsEnabled ? document.createElement('button') : null;
      if (attachBtn) {
        attachBtn.type = 'button';
        attachBtn.className = 'messenger-widget-attach';
        attachBtn.setAttribute('aria-label', 'Прикрепить файл');
        attachBtn.innerHTML = '📎';
        attachBtn.addEventListener('click', () => {
          if (this.fileInput) this.fileInput.click();
        });
        inputRow.appendChild(attachBtn);
      }

      this.sendButton = document.createElement('button');
      this.sendButton.className = 'messenger-widget-send';
      this.sendButton.textContent = 'Отправить';
      this.sendButton.addEventListener('click', () => {
        const body = this.input.value.trim();
        if (body || this.pendingFiles.length > 0) {
          this.sendMessage(body, this.pendingFiles);
        }
      });
      inputRow.appendChild(this.sendButton);

      form.appendChild(inputRow);
      this.ratingForm = form;
      this.chatBody.appendChild(form);
      this.popup.appendChild(this.chatBody);

      // Модалка для просмотра изображений
      const imgModal = document.createElement('div');
      imgModal.id = 'messengerWidgetImageModal';
      imgModal.className = 'messenger-widget-image-modal hidden';
      imgModal.innerHTML = `
        <div class="messenger-widget-image-modal-overlay" data-close-widget-img></div>
        <div class="messenger-widget-image-modal-content">
          <div class="messenger-widget-image-modal-header">
            <div class="messenger-widget-image-modal-title" id="messengerWidgetImageTitle">Изображение</div>
            <div class="messenger-widget-image-modal-actions">
              <a class="messenger-widget-image-modal-link" id="messengerWidgetImageOpenLink" href="#" target="_blank" rel="noopener">В новой вкладке</a>
              <a class="messenger-widget-image-modal-link" id="messengerWidgetImageDownloadLink" href="#" target="_blank" rel="noopener">Скачать</a>
              <button type="button" class="messenger-widget-image-modal-close" data-close-widget-img aria-label="Закрыть">✕</button>
            </div>
          </div>
          <div class="messenger-widget-image-modal-body">
            <div class="messenger-widget-image-modal-loader" id="messengerWidgetImageLoader">Загрузка…</div>
            <img id="messengerWidgetImageImg" alt="" class="hidden" />
          </div>
        </div>
      `;
      document.body.appendChild(imgModal);
      this.imageModal = imgModal;
      this.imageModalImg = document.getElementById('messengerWidgetImageImg');
      this.imageModalTitle = document.getElementById('messengerWidgetImageTitle');
      this.imageModalLoader = document.getElementById('messengerWidgetImageLoader');
      this.imageModalOpenLink = document.getElementById('messengerWidgetImageOpenLink');
      this.imageModalDownloadLink = document.getElementById('messengerWidgetImageDownloadLink');

      // Обработчики закрытия модалки
      imgModal.querySelectorAll('[data-close-widget-img]').forEach(b => {
        b.addEventListener('click', (e) => {
          e.preventDefault();
          this.closeImageModal();
        });
      });
      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && !imgModal.classList.contains('hidden')) {
          this.closeImageModal();
        }
      });

      // Обработчик кликов по карточкам вложений
      this.messagesContainer.addEventListener('click', (e) => {
        const card = e.target.closest('.messenger-widget-attachment-card');
        if (!card) return;
        e.preventDefault();
        e.stopPropagation();
        const openUrl = card.getAttribute('data-open') || '';
        const downloadUrl = card.getAttribute('data-download') || openUrl;
        const isImage = card.getAttribute('data-is-image') === '1';
        const isPdf = card.getAttribute('data-is-pdf') === '1';
        const title = card.querySelector('.messenger-widget-attachment-card__name')?.textContent?.trim() || 'Файл';
        if (isImage && openUrl) {
          this.openImageModal(openUrl, downloadUrl, title);
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
      this.messagesContainer.addEventListener('keydown', (e) => {
        if (e.key !== 'Enter' && e.key !== ' ') return;
        const card = e.target.closest('.messenger-widget-attachment-card');
        if (!card) return;
        e.preventDefault();
        card.click();
      });

      // Блок оценки (после закрытия диалога)
      this.ratingBlock = document.createElement('div');
      this.ratingBlock.className = 'messenger-widget-rating messenger-widget-rating-hidden';
      this.ratingBlock.innerHTML = '<div class="messenger-widget-rating-title">Оцените, пожалуйста, диалог</div><div class="messenger-widget-rating-buttons"></div><textarea class="messenger-widget-rating-comment" placeholder="Комментарий (необязательно)" rows="2"></textarea>';
      this.popup.appendChild(this.ratingBlock);

      // Privacy notice
      if (this.privacyText) {
        const privacy = document.createElement('div');
        privacy.className = 'messenger-widget-privacy';
        const textSpan = document.createElement('span');
        textSpan.textContent = this.privacyText;
        privacy.appendChild(textSpan);
        if (this.privacyUrl) {
          const link = document.createElement('a');
          link.href = this.privacyUrl;
          link.target = '_blank';
          link.rel = 'noopener';
          link.className = 'messenger-widget-privacy-link';
          link.textContent = 'Политика конфиденциальности';
          privacy.appendChild(document.createTextNode(' '));
          privacy.appendChild(link);
        }
        this.popup.appendChild(privacy);
      }

      this.updatePrechatVisibility();
      container.appendChild(this.button);
      container.appendChild(this.popup);
      document.body.appendChild(container);

      // Если капча нужна — покажем строку
      this.renderCaptchaRow();
    }
  }

  // Автоинициализация при загрузке скрипта + публичный JS API
  let widgetInstance = null;

  const scriptTag = document.currentScript;
  if (scriptTag) {
    const widgetToken = scriptTag.getAttribute('data-widget-token');
    if (widgetToken) {
      const apiBaseUrl = scriptTag.getAttribute('data-api-base-url') || scriptTag.getAttribute('data-api-base') || '';
      if (apiBaseUrl) {
        CONFIG.API_BASE_URL = String(apiBaseUrl).replace(/\/+$/, '');
      }
      const widget = new MessengerWidget(widgetToken);
      widgetInstance = widget;
      widget.init();
    } else {
      console.warn('[MessengerWidget] data-widget-token attribute is required');
    }
  }

  if (typeof window !== 'undefined') {
    window.ProfiMessenger = {
      open() {
        if (widgetInstance) widgetInstance.open();
      },
      close() {
        if (widgetInstance) widgetInstance.close();
      },
      toggle() {
        if (widgetInstance) widgetInstance.toggle();
      },
      showLauncher() {
        if (widgetInstance) widgetInstance.showLauncher();
      },
      hideLauncher() {
        if (widgetInstance) widgetInstance.hideLauncher();
      },
      isOpen() {
        return !!(widgetInstance && widgetInstance.isOpen);
      },
    };
  }
})();
