"""Проверки происхождения данных, уровней наблюдений и девяти таблиц."""

import hashlib
import json
import ast
import tempfile
from unittest.mock import patch
from urllib.request import Request
from io import BytesIO
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
TABLES = {
    "recipes": (5, ["recipe_id"]),
    "physical_measurements": (905, ["measurement_id"]),
    "physical_batch_means": (225, ["recipe_id", "batch_id", "metric_id"]),
    "physical_recipe_summary": (75, ["recipe_id", "metric_id"]),
    "sensory_means": (200, ["recipe_id", "metric_id"]),
    "metric_dictionary": (56, ["metric_id"]),
    "statistical_results": (8, ["statistical_result_id"]),
    "colour_summary": (5, ["recipe_id"]),
    "candidate_comparison": (4, ["recipe_id"]),
}


class DataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tables = {name: pd.read_csv(ROOT / "data" / f"{name}.csv") for name in TABLES}

    def test_source_manifest_and_optional_local_cache(self):
        manifest = json.loads((ROOT / "data" / "source_manifest.json").read_text())
        self.assertEqual(len(manifest), 6)
        for entry in manifest:
            with self.subTest(file=entry["file"]):
                self.assertEqual(len(entry["sha256"]), 64)
                self.assertGreater(entry["bytes"], 0)
                self.assertTrue(entry["url"].startswith("https://"))
                source = ROOT / "source" / entry["file"]
                if source.exists():
                    self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), entry["sha256"])

    def test_source_loader_uses_cache_and_rejects_corruption(self):
        # Тестируем функцию из notebook, не импортируя научные библиотеки в приложение.
        nb = json.loads((ROOT / "biscuit_recipe_analysis.ipynb").read_text())
        setup = ast.parse("".join(nb["cells"][1]["source"]))
        function = next(node for node in setup.body if isinstance(node, ast.FunctionDef) and node.name == "obtain_sources")
        namespace = {"hashlib": hashlib, "Request": Request, "urlopen": None}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "notebook_source_loader", "exec"), namespace)
        payload = b"fixture for transport integrity, not experimental data"
        entry = {"file": "test.xlsx", "url": "https://example.org/test.xlsx", "sha256": hashlib.sha256(payload).hexdigest()}
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder)
            cached = destination / entry["file"]
            cached.write_bytes(payload)
            with patch.dict(namespace, urlopen=lambda *args, **kwargs: self.fail("Повторная загрузка проверенного файла")):
                self.assertEqual(namespace["obtain_sources"]([entry], destination), {entry["file"]: entry["sha256"]})
            cached.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "не перезаписан"):
                namespace["obtain_sources"]([entry], destination)
            self.assertEqual(cached.read_bytes(), b"changed")
            cached.unlink()
            with patch.dict(namespace, urlopen=lambda *args, **kwargs: BytesIO(b"wrong response")):
                with self.assertRaisesRegex(ValueError, "файл не сохранён"):
                    namespace["obtain_sources"]([entry], destination)
            self.assertFalse(cached.exists())
            with patch.dict(namespace, urlopen=lambda *args, **kwargs: (_ for _ in ()).throw(OSError("offline"))):
                with self.assertRaisesRegex(RuntimeError, "Скачайте файл"):
                    namespace["obtain_sources"]([entry], destination)
            self.assertFalse(cached.exists())

    def test_keys_and_references(self):
        recipes = self.tables["recipes"]
        metrics = self.tables["metric_dictionary"]
        for name, (count, keys) in TABLES.items():
            frame = self.tables[name]
            with self.subTest(table=name):
                self.assertEqual(len(frame), count)
                self.assertFalse(frame[keys].isna().any().any())
                self.assertFalse(frame.duplicated(keys).any())
                if name != "recipes" and "recipe_id" in frame:
                    self.assertEqual(len(frame.merge(recipes[["recipe_id"]], validate="many_to_one")), len(frame))
                if name != "metric_dictionary" and "metric_id" in frame:
                    self.assertEqual(len(frame.merge(metrics[["metric_id"]], validate="many_to_one")), len(frame))

    def test_recipe_amounts_and_reduction(self):
        recipes = self.tables["recipes"].sort_values("recipe_order")
        self.assertEqual(recipes.recipe_id.tolist(), ["control", "nutriose", "promitor", "orafti", "fibruline"])
        np.testing.assert_allclose(recipes.sucrose_g_per_100g_flour, [29.45, 20.18, 19.11, 22.94, 19.19])
        np.testing.assert_allclose(recipes.sucrose_reduction_pct,
                                   (29.45 - recipes.sucrose_g_per_100g_flour) / 29.45 * 100)
        np.testing.assert_allclose(recipes.total_recipe_g_per_100g_flour, [175.95, 175.95, 175.95, 180.16, 175.95])
        self.assertIn("orafti_total_recipe_mass_differs", recipes.set_index("recipe_id").loc["orafti", "qc_flags"])

    def test_measurements_aggregate_within_batches(self):
        raw = self.tables["physical_measurements"]
        batches = self.tables["physical_batch_means"]
        self.assertEqual(raw.batch_id.nunique(), 15)
        self.assertTrue(raw[["experiment_date", "shared_day_id", "actual_specimen_id"]].isna().all().all())
        self.assertFalse(raw[["source_file", "source_sheet", "source_cell", "value"]].isna().any().any())
        keys = ["recipe_id", "batch_id", "metric_id"]
        computed = raw.groupby(keys).value.mean()
        saved = batches.set_index(keys).batch_mean.reindex(computed.index)
        self.assertFalse(saved.isna().any())
        np.testing.assert_allclose(computed, saved, rtol=1e-12)
        self.assertTrue(batches.groupby(["recipe_id", "metric_id"]).batch_id.nunique().eq(3).all())
        breaks = batches[batches.metric_id.eq("breaking_strength_n")].sort_values(["recipe_id", "source_replicate"])
        for _, group in breaks.groupby("recipe_id"):
            self.assertEqual(group.n_values_observed.tolist(), [10, 10, 9])
            self.assertEqual(group.n_values_missing.tolist(), [0, 0, 1])

    def test_recipe_means_control_and_units(self):
        summary = self.tables["physical_recipe_summary"]
        keys = ["recipe_id", "metric_id"]
        means = self.tables["physical_batch_means"].groupby(keys).batch_mean.mean()
        np.testing.assert_allclose(means, summary.set_index(keys).mean_of_batch_means.reindex(means.index))
        self.assertTrue(summary.n_independent_batches.eq(3).all())
        controls = summary[summary.recipe_id.eq("control")].set_index("metric_id").mean_of_batch_means
        np.testing.assert_allclose(summary.control_mean, summary.metric_id.map(controls))
        np.testing.assert_allclose(summary.delta_vs_control, summary.mean_of_batch_means - summary.control_mean)
        relative = summary[summary.relative_delta_allowed]
        np.testing.assert_allclose(relative.relative_delta_pct,
                                   (relative.mean_of_batch_means / relative.control_mean - 1) * 100, atol=1e-12)
        units = summary.groupby("metric_id").unit.agg(lambda values: set(values))
        for metric, unit in [("breaking_strength_n", "Н"), ("penetration_resistance_ns", "Н·с"),
                             ("length_mm", "мм"), ("moisture_pct", "%"), ("water_activity", "безразм.")]:
            self.assertEqual(units[metric], {unit})
        force = summary[summary.metric_id.eq("breaking_strength_n")].sort_values("recipe_order")
        np.testing.assert_allclose(force.mean_of_batch_means, [61.91, 47.03, 44.98, 41.59, 56.24], atol=0.005)

    def test_statistics_and_candidates_match_physical_results(self):
        stats = self.tables["statistical_results"]
        force = self.tables["physical_recipe_summary"].query("metric_id == 'breaking_strength_n'").set_index("recipe_id")
        self.assertTrue(stats.analysis_unit.eq("production_batch_mean").all())
        self.assertTrue(stats[["n_variant_batches", "n_control_batches"]].eq(3).all().all())
        self.assertTrue(stats.family_size.eq(4).all())
        self.assertEqual(set(stats.method_id), {"welch_holm", "dunnett_equal_variance"})
        primary = stats[stats.is_primary_model]
        self.assertEqual(len(primary), 4)
        self.assertTrue(primary.method_id.eq("welch_holm").all())
        np.testing.assert_allclose(stats.difference_vs_control, stats.recipe_id.map(force.delta_vs_control))
        np.testing.assert_allclose(stats.relative_difference_pct, stats.recipe_id.map(force.relative_delta_pct))
        self.assertTrue(stats.p_adjusted.between(0, 1).all())
        self.assertTrue(stats.ci_low.le(stats.ci_high).all())
        candidates = self.tables["candidate_comparison"]
        np.testing.assert_allclose(candidates.mean_breaking_strength_n, candidates.recipe_id.map(force.mean_of_batch_means))
        np.testing.assert_allclose(candidates.breaking_relative_delta_pct, candidates.recipe_id.map(force.relative_delta_pct))
        reductions = self.tables["recipes"].set_index("recipe_id").sucrose_reduction_pct
        np.testing.assert_allclose(candidates.sucrose_reduction_pct, candidates.recipe_id.map(reductions))

    def test_sensory_and_colour(self):
        sensory = self.tables["sensory_means"]
        self.assertEqual(sensory.metric_id.nunique(), 40)
        self.assertFalse(sensory.new_statistical_tests_allowed.any())
        self.assertTrue(sensory.n_independent_batches.isna().all())
        controls = sensory[sensory.recipe_id.eq("control")].set_index("metric_id").published_mean
        np.testing.assert_allclose(sensory.control_published_mean, sensory.metric_id.map(controls))
        np.testing.assert_allclose(sensory.delta_vs_control_points, sensory.published_mean - sensory.control_published_mean)
        candidates = self.tables["candidate_comparison"]
        for metric, column in [("sensory_flavour_bitter", "bitterness_delta_points"),
                               ("sensory_flavour_burnt", "burnt_taste_delta_points")]:
            scores = sensory[sensory.metric_id.eq(metric)].set_index("recipe_id").delta_vs_control_points
            np.testing.assert_allclose(candidates[column], candidates.recipe_id.map(scores))
        centres = self.tables["physical_recipe_summary"].query("metric_id in ['colour_l_star', 'colour_a_star', 'colour_b_star']")
        centres = centres.pivot(index="recipe_id", columns="metric_id", values="mean_of_batch_means")
        distance = np.linalg.norm(centres - centres.loc["control"], axis=1)
        np.testing.assert_allclose(self.tables["colour_summary"].set_index("recipe_id").delta_e76_vs_control.reindex(centres.index), distance)


if __name__ == "__main__":
    unittest.main()
