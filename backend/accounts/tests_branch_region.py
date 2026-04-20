"""Тесты справочника BranchRegion (Положение 2025-2026)."""

from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase

from accounts.models import Branch, BranchRegion


class BranchRegionTests(TestCase):
    def setUp(self):
        self.ekb = Branch.objects.create(name="Екатеринбург", code="ekb")
        self.tmn = Branch.objects.create(name="Тюмень", code="tym")
        self.krd = Branch.objects.create(name="Краснодар", code="krd")

    def test_load_fixture_creates_regions(self):
        call_command("load_branch_regions", "--flush")
        self.assertTrue(
            BranchRegion.objects.filter(
                branch=self.ekb, region_name="Свердловская область"
            ).exists()
        )
        self.assertTrue(
            BranchRegion.objects.filter(branch=self.krd, region_name="Краснодарский край").exists()
        )
        self.assertTrue(
            BranchRegion.objects.filter(branch=self.tmn, region_name="Тюменская область").exists()
        )

    def test_common_pool_created_for_all_branches(self):
        call_command("load_branch_regions", "--flush")
        moscow_count = BranchRegion.objects.filter(
            region_name="Москва, Московская область", is_common_pool=True
        ).count()
        self.assertEqual(moscow_count, 3)

    def test_unique_constraint(self):
        BranchRegion.objects.create(branch=self.ekb, region_name="Тест")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                BranchRegion.objects.create(branch=self.ekb, region_name="Тест")

    def test_lookup_branch_by_region_non_pool(self):
        call_command("load_branch_regions", "--flush")
        region = BranchRegion.objects.filter(
            region_name="Сахалинская область", is_common_pool=False
        ).first()
        self.assertIsNotNone(region)
        self.assertEqual(region.branch, self.tmn)
