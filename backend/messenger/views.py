"""
Views для messenger app.
"""

from django.shortcuts import render
from django.http import Http404, HttpResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.clickjacking import xframe_options_exempt

from .models import Inbox
from .utils import ensure_messenger_enabled_view


@login_required
def widget_demo(request):
    """
    Demo страница для тестирования виджета.
    
    Доступна только авторизованным пользователям (для безопасности).
    """
    ensure_messenger_enabled_view()

    # Получить первый активный Inbox для демо
    inbox = Inbox.objects.filter(is_active=True).first()
    
    if not inbox:
        # Если нет активных inbox - показать инструкцию
        return render(
            request,
            'messenger/widget_demo.html',
            {
                'inbox': None,
                'widget_token': None,
            },
        )

    return render(
        request,
        'messenger/widget_demo.html',
        {
            'inbox': inbox,
            'widget_token': inbox.widget_token,
        },
    )


@xframe_options_exempt
def widget_test_page(request):
    """
    Standalone HTML-страница для тестирования виджета на «внешнем сайте».
    Без авторизации, без шаблонов CRM — чистый HTML, как на реальном сайте клиента.
    """
    ensure_messenger_enabled_view()
    inbox = Inbox.objects.filter(is_active=True).first()
    if not inbox:
        return HttpResponse("No active inbox", status=404)

    # Определяем base URL для API (схема + хост текущего запроса)
    scheme = request.scheme
    host = request.get_host()
    api_base = f"{scheme}://{host}"
    token = inbox.widget_token

    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Тест виджета — ЦПР ПРОФИ</title>
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f0f4f8; color: #1e293b; }}
    .hero {{
      background: linear-gradient(135deg, #01948E 0%, #016d68 100%);
      color: #fff; padding: 80px 20px; text-align: center;
    }}
    .hero h1 {{ font-size: 2.5rem; margin-bottom: 16px; }}
    .hero p {{ font-size: 1.2rem; opacity: 0.9; max-width: 600px; margin: 0 auto; }}
    .content {{ max-width: 800px; margin: 40px auto; padding: 0 20px; }}
    .card {{
      background: #fff; border-radius: 16px; padding: 32px;
      box-shadow: 0 4px 24px rgba(0,0,0,0.06); margin-bottom: 24px;
    }}
    .card h2 {{ font-size: 1.4rem; margin-bottom: 12px; color: #01948E; }}
    .card p {{ line-height: 1.7; color: #475569; }}
    .features {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 16px; }}
    .feature {{ background: #f8fafc; border-radius: 12px; padding: 20px; border: 1px solid #e2e8f0; }}
    .feature h3 {{ font-size: 1rem; margin-bottom: 6px; }}
    .feature p {{ font-size: 0.9rem; color: #64748b; }}
    .badge {{ display: inline-block; background: #01948E; color: #fff; padding: 4px 12px; border-radius: 20px; font-size: 0.85rem; margin-top: 8px; }}
    footer {{ text-align: center; padding: 40px 20px; color: #94a3b8; font-size: 0.9rem; }}
    .arrow {{ position: fixed; bottom: 28px; right: 90px; color: #01948E; font-size: 1rem; font-weight: 600; animation: bounce 2s infinite; z-index: 9990; }}
    @keyframes bounce {{ 0%,100%{{transform:translateY(0)}} 50%{{transform:translateY(-8px)}} }}
    @media(max-width:600px) {{ .features{{grid-template-columns:1fr}} .hero h1{{font-size:1.8rem}} }}
    code {{ background: #f1f5f9; padding: 2px 8px; border-radius: 4px; font-size: 0.85rem; color: #0f172a; }}
    pre {{ background: #1e293b; color: #e2e8f0; padding: 16px; border-radius: 8px; overflow-x: auto; font-size: 0.85rem; margin-top: 12px; }}
  </style>
</head>
<body>
  <div class="hero">
    <h1>ЦПР ПРОФИ</h1>
    <p>Центр профессионального развития. Обучение, аттестация, повышение квалификации по промышленной безопасности.</p>
  </div>

  <div class="content">
    <div class="card">
      <h2>Наши услуги</h2>
      <p>Мы предлагаем широкий спектр образовательных программ для специалистов в области промышленной безопасности, охраны труда и пожарной безопасности.</p>
      <div class="features">
        <div class="feature">
          <h3>Промышленная безопасность</h3>
          <p>Аттестация руководителей и специалистов по промбезопасности</p>
          <span class="badge">от 8 500 руб.</span>
        </div>
        <div class="feature">
          <h3>Охрана труда</h3>
          <p>Обучение и проверка знаний по охране труда</p>
          <span class="badge">от 5 000 руб.</span>
        </div>
        <div class="feature">
          <h3>Пожарная безопасность</h3>
          <p>Повышение квалификации по пожарно-техническому минимуму</p>
          <span class="badge">от 4 500 руб.</span>
        </div>
        <div class="feature">
          <h3>Дистанционное обучение</h3>
          <p>Все программы доступны в дистанционном формате</p>
          <span class="badge">Онлайн</span>
        </div>
      </div>
    </div>

    <div class="card">
      <h2>Контакты</h2>
      <p>Тел: +7 (343) 222-33-44<br>
      Email: info@groupprofi.ru<br>
      Адрес: г. Екатеринбург, ул. Примерная, 10</p>
      <p style="margin-top:12px; color:#01948E; font-weight:600;">Или напишите нам в чат справа внизу &rarr;</p>
    </div>

    <div class="card">
      <h2>Код для вставки на сайт</h2>
      <p>Чтобы добавить виджет на свой сайт, вставьте этот код перед <code>&lt;/body&gt;</code>:</p>
      <pre>&lt;link rel="stylesheet" href="{api_base}/static/messenger/widget.css"&gt;
&lt;script src="{api_base}/static/messenger/widget.js"
        data-widget-token="{token}"
        data-api-base-url="{api_base}"&gt;
&lt;/script&gt;</pre>
      <p style="margin-top:12px;">Или с ленивой загрузкой (рекомендуется):</p>
      <pre>&lt;script src="{api_base}/static/messenger/widget-loader.js"
        data-widget-token="{token}"
        data-api-base-url="{api_base}"
        data-load-after="3"&gt;
&lt;/script&gt;</pre>
    </div>
  </div>

  <div class="arrow">&larr; Напишите нам!</div>

  <footer>
    &copy; 2026 ЦПР ПРОФИ. Тестовая страница для проверки виджета мессенджера.
  </footer>

  <!-- ВИДЖЕТ МЕССЕНДЖЕРА -->
  <link rel="stylesheet" href="{api_base}/static/messenger/widget.css">
  <script src="{api_base}/static/messenger/widget.js"
          data-widget-token="{token}"
          data-api-base-url="{api_base}">
  </script>
</body>
</html>"""
    resp = HttpResponse(html, content_type="text/html")
    resp._skip_csp = True  # Эта страница имитирует внешний сайт — CSP не нужен
    return resp
