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
  }

  init() {
    this.initSidebarControls();
    this.startListPolling();
    this.initKeyboardShortcuts();
    this.initNotifications();
    this.initOverlayHandlers();
    
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
      // Попросим разрешение один раз, тихо
      Notification.requestPermission().then((result) => {
        this.notificationsEnabled = result === 'granted';
      }).catch(() => {
        this.notificationsEnabled = false;
      });
    } else {
      this.notificationsEnabled = false;
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
      // простое состояние загрузки, если список пустой
      if (!listEl.querySelector('.conversation-card')) {
        listEl.innerHTML = `
          <div class="empty-state">
            <p>Загрузка диалогов…</p>
          </div>
        `;
      }

      const response = await fetch(`/api/messenger/conversations/?${params.toString()}`, {
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
      
      // Умное обновление: обновляем карточки и упорядочиваем DOM по ответу API
      const existingCards = new Map();
      Array.from(listEl.querySelectorAll('.conversation-card')).forEach(card => {
        const id = card.getAttribute('data-conversation-id');
        if (id) existingCards.set(parseInt(id), card);
      });

      const fragment = document.createDocumentFragment();
      items.forEach(conversation => {
        const id = conversation.id;
        let card = existingCards.get(id);
        if (!card) {
          const tempDiv = document.createElement('div');
          tempDiv.innerHTML = this.renderConversationCardHtml(conversation);
          card = tempDiv.firstElementChild;
        } else {
          this.updateConversationCardInPlace(card, conversation);
        }
        fragment.appendChild(card);
      });

      listEl.innerHTML = '';
      listEl.appendChild(fragment);
      
    } catch (e) {
      console.error('refreshConversationList failed:', e);
    }
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
    if (timeEl && conversation.last_message_at) {
      const lastAt = new Date(conversation.last_message_at);
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
    const metaEl = cardEl.querySelector('.conversation-meta');
    if (metaEl) {
      const unread = Number(conversation.unread_count || 0);
      let statusBadge = '';
      if (status === 'open') statusBadge = '<span class="badge badge-new badge-xs">Открыт</span>';
      else if (status === 'pending') statusBadge = '<span class="badge badge-progress badge-xs">Ожидание</span>';
      else if (status === 'resolved') statusBadge = '<span class="badge badge-done badge-xs">Решён</span>';
      else if (status === 'closed') statusBadge = '<span class="badge badge-cancel badge-xs">Закрыт</span>';
      
      metaEl.innerHTML = (unread > 0 ? `<span class="conversation-badge">${unread}</span>` : '') + statusBadge;
    }
    
    // Обновить активное состояние
    const isActive = this.currentConversationId === id;
    if (isActive) {
      cardEl.classList.add('active');
    } else {
      cardEl.classList.remove('active');
    }
  }

  startListPolling() {
    if (this.listPollingTimer) return;
    this.listPollingTimer = setInterval(() => {
      this.refreshConversationList();
    }, 10000);
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) {
        if (this.listPollingTimer) { clearInterval(this.listPollingTimer); this.listPollingTimer = null; }
      } else {
        this.startListPolling();
        this.refreshConversationList();
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

    const lastAt = conversation.last_message_at ? new Date(conversation.last_message_at) : null;
    const now = new Date();
    const timeStr = lastAt
      ? (lastAt.toDateString() === now.toDateString()
          ? lastAt.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
          : lastAt.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' }))
      : '';

    const unread = Number(conversation.unread_count || 0);
    const isActive = this.currentConversationId === id;

    let statusBadge = '';
    if (status === 'open') statusBadge = '<span class="badge badge-new badge-xs">Открыт</span>';
    else if (status === 'pending') statusBadge = '<span class="badge badge-progress badge-xs">Ожидание</span>';
    else if (status === 'resolved') statusBadge = '<span class="badge badge-done badge-xs">Решён</span>';
    else if (status === 'closed') statusBadge = '<span class="badge badge-cancel badge-xs">Закрыт</span>';

    return `
      <div class="conversation-card ${isActive ? 'active' : ''}" data-conversation-id="${id}" onclick="window.MessengerPanel.openConversation(${id})">
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
          </div>
        </div>
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

    const prevConversationId = this.currentConversationId;
    if (prevConversationId && prevConversationId !== conversationId) {
      this.stopPolling(prevConversationId);
      this.stopTypingPolling();
    }

    this.currentConversationId = conversationId;
    this.selectedConversationId = conversationId;
    this.pendingNewMessagesCount = 0;
    
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
        <div class="flex items-center justify-center h-full">
          <div class="text-center">
            <div class="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-brand-teal mb-2"></div>
            <p class="text-sm text-brand-dark/60">Загрузка диалога...</p>
          </div>
        </div>
      `;
    }
    if (infoArea) {
      infoArea.innerHTML = `
        <div class="flex items-center justify-center h-full">
          <div class="text-center">
            <div class="inline-block animate-spin rounded-full h-6 w-6 border-b-2 border-brand-teal mb-2"></div>
            <p class="text-xs text-brand-dark/60">Загрузка информации...</p>
          </div>
        </div>
      `;
    }

    try {
      // Загрузить диалог через API
      const response = await fetch(`/api/messenger/conversations/${conversationId}/`, {
        credentials: 'same-origin',
        headers: {
          'Accept': 'application/json',
        }
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const conversation = await response.json();
      
      // Загрузить последние сообщения (ленивая история)
      const messagesResponse = await fetch(`/api/messenger/conversations/${conversationId}/messages/?limit=${this.initialMessagesLimit}`, {
        credentials: 'same-origin',
        headers: {
          'Accept': 'application/json',
        }
      });

      if (!messagesResponse.ok) {
        throw new Error(`HTTP ${messagesResponse.status}`);
      }

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
      
      // Начать polling для новых сообщений
      this.startPolling(conversationId);
      this.startTypingPolling(conversationId);
      
    } catch (error) {
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
    await fetch(`/api/messenger/conversations/${conversationId}/read/`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'X-CSRFToken': this.getCsrfToken(),
      },
    });
    // обновим список (сбросит бейдж непрочитанных, если был)
    this.refreshConversationList();
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
          <div class="flex items-center gap-2">
            <button type="button" id="mobileInfoBtn" class="lg:hidden inline-flex items-center justify-center w-8 h-8 rounded-full border border-brand-soft/80 text-brand-dark/60 hover:text-brand-dark hover:border-brand-soft" title="Инфо">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                <circle cx="12" cy="12" r="9"/><path d="M12 10v6"/><path d="M12 7h.01"/>
              </svg>
            </button>
            ${conversation.status === 'open' ? '<span class="badge badge-new">Открыт</span>' : ''}
            ${conversation.status === 'pending' ? '<span class="badge badge-progress">В ожидании</span>' : ''}
            ${conversation.status === 'resolved' ? '<span class="badge badge-done">Решён</span>' : ''}
            ${conversation.status === 'closed' ? '<span class="badge badge-cancel">Закрыт</span>' : ''}
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
        <div class="flex items-center justify-between mb-2">
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
          <div class="messenger-operator-form-row">
            <input type="file" name="attachments" id="messageAttachments" class="hidden" multiple accept="image/*,.pdf">
            <button type="button" id="messageAttachBtn" class="messenger-operator-icon-btn" title="Прикрепить файл">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48"/></svg>
            </button>
            <textarea name="body" id="messageBody" class="messenger-operator-input messenger-input-autogrow" rows="1" placeholder="Введите сообщение..." style="min-height:40px;max-height:120px;"></textarea>
            <button type="button" id="messageEmojiBtn" class="messenger-operator-icon-btn" title="Эмодзи">😊</button>
            <button type="submit" id="messageSendBtn" class="messenger-operator-send-btn" title="Отправить (Ctrl+Enter)">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>
            </button>
          </div>
          <div id="messageAttachmentsNames" class="text-xs text-brand-dark/60 mt-1 px-1"></div>
          <div id="composeModeHint" class="text-[10px] text-brand-dark/40 mt-1 px-1">Сообщение увидит клиент. Внутренние заметки доступны только сотрудникам.</div>
          <p class="text-[10px] text-brand-dark/40 mt-1.5 px-1">Макс. 5 МБ на файл • изображения и PDF</p>
        </form>
      </div>
    `;

    html += `</div>`;
    contentArea.innerHTML = html;
    
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
        const names = Array.from(this.files).map(f => f.name).join(', ');
        const namesEl = document.getElementById('messageAttachmentsNames');
        if (namesEl) namesEl.textContent = names || '';
      });
    }

    this.initOperatorEmojiPicker(conversation.id);

    // Ctrl+Enter для отправки
    const messageBody = document.getElementById('messageBody');
    if (messageBody) {
      messageBody.addEventListener('keydown', function(e) {
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
          e.preventDefault();
          const form = document.getElementById('messageForm');
          if (form) form.requestSubmit ? form.requestSubmit() : form.dispatchEvent(new Event('submit'));
        }
      });
      messageBody.addEventListener('input', () => {
        this.sendOperatorTypingPing(conversation.id);
      });
      
      // Инициализация авто-роста поля ввода
      this.initMessageInputAutogrow(messageBody);
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
    };
    if (btnOut) btnOut.addEventListener('click', () => { this.composeMode = 'OUT'; applyModeUI(); });
    if (btnInternal) btnInternal.addEventListener('click', () => { this.composeMode = 'INTERNAL'; applyModeUI(); });

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
      const url = `/api/messenger/conversations/${conversationId}/messages/?before=${beforeTs}${beforeId}&limit=${limit}`;
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
                <span>Филиал</span>
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
              <button type="button" class="btn btn-outline btn-sm flex-1 text-xs" id="assignMeBtn">
                <span class="inline-flex items-center gap-1.5">
                  ${iconUserPlus}
                  <span>Назначить меня</span>
                </span>
              </button>
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
      </div>
    `;

    infoArea.innerHTML = html;

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
  }

  async patchConversation(conversationId, payload, onError) {
    try {
      const response = await fetch(`/api/messenger/conversations/${conversationId}/`, {
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
    const body = form.querySelector('[name="body"]').value.trim();
    const fileInput = form.querySelector('[name="attachments"]');
    const files = fileInput ? Array.from(fileInput.files) : [];
    
    if (!body && files.length === 0) {
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

      const response = await fetch(`/api/messenger/conversations/${conversationId}/messages/`, {
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
      
      // Очистить форму
      form.querySelector('[name="body"]').value = '';
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
        submitButton.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"/></svg>';
      }
    }
  }

  /**
   * Показать уведомление (toast)
   */
  showNotification(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `fixed top-4 right-4 z-50 px-4 py-3 rounded-lg shadow-lg text-sm ${
      type === 'success' ? 'bg-green-500 text-white' :
      type === 'error' ? 'bg-red-500 text-white' :
      'bg-blue-500 text-white'
    }`;
    toast.textContent = message;
    document.body.appendChild(toast);
    
    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transition = 'opacity 0.3s';
      setTimeout(() => toast.remove(), 300);
    }, 3000);
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
      <div class="flex gap-3 mb-4 ${isOutgoing ? 'flex-row-reverse' : 'flex-row'}" data-message-id="${message.id}">
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
            <div class="text-sm text-brand-dark whitespace-pre-wrap">${this.escapeHtml(message.body || '')}</div>
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
          separatorDiv.innerHTML = `
            <span class="inline-block px-3 py-1 rounded-full bg-brand-soft/40 text-xs text-brand-dark/70">
              ${messageDate}
            </span>
          `;
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
    if (typeof window === 'undefined' || typeof AudioContext === 'undefined') return;

    // Короткий «ping» через Web Audio API
    const Ctor = window.AudioContext || window.webkitAudioContext;
    if (!Ctor) return;

    const ctx = new Ctor();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();

    osc.type = 'sine';
    osc.frequency.value = 880; // A5
    gain.gain.setValueAtTime(0.0001, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.2, ctx.currentTime + 0.01);
    gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.25);

    osc.connect(gain);
    gain.connect(ctx.destination);

    osc.start();
    osc.stop(ctx.currentTime + 0.3);
  }

  startTypingPolling(conversationId) {
    this.stopTypingPolling();
    if (!conversationId) return;

    this.typingPollTimer = setInterval(async () => {
      if (document.hidden) return;
      if (this.currentConversationId !== conversationId) return;
      try {
        const response = await fetch(`/api/messenger/conversations/${conversationId}/typing/`, {
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
      await fetch(`/api/messenger/conversations/${conversationId}/typing/`, {
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
   * Начать polling для новых сообщений
   */
  startPolling(conversationId) {
    // Остановить предыдущий polling
    this.stopPolling(conversationId);
    
    this.pollingIntervals[conversationId] = setInterval(async () => {
      if (this.currentConversationId !== conversationId) {
        this.stopPolling(conversationId);
        return;
      }

      if (document.hidden) return;

      try {
        const url = `/api/messenger/conversations/${conversationId}/messages/` + 
                    (this.lastMessageTimestamp ? `?since=${encodeURIComponent(this.lastMessageTimestamp)}` : '');
        
        const response = await fetch(url, {
          credentials: 'same-origin',
          headers: {
            'Accept': 'application/json',
          }
        });

        if (response.ok) {
          const messages = await response.json();
          if (messages.length > 0) {
            // Есть новые сообщения - добавить их умно, без полного перерендера
            this.appendNewMessages(messages);
            // Обновить список диалогов (обновит превью/время)
            this.refreshConversationList();
          }
        }
      } catch (error) {
        console.error('Polling error:', error);
      }
    }, 3000); // Каждые 3 секунды
  }

  /**
   * Остановить polling
   */
  stopPolling(conversationId) {
    if (this.pollingIntervals[conversationId]) {
      clearInterval(this.pollingIntervals[conversationId]);
      delete this.pollingIntervals[conversationId];
    }
  }

  /**
   * Эмодзи-пикер в форме сообщения оператора
   */
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
        const start = messageBody.selectionStart;
        const end = messageBody.selectionEnd;
        const text = messageBody.value;
        messageBody.value = text.slice(0, start) + emoji + text.slice(end);
        messageBody.selectionStart = messageBody.selectionEnd = start + emoji.length;
        messageBody.focus();
        pickerEl.classList.add('messenger-operator-emoji-picker-hidden');
        if (messageBody.getAttribute('data-messenger-autogrow-init')) {
          messageBody.style.height = 'auto';
          messageBody.style.height = messageBody.scrollHeight + 'px';
        }
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
}

// Глобальный экземпляр
const panel = new MessengerOperatorPanel();

// Экспорт для использования в других скриптах
window.MessengerPanel = panel;
