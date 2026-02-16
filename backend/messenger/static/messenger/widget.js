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
      this.pollInterval = null;
      this.isOpen = false;
      this.isSending = false;
      this.receivedMessageIds = new Set(); // Anti-duplicate: Set для отслеживания полученных сообщений

      // DOM элементы (будут созданы в render)
      this.button = null;
      this.popup = null;
      this.messagesContainer = null;
      this.input = null;
      this.sendButton = null;
      this.closeButton = null;
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

      // Начать polling
      this.startPolling();
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
     * Отправка сообщения
     */
    async sendMessage(body) {
      if (!body || !body.trim()) {
        return false;
      }

      const trimmedBody = body.trim();
      if (trimmedBody.length > CONFIG.MAX_MESSAGE_LENGTH) {
        console.warn('[MessengerWidget] Message too long, max length:', CONFIG.MAX_MESSAGE_LENGTH);
        return false;
      }

      if (this.isSending) {
        return false; // Уже отправляется
      }

      if (!this.sessionToken) {
        // Нет сессии - попробовать bootstrap
        const success = await this.bootstrap();
        if (!success) {
          return false;
        }
      }

      this.isSending = true;
      this.updateSendButton();

      try {
        const response = await fetch(CONFIG.API_BASE_URL + '/api/widget/send/', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            widget_token: this.widgetToken,
            widget_session_token: this.sessionToken,
            body: trimmedBody,
          }),
        });

        if (response.status === 401) {
          // Expired session - re-bootstrap
          this.clearStorage();
          this.sessionToken = null;
          const success = await this.bootstrap();
          if (!success) {
            this.isSending = false;
            this.updateSendButton();
            return false;
          }
          // Не повторяем отправку автоматически после re-bootstrap
          this.isSending = false;
          this.updateSendButton();
          return false;
        }

        if (response.status === 403) {
          // Mismatch inbox - re-bootstrap и stop
          this.clearStorage();
          this.sessionToken = null;
          this.stopPolling();
          await this.bootstrap();
          this.isSending = false;
          this.updateSendButton();
          return false;
        }

        if (!response.ok) {
          console.error('[MessengerWidget] Send failed:', response.status, response.statusText);
          this.isSending = false;
          this.updateSendButton();
          return false;
        }

        const data = await response.json();
        
        // Добавить отправленное сообщение в ленту (оптимистично)
        this.addMessageToUI({
          id: data.id,
          body: trimmedBody,
          direction: 'in',
          created_at: data.created_at,
        });

        // Очистить поле ввода
        if (this.input) {
          this.input.value = '';
        }

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

    /**
     * Poll: получение новых сообщений
     */
    async poll() {
      if (!this.sessionToken) {
        return;
      }

      try {
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
          return;
        }

        if (response.status === 403) {
          // Mismatch inbox - re-bootstrap и stop
          this.clearStorage();
          this.sessionToken = null;
          this.stopPolling();
          await this.bootstrap();
          return;
        }

        if (!response.ok) {
          console.error('[MessengerWidget] Poll failed:', response.status, response.statusText);
          return;
        }

        const data = await response.json();
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
        }
      } catch (error) {
        console.error('[MessengerWidget] Poll error:', error);
      }
    }

    /**
     * Начать polling
     */
    startPolling() {
      if (this.pollInterval) {
        return; // Уже запущен
      }
      if (!this.sessionToken) {
        return; // Нет сессии
      }
      // Первый poll сразу
      this.poll();
      // Затем каждые 3 секунды
      this.pollInterval = setInterval(() => {
        this.poll();
      }, CONFIG.POLL_INTERVAL);
    }

    /**
     * Остановить polling
     */
    stopPolling() {
      if (this.pollInterval) {
        clearInterval(this.pollInterval);
        this.pollInterval = null;
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
      this.popup.classList.add('messenger-widget-popup-open');
      // Фокус на поле ввода
      if (this.input) {
        setTimeout(() => this.input.focus(), 100);
      }
      // Автоскролл вниз
      this.scrollToBottom();
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
     * Скролл вниз ленты сообщений
     */
    scrollToBottom() {
      if (this.messagesContainer) {
        this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
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

      const bodyEl = document.createElement('div');
      bodyEl.className = 'messenger-widget-message-body';
      bodyEl.textContent = message.body;

      const timeEl = document.createElement('div');
      timeEl.className = 'messenger-widget-message-time';
      if (message.created_at) {
        const date = new Date(message.created_at);
        timeEl.textContent = date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
      }

      messageEl.appendChild(bodyEl);
      messageEl.appendChild(timeEl);
      this.messagesContainer.appendChild(messageEl);

      // Автоскролл вниз
      this.scrollToBottom();
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
      header.innerHTML = '<span>Чат с поддержкой</span>';

      this.closeButton = document.createElement('button');
      this.closeButton.className = 'messenger-widget-close';
      this.closeButton.setAttribute('aria-label', 'Закрыть');
      this.closeButton.innerHTML = '×';
      this.closeButton.addEventListener('click', () => this.close());

      header.appendChild(this.closeButton);
      this.popup.appendChild(header);

      // Лента сообщений
      this.messagesContainer = document.createElement('div');
      this.messagesContainer.className = 'messenger-widget-messages';
      this.popup.appendChild(this.messagesContainer);

      // Форма отправки
      const form = document.createElement('div');
      form.className = 'messenger-widget-form';

      this.input = document.createElement('textarea');
      this.input.className = 'messenger-widget-input';
      this.input.placeholder = 'Введите сообщение...';
      this.input.rows = 3;
      this.input.maxLength = CONFIG.MAX_MESSAGE_LENGTH;
      this.input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          const body = this.input.value.trim();
          if (body) {
            this.sendMessage(body);
          }
        }
      });

      this.sendButton = document.createElement('button');
      this.sendButton.className = 'messenger-widget-send';
      this.sendButton.textContent = 'Отправить';
      this.sendButton.addEventListener('click', () => {
        const body = this.input.value.trim();
        if (body) {
          this.sendMessage(body);
        }
      });

      form.appendChild(this.input);
      form.appendChild(this.sendButton);
      this.popup.appendChild(form);

      container.appendChild(this.button);
      container.appendChild(this.popup);
      document.body.appendChild(container);
    }
  }

  // Автоинициализация при загрузке скрипта
  const scriptTag = document.currentScript;
  if (scriptTag) {
    const widgetToken = scriptTag.getAttribute('data-widget-token');
    if (widgetToken) {
      const widget = new MessengerWidget(widgetToken);
      widget.init();
    } else {
      console.warn('[MessengerWidget] data-widget-token attribute is required');
    }
  }
})();
