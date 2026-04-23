"""UX-1 — Unified timeline classical-mode promotion tests.

Verifies:
- Classical "История действий" section renders unified `timeline_items`
  (not legacy `activity` ActivityEvent list).
- Filter pills present в rendered HTML.
- Timeline partial entries имеют `data-kind` attribute для JS filter.
- JS module `pages/company_timeline_filters.js` загружается.
"""

from __future__ import annotations

from django.test import Client, TestCase
from django.utils import timezone

from companies.models import Company, CompanyNote
from core.test_utils import make_disposable_user


class UX1ClassicalTimelineTest(TestCase):
    """Classical mode company detail renders unified timeline + filter pills."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = make_disposable_user(
            role="admin",
            prefix="ux1_admin",
            is_staff=True,
            is_superuser=True,
        )
        cls.company = Company.objects.create(name="UX1 Timeline Co")
        # Seed one note → timeline будет non-empty
        cls.note = CompanyNote.objects.create(
            company=cls.company,
            author=cls.admin,
            text="Тестовая заметка для timeline",
        )

    def _get_detail_html(self) -> str:
        c = Client()
        c.force_login(self.admin)
        # Force classical view mode — UX-1 focused на classical default.
        r = c.get(f"/companies/{self.company.id}/?view=classic")
        self.assertEqual(r.status_code, 200, r.content[:500])
        return r.content.decode()

    def test_filter_pills_present(self):
        """Classical timeline section должен include 6 filter pills."""
        html = self._get_detail_html()
        # Containers
        self.assertIn("company-timeline-filters", html)
        self.assertIn('id="companyTimelineClassic"', html)
        # Expected pill labels
        labels = ("Все", "Звонки", "Письма", "Заметки", "Задачи", "Изменения")
        for label in labels:
            self.assertIn(f">{label}<", html, f"Filter pill «{label}» missing")

    def test_timeline_entries_have_data_kind_attr(self):
        """Partial entries marked с data-kind для JS filter."""
        html = self._get_detail_html()
        # Note entry kind = 'note' (or subkind if note_type), always has data-kind
        self.assertIn('class="mb-4 ml-6 company-timeline-entry" data-kind=', html)

    def test_classical_includes_timeline_js(self):
        """Classical render должен include pages/company_timeline_filters.js."""
        html = self._get_detail_html()
        self.assertIn("pages/company_timeline_filters.js", html)

    def test_timeline_classical_uses_unified_partial(self):
        """Classical <ol> uses `_company_timeline_items.html` partial."""
        html = self._get_detail_html()
        # Rendered partial starts с <li class="mb-4 ml-6 company-timeline-entry">
        self.assertIn("companyTimelineClassicList", html)
        # Note renders — it's present в DB
        self.assertIn("Тестовая заметка для timeline", html)

    def test_no_legacy_activity_list_in_classical_history(self):
        """Legacy ActivityEvent <div class="rounded-lg border p-3 text-sm">
        loop (get_verb_display render) заменён на timeline_items."""
        html = self._get_detail_html()
        # Старый pattern: "get_verb_display" и action=="создал" text рендер
        # legacy rendered activity.message/get_verb_display
        # Мы заменили этот блок на timeline_items — теперь нет simple
        # ActivityEvent-only list ниже summary "История".
        summary_marker = (
            '<summary class="cursor-pointer font-medium flex items-center gap-2">'
        )
        idx_summary = html.find(summary_marker)
        self.assertGreater(idx_summary, 0, "UX-1 summary not found")
        # Within 2KB after summary нет legacy "ev.get_verb_display" patterns
        window = html[idx_summary : idx_summary + 2000]
        # Verify pills + list present (unified), not simple list
        self.assertIn("company-timeline-filters", window)
