"""Регрессии уровня данных и пользовательских фильтров; настоящий браузер проверяется отдельно."""

import ast
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import tomllib
import unittest

import pandas as pd
from streamlit.testing.v1 import AppTest

import app

ROOT = Path(app.__file__).resolve().parent


def plot_specs(at):
    return [json.loads(element.proto.spec) for element in at.get("plotly_chart")]


class CookiesAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest()
                      for folder in ["data", "source", "audit", "datalens"]
                      for p in (ROOT / folder).rglob("*") if p.is_file() and "__pycache__" not in p.parts}
        cls.hashes[ROOT / "biscuit_recipe_analysis.ipynb"] = hashlib.sha256((ROOT / "biscuit_recipe_analysis.ipynb").read_bytes()).hexdigest()
        cls.tables = app.load_data()

    @classmethod
    def tearDownClass(cls):
        for path, digest in cls.hashes.items():
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise AssertionError(f"Приложение изменило материал исследования: {path}")

    def healthy(self, at):
        self.assertEqual(len(at.exception), 0, [e.message for e in at.exception])
        self.assertEqual(len(at.error), 0, [e.value for e in at.error])
        for fig in plot_specs(at):
            self.assertTrue(fig["data"])
            self.assertTrue(any(len(trace.get("x", [])) for trace in fig["data"]))
            self.assertTrue(fig["layout"]["xaxis"]["title"]["text"])
            self.assertTrue(fig["layout"]["yaxis"]["title"]["text"])

    def test_light_theme_contrast(self):
        theme = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text())["theme"]
        self.assertEqual(theme["base"], "light")
        def luminance(colour):
            values = [int(colour[i:i + 2], 16) / 255 for i in [1, 3, 5]]
            linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in values]
            return sum(a * b for a, b in zip(linear, [0.2126, 0.7152, 0.0722]))
        for foreground, background in [(theme["textColor"], theme["backgroundColor"]),
                                       (theme["textColor"], theme["secondaryBackgroundColor"]),
                                       ("#FFFFFF", theme["primaryColor"])]:
            a, b = sorted([luminance(foreground), luminance(background)])
            self.assertGreaterEqual((b + 0.05) / (a + 0.05), 4.5)

    def test_paths_and_application_dependencies(self):
        previous = Path.cwd()
        with tempfile.TemporaryDirectory() as folder:
            try:
                os.chdir(folder)
                app.load_data.clear()
                self.assertEqual(len(app.load_data()), 8)
            finally:
                os.chdir(previous)
        imports = [node for node in ast.walk(ast.parse((ROOT / "app.py").read_text()))
                   if isinstance(node, (ast.Import, ast.ImportFrom))]
        modules = {node.module.split(".")[0] for node in imports if isinstance(node, ast.ImportFrom)}
        modules |= {name.name.split(".")[0] for node in imports if isinstance(node, ast.Import) for name in node.names}
        self.assertLessEqual(modules, {"pathlib", "math", "pandas", "plotly", "streamlit"})

    def test_kpis_recipe_filters_and_download(self):
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20).run()
        self.healthy(at)
        self.assertEqual([m.value for m in at.metric], ["29,45 г", "35,11%", "4", "3"])
        choices = [[], ["nutriose"], ["promitor"], ["orafti"], ["fibruline"], ["nutriose", "fibruline"]]
        for selected in choices:
            at.multiselect(key="recipes").set_value(selected).run()
            self.healthy(at)
            expected = app.selected_rows(self.tables["recipes"], selected)
            figs = plot_specs(at)
            self.assertEqual(figs[0]["data"][0]["x"], expected.sucrose_g_per_100g_flour.tolist())
            self.assertEqual(figs[1]["data"][0]["x"], expected.sucrose_reduction_pct.tolist())
            self.assertEqual(figs[2]["data"][0]["x"], expected.fibre_ingredient_g_per_100g_flour.tolist())
            self.assertIn("Стандартная", figs[0]["data"][0]["y"])
            self.assertEqual(at.metric[1].value, "35,11%")
            exported = app.csv_bytes(app.comparison_table(self.tables, selected))
            self.assertTrue(exported.startswith(b"\xef\xbb\xbf"))
            csv = pd.read_csv(io.BytesIO(exported))
            self.assertEqual(len(csv), len(selected) + 1)
            self.assertEqual(csv.iloc[0]["Рецептура"], "Стандартная")
            for _, row in csv.iterrows():
                self.assertAlmostEqual(row["Снижение добавленной сахарозы, %"],
                                       (29.45 - row["Сахароза, г на 100 г муки"]) / 29.45 * 100)
        self.assertEqual(len(at.get("download_button")), 1)

    def test_force_physical_metrics_and_statistics(self):
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20).run()
        at.radio(key="section").set_value(app.SECTIONS[1]).run()
        at.multiselect(key="recipes").set_value(["fibruline"]).run()
        self.healthy(at)
        force = plot_specs(at)[0]
        self.assertEqual(len(force["data"][0]["x"]), 6)
        self.assertEqual(len(force["data"][1]["x"]), 2)
        expected = app.selected_rows(app.quality_data(self.tables).query("metric_id == 'breaking_strength_n'"), ["fibruline"])
        self.assertEqual(force["data"][1]["x"], expected.value.tolist())
        self.assertEqual(force["data"][1]["error_x"]["array"], (expected.mean_ci_high - expected.value).tolist())
        quality = app.quality_data(self.tables)
        for group, metrics in app.GROUPS.items():
            at.selectbox(key="physical_group").set_value(group).run()
            for metric in metrics:
                at.selectbox(key="physical_metric").set_value(metric).run()
                self.healthy(at)
                expected = app.selected_rows(quality[quality.metric_id.eq(metric)], ["fibruline"])
                self.assertEqual(plot_specs(at)[1]["data"][0]["x"], expected.value.tolist())
                at.radio(key="physical_mode").set_value("Разность со стандартом").run()
                fig = plot_specs(at)[1]
                self.assertEqual(fig["data"][0]["x"], expected.delta_vs_control.tolist())
                self.assertEqual(fig["layout"]["xaxis"]["title"]["text"], expected.delta_unit.iloc[0])
                at.radio(key="physical_mode").set_value("Значения").run()
        derived = quality[quality.metric_id.eq("colour_delta_e76")]
        self.assertTrue(derived.n_independent_batches.isna().all())
        self.assertTrue(derived.mean_ci_low.isna().all())
        for model in ["welch_holm", "dunnett_equal_variance"]:
            at.selectbox(key="stat_model").set_value(model).run()
            self.healthy(at)
            table = next(x.value for x in at.dataframe if "p с поправкой" in x.value.columns)
            expected_p = self.tables["statistical_results"].query("recipe_id == 'fibruline' and method_id == @model").p_adjusted.iloc[0]
            self.assertAlmostEqual(table.iloc[0]["p с поправкой"], expected_p)

    def test_sensory_all_attributes_and_empty_selections(self):
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20).run()
        at.radio(key="section").set_value(app.SECTIONS[1]).run()
        at.multiselect(key="recipes").set_value(["promitor"]).run()
        for metric in self.tables["sensory_means"].metric_id.unique():
            at.multiselect(key="sensory_metrics").set_value([metric]).run()
            self.healthy(at)
            fig = plot_specs(at)[2]
            self.assertEqual(len(fig["data"]), 2)
            for trace in fig["data"]:
                self.assertNotIn("error_x", trace)
                source = self.tables["sensory_means"]
                expected_value = source[(source.metric_id == metric) & (source.recipe_name_ru == trace["name"])].delta_vs_control_points.iloc[0]
                self.assertAlmostEqual(trace["x"][0], expected_value)
        at.radio(key="sensory_mode").set_value("Опубликованные средние").run()
        self.healthy(at)
        self.assertEqual(plot_specs(at)[2]["layout"]["xaxis"]["range"], [0, 100])
        at.multiselect(key="sensory_metrics").set_value([]).run()
        self.healthy(at)
        self.assertEqual(len(plot_specs(at)), 2)
        self.assertTrue(any("Выберите хотя бы один" in x.value for x in at.info))
        at.multiselect(key="recipes").set_value([]).run()
        self.healthy(at)
        self.assertEqual(len(plot_specs(at)[0]["data"][0]["x"]), 3)

    def test_candidates_and_section_navigation(self):
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20).run()
        at.radio(key="section").set_value(app.SECTIONS[2]).run()
        self.healthy(at)
        table = at.dataframe[0].value
        self.assertEqual(len(table), 4)
        self.assertEqual(table["Рецептура"].tolist(), self.tables["candidate_comparison"].recipe_name_ru.tolist())
        content = " ".join(x.value for x in at.markdown)
        self.assertIn("Кукурузный декстрин № 2", content)
        self.assertIn("Длинноцепочечный инулин", content)
        self.assertIn("Кукурузный декстрин № 1", content)
        for selected in [["orafti"], []]:
            at.multiselect(key="recipes").set_value(selected).run()
            self.healthy(at)
            if selected:
                self.assertEqual(len(at.dataframe[0].value), 1)
            else:
                self.assertEqual(len(at.dataframe), 0)
                self.assertTrue(any("только стандарт" in x.value for x in at.info))
        at.radio(key="section").set_value(app.SECTIONS[0]).run()
        self.healthy(at)


if __name__ == "__main__":
    unittest.main()
