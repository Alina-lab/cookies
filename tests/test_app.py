"""Проверки упрощённого интерфейса, чисел и сохранности исследования."""

import hashlib
import io
import re
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


def html_bodies(at):
    return " ".join(element.proto.body for element in at.get("html") if not element.proto.body.startswith("<style>"))


def plot_specs(at):
    return [json.loads(element.proto.spec) for element in at.get("plotly_chart")]


class CookiesAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest()
                      for folder in ["data", "source"]
                      for p in (ROOT / folder).rglob("*") if p.is_file() and "__pycache__" not in p.parts}
        for name in ["biscuit_recipe_analysis.ipynb", "audit_report.md", "DATA_LICENSE.md"]:
            path = ROOT / name
            cls.hashes[path] = hashlib.sha256(path.read_bytes()).hexdigest()
        cls.tables = app.load_data()

    @classmethod
    def tearDownClass(cls):
        for path, digest in cls.hashes.items():
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise AssertionError(f"Приложение изменило материал исследования: {path}")

    def healthy(self, at):
        self.assertEqual(len(at.exception), 0, [e.message for e in at.exception])
        self.assertEqual(len(at.error), 0, [e.value for e in at.error])
        self.assertEqual(len(at.radio), 1)
        self.assertEqual(list(at.radio[0].options), ["Результаты", "Следующие испытания"])
        for fig in plot_specs(at):
            self.assertTrue(fig["data"])
            self.assertTrue(all(len(trace.get("x", [])) for trace in fig["data"]))
            self.assertTrue(fig["layout"]["xaxis"]["title"]["text"])
            self.assertNotIn("error_x", fig["data"][0])
        self.assertEqual(len(at.get("popover")), 1)
        captions = " ".join(element.value for element in [*at.caption, *at.markdown])
        for text in ["трёх независимых партиях", "Допустимые изменения качества не заданы",
                     "Все сахара готового печенья не измеряли", "Rodriguez-Garcia et al., 2022",
                     "https://doi.org/10.17864/1947.000384",
                     "https://doi.org/10.1016/j.lwt.2022.113837",
                     "https://github.com/Alina-lab/cookies/blob/main/biscuit_recipe_analysis.ipynb"]:
            self.assertIn(text, captions)

    def test_results_page_kpis_and_two_charts(self):
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20).run()
        self.healthy(at)
        html = html_bodies(at)
        self.assertIn("<h1>Печенье</h1>", html)
        self.assertIn("Как снижение сахарозы повлияло на вкус и структуру", html)
        self.assertIn("Производителю нужно сократить добавленный сахар", html)
        self.assertEqual(re.findall(r'data-kpi="[^"]+">([^<]+)', html), ["35,11%", "4"])
        self.assertEqual(len(at.dataframe), 0)
        self.assertEqual(len(at.get("download_button")), 0)
        sugar, force = plot_specs(at)
        self.assertEqual(sugar["layout"]["xaxis"]["title"]["text"], "Снижение сахарозы, %")
        self.assertEqual(force["layout"]["xaxis"]["title"]["text"], "Средняя сила<br>разрушения, Н")
        self.assertEqual(len(sugar["data"][0]["x"]), 4)
        self.assertEqual(len(force["data"][0]["x"]), 5)
        self.assertNotIn("Стандартная", sugar["data"][0]["y"])
        self.assertEqual(force["data"][0]["y"][0], "Стандартная")
        for actual, expected in zip(sugar["data"][0]["x"], [31.48, 35.11, 22.11, 34.84]):
            self.assertAlmostEqual(actual, expected, places=2)
        for actual, expected in zip(force["data"][0]["x"], [61.91, 47.03, 44.98, 41.59, 56.24]):
            self.assertAlmostEqual(actual, expected, places=2)
        self.assertEqual(force["data"][0]["marker"]["color"], list(app.PALETTE.values()))
        self.assertEqual(sugar["data"][0]["marker"]["color"], list(app.PALETTE.values())[1:])
        self.assertEqual(sugar["data"][0]["type"], "bar")
        self.assertEqual(force["data"][0]["type"], "scatter")
        for fig in [sugar, force]:
            self.assertEqual(fig["layout"]["yaxis"]["ticklabelstandoff"], 16)
            self.assertGreaterEqual(fig["layout"]["height"], 410)
            self.assertGreaterEqual(fig["layout"]["font"]["size"], 14)
        summary = re.search(r'<div class="takeaway">.*?<p>(.*?)</p>', html).group(1)
        for value in ["22,11–35,11%", "27,35%", "27,62 пункта", "34,84%", "-9,15%"]:
            self.assertIn(value, summary)
        for term in ["задать требования к качеству", "в трёх независимых партиях"]:
            self.assertIn(term, html)
        for term in ["столовый сахар", "тонком кишечнике", "кукурузного крахмала", "Инулин из цикория"]:
            self.assertIn(term, html)
        self.assertEqual(html.count('class="ingredient-card"'), 3)
        self.assertIn("Все сахара готового печенья не измеряли", html)
        self.assertNotIn("doi.org", html)

    def test_candidates_table_taste_and_recommendations(self):
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20).run()
        at.radio(key="page").set_value("Следующие испытания").run()
        self.healthy(at)
        html = html_bodies(at)
        self.assertIn("<h1>Какие рецептуры стоит испытывать?</h1>", html)
        self.assertEqual(len(at.dataframe), 1)
        self.assertEqual(at.dataframe[0].value.shape, (4, 4))
        table = at.dataframe[0].value
        candidates = self.tables["candidate_comparison"].sort_values("recipe_order")
        self.assertEqual(table["Рецептура"].tolist(), candidates.recipe_name_ru.tolist())
        self.assertEqual(table["Изменение силы, %"].tolist(), candidates.breaking_relative_delta_pct.tolist())
        self.assertIn("общая масса ингредиентов больше", table.iloc[2, 3])
        for metric, value in [("length_mm", "7,87%"), ("penetration_resistance_ns", "32,24%")]:
            physical = self.tables["physical_recipe_summary"]
            delta = physical.query("recipe_id == 'fibruline' and metric_id == @metric").relative_delta_pct.iloc[0]
            self.assertEqual(app.number(abs(delta)) + "%", value)
            self.assertIn(value, html)
        figures = plot_specs(at)
        self.assertEqual(len(figures), 1)
        taste = figures[0]
        self.assertEqual([trace["name"] for trace in taste["data"]], ["Горечь", "Подгорелый вкус"])
        for trace, metric in zip(taste["data"], app.SENSORY):
            rows = self.tables["sensory_means"].query("metric_id == @metric and recipe_id != 'control'").sort_values("recipe_order")
            self.assertEqual(trace["x"], rows.delta_vs_control_points.tolist())
            self.assertEqual(trace["marker"]["color"], list(app.PALETTE.values())[1:])
        self.assertAlmostEqual(taste["data"][0]["x"][0], 5.66875)
        self.assertAlmostEqual(taste["data"][1]["x"][0], 5.9375)
        self.assertIn("пункты шкалы 0–100", taste["layout"]["xaxis"]["title"]["text"])
        self.assertEqual(taste["layout"]["yaxis"]["ticklabelstandoff"], 16)
        self.assertEqual(taste["layout"]["height"], 490)
        self.assertEqual(taste["layout"]["bargroupgap"], 0.24)
        self.assertEqual(taste["layout"]["font"]["size"], 14)
        self.assertIn("а не то, насколько печенье понравилось покупателям", html)
        self.assertEqual(re.findall(r'data-recipe-id="([^"]+)', html), ["promitor", "fibruline", "nutriose"])
        for value in ["35,11%", "−9,15%", "+5,67 / +5,94"]:
            self.assertIn(value.replace("−", "-"), html)
        self.assertEqual(html.count('class="recommendation-story"'), 3)
        for term in ["27,35%", "27,62 пункта", "34,84%", "24,04%", "32,24%", "7,87%",
                     "При следующей выпечке проверьте силу разрушения", "В новой серии выпечек сравните размеры",
                     "При следующем испытании проверьте высоту", "Ни один вариант пока не подтверждён как полноценная замена стандарта"]:
            self.assertIn(term, html)
        self.assertNotIn("doi.org", html)
        self.assertEqual(len(at.get("download_button")), 1)

    def test_navigation_does_not_leave_hidden_old_content(self):
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20).run()
        for page in ["Следующие испытания", "Результаты", "Следующие испытания", "Результаты"]:
            at.radio(key="page").set_value(page).run()
            self.healthy(at)
            self.assertEqual(html_bodies(at).count("<h1>"), 1)
            self.assertEqual(len(plot_specs(at)), 2 if page == "Результаты" else 1)
            self.assertEqual(len(at.dataframe), 0 if page == "Результаты" else 1)

    def test_csv_matches_compact_table_without_rounding_source_values(self):
        table = app.comparison_table(self.tables)
        exported = app.csv_bytes(table)
        self.assertTrue(exported.startswith(b"\xef\xbb\xbf"))
        loaded = pd.read_csv(io.BytesIO(exported))
        pd.testing.assert_frame_equal(loaded, table.reset_index(drop=True), check_exact=False)
        self.assertEqual(loaded.shape, (4, 4))
        self.assertAlmostEqual(loaded.iloc[1, 1], 35.11035653650255)
        self.assertTrue(loaded["Изменение силы, %"].lt(0).all())

    def test_paths_are_relative_to_app(self):
        previous = Path.cwd()
        with tempfile.TemporaryDirectory() as folder:
            try:
                os.chdir(folder)
                app.load_data.clear()
                self.assertEqual(len(app.load_data()), 4)
                at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20).run()
                self.healthy(at)
            finally:
                os.chdir(previous)

    def test_light_theme_contrast(self):
        theme = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text())["theme"]
        self.assertEqual(theme["base"], "light")

        def luminance(colour):
            values = [int(colour[i:i + 2], 16) / 255 for i in [1, 3, 5]]
            linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in values]
            return sum(a * b for a, b in zip(linear, [0.2126, 0.7152, 0.0722]))

        for foreground, background in [(theme["textColor"], theme["backgroundColor"]),
                                       ("#6F6255", theme["backgroundColor"]),
                                       ("#93562F", "#EFE8DA"),
                                       ("#FFFFFF", theme["primaryColor"])]:
            a, b = sorted([luminance(foreground), luminance(background)])
            self.assertGreaterEqual((b + 0.05) / (a + 0.05), 4.5)


if __name__ == "__main__":
    unittest.main()
