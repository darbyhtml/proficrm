(function () {
  'use strict';

  // ─── УТИЛИТЫ ДЛЯ ТЕЛЕФОНОВ ──────────────────────────────────────────────────
  function formatRuPhone11(d11) {
    var p = d11.slice(1);
    return '+7 (' + p.slice(0, 3) + ') ' + p.slice(3, 6) + '-' + p.slice(6, 8) + '-' + p.slice(8, 10);
  }

  function normalizePhoneToDisplay(raw) {
    var digits = String(raw || '').replace(/\D/g, '');
    if (!digits) return null;
    if (digits.length === 10) return formatRuPhone11('7' + digits);
    if (digits.length >= 11) {
      var base = digits;
      if (base[0] === '8') base = '7' + base.slice(1);
      if (base[0] === '7') return formatRuPhone11(base.slice(0, 11));
      return '+' + digits;
    }
    return null;
  }

  function extractPhones(text) {
    var src = String(text || '');
    var re = /(\+?\d[\d()\s\-]{8,}\d)/g;
    var matches = [], m;
    while ((m = re.exec(src)) !== null) matches.push(m[1]);
    var candidates = matches.length ? matches : src.split(/[\n,;]+/).map(function (s) { return s.trim(); }).filter(Boolean);
    var out = [], seen = new Set();
    for (var i = 0; i < candidates.length; i++) {
      var n = normalizePhoneToDisplay(candidates[i]);
      if (!n || seen.has(n)) continue;
      seen.add(n);
      out.push(n);
    }
    return out;
  }

  // ─── НОРМАЛИЗАЦИЯ РАСПИСАНИЯ (только на blur) ────────────────────────────────
  function normalizeScheduleText(text) {
    var s = String(text || '');
    s = s.replace(/;+/g, '\n');
    s = s.replace(/[—–−]/g, '-');
    s = s.replace(/(\d{2}:\d{2})\s*-\s*(\d{2}:\d{2})/g, '$1–$2');
    s = s.replace(/\b(пн|вт|ср|чт|пт|сб|вс)\s*-\s*(пн|вт|ср|чт|пт|сб|вс)\b/gi, function (_, a, b) {
      var cap = function (x) { return x.charAt(0).toUpperCase() + x.slice(1).toLowerCase(); };
      return cap(a) + '–' + cap(b);
    });
    s = s.replace(/\b(пн|вт|ср|чт|пт|сб|вс)\b/gi, function (m) {
      return m.charAt(0).toUpperCase() + m.slice(1).toLowerCase();
    });
    s = s.replace(/\b(\d{1,2})[:.\-](\d{2})(?::\d{2})?\b/g, function (_, h, min) {
      return h.padStart(2, '0') + ':' + min;
    });
    s = s.split('\n').map(function (line) {
      var l = line.trim();
      if (!l) return '';
      if (l.includes(':')) return l;
      if (/\b(Пн|Вт|Ср|Чт|Пт|Сб|Вс)(?:[–, ](?:Пн|Вт|Ср|Чт|Пт|Сб|Вс))*/.test(l) && /\d{1,2}:\d{2}/.test(l)) {
        return l.replace(/^((?:Пн|Вт|Ср|Чт|Пт|Сб|Вс)(?:[–, ](?:Пн|Вт|Ср|Чт|Пт|Сб|Вс))*)\s+/, '$1: ');
      }
      return l;
    }).filter(Boolean).join('\n');
    return s.trim();
  }

  // ─── LIVE ДУБЛИРОВАНИЕ ──────────────────────────────────────────────────────
  var box          = document.getElementById('dupBox');
  var list         = document.getElementById('dupList');
  var meta         = document.getElementById('dupMeta');
  var hidden       = document.getElementById('dupHidden');
  var spinner      = document.getElementById('dupSpinnerInline');
  var confirmBlock = document.getElementById('dupConfirmBlock');
  var confirmCheck = document.getElementById('dupConfirmCheck');
  var submitHint   = document.getElementById('dupSubmitHint');
  var nameEl   = document.querySelector('input[name="name"]');
  var innEl    = document.querySelector('textarea[name="inn"], input[name="inn"]');
  var kppEl    = document.querySelector('input[name="kpp"]');
  var addrEl   = document.querySelector('textarea[name="address"], input[name="address"]');

  var dupTimer = null;
  var dupAbort = null;

  function normalizeIdent(v) { return (v || '').replace(/\s+/g, ''); }

  function showSpinner(on) {
    if (spinner) spinner.classList.toggle('hidden', !on);
  }

  async function runDupCheck() {
    var name    = (nameEl  && nameEl.value  || '').trim();
    var inn     = normalizeIdent(innEl  && innEl.value  || '');
    var kpp     = normalizeIdent(kppEl  && kppEl.value  || '');
    var address = (addrEl  && addrEl.value  || '').trim();

    if (!name && !inn && !kpp && !address) {
      if (box) box.style.display = 'none';
      showSpinner(false);
      setDupState(false);
      return;
    }

    showSpinner(true);
    if (dupAbort) dupAbort.abort();
    dupAbort = new AbortController();

    var qs = new URLSearchParams({ name: name, inn: inn, kpp: kpp, address: address });
    var data;
    try {
      var res = await fetch('/companies/duplicates/?' + qs.toString(), {
        headers: { 'X-Requested-With': 'fetch' },
        signal: dupAbort.signal,
      });
      data = await res.json();
    } catch (e) {
      showSpinner(false);
      if (e.name !== 'AbortError') console && console.warn && console.warn('duplicate check failed', e);
      return;
    }
    showSpinner(false);

    if (!box || !list || !meta || !hidden) return;

    var items         = Array.isArray(data.items) ? data.items : [];
    var backendHidden = data.hidden_count || 0;

    if (!items.length && backendHidden === 0) { box.style.display = 'none'; setDupState(false); return; }

    box.style.display = 'block';
    var reasonsText = data.reasons && data.reasons.length ? ('По: ' + data.reasons.join(', ')) : '';
    meta.textContent = items.length + ' совп.' + (reasonsText ? ' · ' + reasonsText : '');

    function esc(s) {
      return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function createDupCard(it) {
      var card = document.createElement('div');
      card.className = 'block rounded-xl border border-brand-soft/80 bg-white/90 px-3 py-2 shadow-sm transition-all hover:border-brand-teal/50 hover:bg-brand-soft/10 cursor-default';
      var matchBadges = (it.match && it.match.length)
        ? ('<div class="mt-1 flex flex-wrap gap-1"><span class="badge badge-warn">Совпало:</span>' +
            it.match.map(function (mm) { return '<span class="badge badge-warn">' + esc(mm) + '</span>'; }).join('') +
           '</div>')
        : '';
      card.innerHTML =
        '<div class="font-medium">' + esc(it.name) + '</div>' +
        '<div class="text-xs muted">ИНН: ' + esc(it.inn || '—') + ' · КПП: ' + esc(it.kpp || '—') +
          (it.branch ? ' · Филиал: ' + esc(it.branch) : '') +
          (it.responsible ? ' · Ответственный: ' + esc(it.responsible) : '') + '</div>' +
        matchBadges +
        '<div class="text-xs muted">' + esc(it.address || '—') + '</div>' +
        '<div class="mt-2 flex flex-wrap gap-2 text-xs">' +
          '<button type="button" class="btn btn-outline btn-xxs" data-dup-open="' + esc(it.url) + '">Открыть в новой вкладке</button>' +
          '<button type="button" class="btn btn-outline btn-xxs" data-dup-goto="' + esc(it.url) + '">Это дубль — перейти и не создавать</button>' +
        '</div>';
      return card;
    }

    list.innerHTML = '';
    var visibleItems = items.slice(0, 1);
    var hiddenItems  = items.slice(1);

    visibleItems.forEach(function (it) { list.appendChild(createDupCard(it)); });

    if (hiddenItems.length) {
      var extraContainer = document.createElement('div');
      extraContainer.className = 'mt-2 space-y-2';
      extraContainer.style.display = 'none';
      hiddenItems.forEach(function (it) { extraContainer.appendChild(createDupCard(it)); });

      var toggleBtn = document.createElement('button');
      toggleBtn.type = 'button';
      toggleBtn.className = 'mt-2 text-xs text-brand-dark/70 hover:text-brand-teal underline decoration-dotted';
      toggleBtn.textContent = 'Показать ещё ' + hiddenItems.length + ' совпадений';
      var expanded = false;
      toggleBtn.addEventListener('click', function () {
        expanded = !expanded;
        extraContainer.style.display = expanded ? 'block' : 'none';
        toggleBtn.textContent = expanded ? 'Скрыть список' : 'Показать ещё ' + hiddenItems.length + ' совпадений';
      });
      list.appendChild(toggleBtn);
      list.appendChild(extraContainer);
    }

    hidden.style.display  = backendHidden > 0 ? 'block' : 'none';
    hidden.textContent    = backendHidden > 0 ? 'На списке компаний могут быть и другие похожие карточки.' : '';
    setDupState(items.length > 0);
  }

  // ─── БЛОКИРОВКА КНОПКИ ПРИ НАЛИЧИИ ДУБЛЕЙ ──────────────────────────────────
  function setDupState(hasItems) {
    var btn = document.getElementById('createSubmitBtn');
    if (confirmBlock) confirmBlock.classList.toggle('hidden', !hasItems);
    if (confirmCheck && hasItems) confirmCheck.checked = false;
    if (submitHint) submitHint.classList.toggle('hidden', !hasItems);
    if (btn) {
      btn.disabled = hasItems;
      btn.style.opacity = hasItems ? '0.45' : '';
      btn.style.cursor  = hasItems ? 'not-allowed' : '';
    }
  }

  function scheduleDupCheck() {
    if (dupTimer) clearTimeout(dupTimer);
    dupTimer = setTimeout(runDupCheck, 400);
  }

  [nameEl, innEl, kppEl, addrEl].filter(Boolean).forEach(function (el) {
    el.addEventListener('input', scheduleDupCheck);
  });

  if (list) {
    list.addEventListener('click', function (e) {
      var openBtn = e.target.closest('button[data-dup-open]');
      var gotoBtn = e.target.closest('button[data-dup-goto]');
      var form    = document.getElementById('companyCreateForm');
      var warnText = 'Вы уйдёте со страницы создания компании. Несохранённые данные могут быть потеряны.';

      if (openBtn) {
        var url = openBtn.getAttribute('data-dup-open');
        if (!url) return;
        if (!window.confirm(warnText + '\n\nОткрыть карточку в новой вкладке?')) return;
        window.open(url, '_blank', 'noopener');
        return;
      }
      if (gotoBtn) {
        var url2 = gotoBtn.getAttribute('data-dup-goto');
        if (!url2) return;
        if (!window.confirm(warnText + '\n\nПерейти на существующую карточку?')) return;
        if (form) { form.dataset.submitted = '1'; form.dataset.allowNavigate = '1'; }
        window.location.href = url2;
      }
    });
  }

  // ─── ФОРМА: ЧЕРНОВИК + SUBMIT ────────────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', function () {
    var formEl          = document.getElementById('companyCreateForm');
    var submitBtn       = document.getElementById('createSubmitBtn');
    var DRAFT_KEY       = 'companyCreate:draft';
    var draftBannerEl   = document.getElementById('companyCreateDraftBanner');
    var draftMetaEl     = document.getElementById('companyCreateDraftMeta');
    var draftRestoreBtn = document.getElementById('companyCreateDraftRestoreBtn');
    var draftDiscardBtn = document.getElementById('companyCreateDraftDiscardBtn');
    var formDirty       = false;
    var draftSnapshot   = null;

    // ── autofocus ────────────────────────────────────────────────────────────
    var nameInput = document.getElementById('id_name');
    if (nameInput && !nameInput.value) nameInput.focus();

    // ── Чекбокс подтверждения дублей ─────────────────────────────────────────
    if (confirmCheck) {
      confirmCheck.addEventListener('change', function () {
        var confirmed = this.checked;
        if (submitBtn) {
          submitBtn.disabled    = !confirmed;
          submitBtn.style.opacity = confirmed ? '' : '0.45';
          submitBtn.style.cursor  = confirmed ? '' : 'not-allowed';
        }
        if (submitHint) submitHint.classList.toggle('hidden', confirmed);
      });
    }

    // ── Маска основного телефона ─────────────────────────────────────────────
    var mainPhoneInput = document.getElementById('id_phone');
    if (mainPhoneInput) {
      mainPhoneInput.addEventListener('blur', function () {
        var n = normalizePhoneToDisplay(this.value);
        if (n) this.value = n;
      });
    }

    // ── Вид договора: скрыть «Действует до» для годовых ─────────────────────
    var contractTypeEl     = document.getElementById('id_contract_type');
    var contractUntilWrap  = document.getElementById('contract-until-wrapper');
    function syncContractUntil() {
      if (!contractTypeEl || !contractUntilWrap) return;
      var opt = contractTypeEl.options[contractTypeEl.selectedIndex];
      var isAnnual = opt && opt.dataset.isAnnual === 'true';
      contractUntilWrap.style.display = isAnnual ? 'none' : '';
    }
    if (contractTypeEl) {
      contractTypeEl.addEventListener('change', syncContractUntil);
      syncContractUntil();
    }

    // ── Режим работы: только blur ────────────────────────────────────────────
    var workScheduleField = document.querySelector('textarea[name="work_schedule"]');
    if (workScheduleField) {
      workScheduleField.addEventListener('blur', function (e) {
        var formatted = normalizeScheduleText(e.target.value);
        if (formatted !== e.target.value) e.target.value = formatted;
      });
    }

    // ── Черновик ─────────────────────────────────────────────────────────────
    function collectFormData(form) {
      var data = {};
      if (!form) return data;
      form.querySelectorAll('input, textarea, select').forEach(function (el) {
        if (!el.name || el.type === 'hidden' || el.name === 'csrfmiddlewaretoken') return;
        if ((el.type === 'checkbox' || el.type === 'radio') && !el.checked) return;
        if (el.tagName === 'SELECT' && el.multiple) {
          data[el.name] = Array.from(el.selectedOptions).map(function (o) { return o.value; });
        } else {
          data[el.name] = el.value;
        }
      });
      return data;
    }

    function hasUserData(form) {
      if (!form) return false;
      var els = form.querySelectorAll('input, textarea, select');
      for (var i = 0; i < els.length; i++) {
        var el = els[i];
        if (!el.name || el.type === 'hidden' || el.name === 'csrfmiddlewaretoken') continue;
        if (el.type === 'checkbox' || el.type === 'radio') { if (el.checked) return true; continue; }
        if ((el.value || '').trim()) return true;
      }
      return false;
    }

    function loadDraft() {
      if (draftSnapshot) return draftSnapshot;
      try {
        var raw = localStorage.getItem(DRAFT_KEY);
        if (!raw) return null;
        var d = JSON.parse(raw);
        if (!d || typeof d !== 'object') return null;
        draftSnapshot = d;
        return d;
      } catch (e) { return null; }
    }

    function applyDraftToControls(form, draft) {
      if (!form || !draft) return;
      var triggerNames = new Set(['name', 'inn', 'kpp', 'address']);
      form.querySelectorAll('input, textarea, select').forEach(function (el) {
        if (!el.name || !(el.name in draft)) return;
        if (el.type === 'checkbox' || el.type === 'radio') { el.checked = !!draft[el.name]; }
        else if (el.tagName === 'SELECT' && el.multiple && Array.isArray(draft[el.name])) {
          var vals = draft[el.name];
          Array.from(el.options).forEach(function (o) { o.selected = vals.indexOf(o.value) !== -1; });
        } else { el.value = draft[el.name]; }
        if (triggerNames.has(el.name)) {
          try { el.dispatchEvent(new Event('input', { bubbles: true })); } catch (e) {}
        }
      });
    }

    var saveDraftTimer = null;
    function saveDraftSoon() {
      if (!formEl) return;
      formDirty = true;
      clearTimeout(saveDraftTimer);
      saveDraftTimer = setTimeout(function () {
        try {
          var payload = collectFormData(formEl);
          payload.__meta = { savedAt: new Date().toLocaleString('ru-RU') };
          localStorage.setItem(DRAFT_KEY, JSON.stringify(payload));
        } catch (e) {}
      }, 400);
    }

    // Восстановление динамических строк (телефоны/email) из черновика
    var phonesContainer = document.getElementById('company-phones-container');
    var emailsContainer = document.getElementById('company-emails-container');
    var phoneIndex = phonesContainer ? phonesContainer.querySelectorAll('input[name^="company_phones_"]').length : 0;
    var emailIndex = emailsContainer ? emailsContainer.querySelectorAll('input[name^="company_emails_"]').length : 0;

    function addPhoneRowFromDraft(val) {
      if (!phonesContainer) return null;
      var row = document.createElement('div');
      row.className = 'flex gap-2 items-center company-phone-row';
      row.innerHTML = '<input type="text" name="company_phones_' + phoneIndex + '" value=""' +
        ' class="flex-1 rounded-lg border px-3 py-2" placeholder="+7 ..." />' +
        '<button type="button" class="btn btn-outline text-red-600 hover:bg-red-50 remove-phone-btn" title="Удалить номер">' +
        '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="pointer-events:none">' +
        '<polyline points="3 6 5 6 21 6"></polyline><path d="M19 6l-1 14H6L5 6"></path>' +
        '<path d="M10 11v6"></path><path d="M14 11v6"></path><path d="M9 6l1-3h4l1 3"></path></svg></button>';
      phonesContainer.appendChild(row);
      var inp = row.querySelector('input');
      if (inp && val) inp.value = val;
      phoneIndex++;
      return inp;
    }

    function addEmailRowFromDraft(val) {
      if (!emailsContainer) return null;
      var row = document.createElement('div');
      row.className = 'flex gap-2 items-center company-email-row';
      row.innerHTML = '<input type="email" name="company_emails_' + emailIndex + '" value=""' +
        ' class="flex-1 rounded-lg border px-3 py-2" placeholder="email@example.com" />' +
        '<button type="button" class="btn btn-outline text-red-600 hover:bg-red-50 remove-email-btn" title="Удалить email">' +
        '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="pointer-events:none">' +
        '<polyline points="3 6 5 6 21 6"></polyline><path d="M19 6l-1 14H6L5 6"></path>' +
        '<path d="M10 11v6"></path><path d="M14 11v6"></path><path d="M9 6l1-3h4l1 3"></path></svg></button>';
      emailsContainer.appendChild(row);
      var inp = row.querySelector('input');
      if (inp && val) inp.value = val;
      emailIndex++;
      return inp;
    }

    function restoreDynamicFromDraft(draft) {
      if (!draft) return;
      var emailMap = {}, phoneMap = {};
      Object.entries(draft).forEach(function (_a) {
        var k = _a[0], v = _a[1];
        var val = String(v || '').trim();
        if (!val) return;
        var em = k.match(/^company_emails_(\d+)$/);
        if (em) { emailMap[em[1]] = val; return; }
        var pm = k.match(/^company_phones_(\d+)$/);
        if (pm) { phoneMap[pm[1]] = val; }
      });
      if (phonesContainer && Object.keys(phoneMap).length) {
        Object.keys(phoneMap).forEach(function (idx) {
          var name = 'company_phones_' + idx;
          var inp = phonesContainer.querySelector('input[name="' + name + '"]');
          while (!inp) { var c = addPhoneRowFromDraft(''); if (!c) break; if (c.name === name) { inp = c; break; } }
          if (inp) inp.value = normalizePhoneToDisplay(phoneMap[idx]) || phoneMap[idx];
        });
      }
      if (emailsContainer && Object.keys(emailMap).length) {
        Object.keys(emailMap).forEach(function (idx) {
          var name = 'company_emails_' + idx;
          var inp = emailsContainer.querySelector('input[name="' + name + '"]');
          while (!inp) { var c = addEmailRowFromDraft(''); if (!c) break; if (c.name === name) { inp = c; break; } }
          if (inp) inp.value = emailMap[idx];
        });
      }
    }

    // Показ баннера черновика
    if (formEl) {
      var draft = loadDraft();
      if (draft && draftBannerEl) {
        draftBannerEl.classList.remove('hidden');
        if (draftMetaEl) {
          var meta2 = (draft.__meta && draft.__meta.savedAt) ? String(draft.__meta.savedAt) : '';
          draftMetaEl.textContent = meta2 ? ' (сохранено: ' + meta2 + ')' : '';
        }
      }

      // Обязательные поля
      ['name', 'inn'].forEach(function (n) {
        var el = formEl.querySelector('[name="' + n + '"]');
        if (el) el.required = true;
      });

      formEl.addEventListener('input',  saveDraftSoon, true);
      formEl.addEventListener('change', saveDraftSoon, true);

      formEl.addEventListener('submit', function (e) {
        if (submitBtn && submitBtn.disabled) { e.preventDefault(); return; }
        if (submitBtn) {
          submitBtn.disabled = true;
          submitBtn.textContent = 'Создание…';
        }
        formEl.dataset.submitted = '1';
        formEl.dataset.allowNavigate = '1';
        formDirty = false;
        try { localStorage.removeItem(DRAFT_KEY); } catch (e2) {}
      });

      window.addEventListener('beforeunload', function (e) {
        if (!formDirty) return;
        if (formEl.dataset.submitted === '1') return;
        if (formEl.dataset.allowNavigate === '1') return;
        e.preventDefault();
        e.returnValue = '';
      });
    }

    if (draftRestoreBtn) {
      draftRestoreBtn.addEventListener('click', function () {
        var d = draftSnapshot || loadDraft();
        if (!d) return;
        if (hasUserData(formEl)) {
          if (!window.confirm('Данные из черновика заменят текущие значения. Продолжить?')) return;
        }
        applyDraftToControls(formEl, d);
        restoreDynamicFromDraft(d);
        if (draftBannerEl) draftBannerEl.classList.add('hidden');
        formDirty = true;
        saveDraftSoon();
      });
    }

    if (draftDiscardBtn) {
      draftDiscardBtn.addEventListener('click', function () {
        try { localStorage.removeItem(DRAFT_KEY); } catch (e) {}
        draftSnapshot = null;
        if (draftBannerEl) draftBannerEl.classList.add('hidden');
      });
    }

    // ── Нормализуем уже загруженные телефоны ────────────────────────────────
    if (phonesContainer) {
      phonesContainer.querySelectorAll('input[name^="company_phones_"]').forEach(function (inp) {
        var n = normalizePhoneToDisplay(inp.value);
        if (n) inp.value = n;
        inp.addEventListener('blur', function () { var nn = normalizePhoneToDisplay(this.value); if (nn) this.value = nn; });
      });

      document.getElementById('add-phone-btn') && document.getElementById('add-phone-btn').addEventListener('click', function () {
        var row = document.createElement('div');
        row.className = 'flex gap-2 items-center company-phone-row';
        row.innerHTML = '<input type="text" name="company_phones_' + phoneIndex + '" value=""' +
          ' class="flex-1 rounded-lg border px-3 py-2" placeholder="+7 ..." />' +
          '<button type="button" class="btn btn-outline text-red-600 hover:bg-red-50 remove-phone-btn" title="Удалить номер">' +
          '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="pointer-events:none">' +
          '<polyline points="3 6 5 6 21 6"></polyline><path d="M19 6l-1 14H6L5 6"></path>' +
          '<path d="M10 11v6"></path><path d="M14 11v6"></path><path d="M9 6l1-3h4l1 3"></path></svg></button>';
        phonesContainer.appendChild(row);
        var inp = row.querySelector('input');
        if (inp) {
          inp.addEventListener('blur', function () { var n = normalizePhoneToDisplay(this.value); if (n) this.value = n; });
          inp.focus();
        }
        phoneIndex++;
      });

      phonesContainer.addEventListener('click', function (e) {
        var btn = e.target.closest('.remove-phone-btn');
        if (!btn) return;
        var row = btn.closest('.company-phone-row');
        if (!row) return;
        if (phonesContainer.children.length > 1) { row.remove(); }
        else { var inp = row.querySelector('input'); if (inp) inp.value = ''; }
      });

      phonesContainer.addEventListener('paste', function (e) {
        var t = e.target;
        if (!t || t.tagName !== 'INPUT') return;
        var txt = (e.clipboardData || window.clipboardData).getData('text');
        var phones = extractPhones(txt);
        if (!phones || phones.length <= 1) return;
        e.preventDefault(); e.stopPropagation();
        t.value = phones[0] || '';
        for (var i = 1; i < phones.length; i++) {
          var row2 = document.createElement('div');
          row2.className = 'flex gap-2 items-center company-phone-row';
          row2.innerHTML = '<input type="text" name="company_phones_' + phoneIndex + '" value="' + phones[i] + '"' +
            ' class="flex-1 rounded-lg border px-3 py-2" placeholder="+7 ..." />' +
            '<button type="button" class="btn btn-outline text-red-600 hover:bg-red-50 remove-phone-btn" title="Удалить номер">' +
            '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="pointer-events:none">' +
            '<polyline points="3 6 5 6 21 6"></polyline><path d="M19 6l-1 14H6L5 6"></path>' +
            '<path d="M10 11v6"></path><path d="M14 11v6"></path><path d="M9 6l1-3h4l1 3"></path></svg></button>';
          phonesContainer.appendChild(row2);
          phoneIndex++;
        }
      });
    }

    // ── Email ─────────────────────────────────────────────────────────────────
    if (emailsContainer) {
      document.getElementById('add-email-btn') && document.getElementById('add-email-btn').addEventListener('click', function () {
        var row = document.createElement('div');
        row.className = 'flex gap-2 items-center company-email-row';
        row.innerHTML = '<input type="email" name="company_emails_' + emailIndex + '" value=""' +
          ' class="flex-1 rounded-lg border px-3 py-2" placeholder="email@example.com" />' +
          '<button type="button" class="btn btn-outline text-red-600 hover:bg-red-50 remove-email-btn" title="Удалить email">' +
          '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="pointer-events:none">' +
          '<polyline points="3 6 5 6 21 6"></polyline><path d="M19 6l-1 14H6L5 6"></path>' +
          '<path d="M10 11v6"></path><path d="M14 11v6"></path><path d="M9 6l1-3h4l1 3"></path></svg></button>';
        emailsContainer.appendChild(row);
        var inp = row.querySelector('input');
        if (inp) inp.focus();
        emailIndex++;
      });

      emailsContainer.addEventListener('click', function (e) {
        var btn = e.target.closest('.remove-email-btn');
        if (!btn) return;
        var row = btn.closest('.company-email-row');
        if (!row) return;
        if (emailsContainer.children.length > 1) { row.remove(); }
        else { var inp = row.querySelector('input'); if (inp) inp.value = ''; }
      });
    }

    // ── MS-виджеты ────────────────────────────────────────────────────────────
    if (window.initMsSingleWidgets) window.initMsSingleWidgets();
    if (window.initMsMultiWidgets)  window.initMsMultiWidgets();
    if (window.initMsWidgets)       window.initMsWidgets();
  });
})();
