"""Реальная проверка двух локальных страниц Chromium через Playwright.

Сначала: python -m streamlit run app.py
Тест не публикует приложение и не обращается к DataLens.
"""

import argparse
from pathlib import Path
import tempfile

import pandas as pd
from playwright.sync_api import sync_playwright, expect


def check_page(page, plots, points):
    expect(page.locator('[data-testid="stPlotlyChart"]')).to_have_count(plots)
    page.wait_for_function(
        "sizes => [...document.querySelectorAll('.js-plotly-plot')].map(e => e.data?.[0]?.x?.length).join() === sizes.join()",
        arg=points,
    )
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
    assert page.locator('[data-testid="stException"]').count() == 0
    assert page.locator('[data-testid="stMultiSelect"], [data-testid="stSelectbox"], [data-testid="stExpander"]').count() == 0
    expect(page.get_by_text("Ограничения: всего три независимые партии", exact=False)).to_be_visible()
    assert page.locator('[data-testid="stMainBlockContainer"]').evaluate("e => e.scrollHeight <= innerHeight * 2")


def run(executable=None, screenshots=None):
    with sync_playwright() as driver, tempfile.TemporaryDirectory(prefix="cookies-browser-") as folder:
        options = {"headless": True}
        if executable:
            options.update(executable_path=executable, args=["--no-sandbox", "--disable-dev-shm-usage"])
        browser = driver.chromium.launch(**options)
        page = browser.new_page(viewport={"width": 1366, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto("http://127.0.0.1:8501", wait_until="networkidle")
        page.get_by_role("heading", name="Что показало исследование?", exact=True).wait_for()
        check_page(page, 2, [4, 5])
        expect(page.locator('[data-testid="stMetricValue"]')).to_have_text(["35,11%", "4"])
        expect(page.locator('[data-testid="stDataFrame"]')).to_have_count(0)
        assert "Стандартная" not in page.locator(".js-plotly-plot").first.locator(".ytick").all_text_contents()
        assert "Стандартная" in page.locator(".js-plotly-plot").nth(1).locator(".ytick").all_text_contents()
        assert page.locator(".js-plotly-plot").evaluate_all("es => es.map(e => e._fullLayout.xaxis.title.text)") == [
            "Снижение добавленной сахарозы, %", "Средняя сила разрушения, Н"]
        # Настоящие подписи и подсказки обеих диаграмм.
        for index, value in [(0, "31,48"), (1, "61,91")]:
            graph = page.locator(".js-plotly-plot").nth(index)
            graph.locator(".barlayer .point").first.hover(force=True)
            expect(graph.locator(".hoverlayer")).to_contain_text(value)
        if screenshots:
            output = Path(screenshots)
            output.mkdir(parents=True, exist_ok=True)
            page.mouse.move(0, 0)
            page.screenshot(path=str(output / "results.png"), full_page=True)

        page.get_by_text("Следующие испытания", exact=True).click()
        page.get_by_role("heading", name="Какие рецептуры стоит испытывать?", exact=True).wait_for()
        check_page(page, 1, [4])
        expect(page.locator('[data-testid="stDataFrame"]')).to_have_count(1)
        expect(page.locator('[data-testid="stMetricValue"]')).to_have_count(0)
        assert page.locator(".js-plotly-plot").evaluate("e => e.data.map(t => t.name)") == ["Горечь", "Подгорелый вкус"]
        assert page.locator(".js-plotly-plot").evaluate("e => e._fullLayout.xaxis.title.text") == "Разность со стандартом, пункты шкалы 0–100"
        assert page.locator(".js-plotly-plot").evaluate("e => e.data[0].x[0]") == 5.66875
        # Наведение на две сенсорные серии, без выдуманных индивидуальных оценок.
        for index, value in [(0, "+5,67"), (1, "+5,94")]:
            page.locator(".barlayer .trace").nth(index).locator(".point").first.hover(force=True)
            expect(page.locator(".hoverlayer")).to_contain_text(value)
        page.mouse.move(0, 0)
        with page.expect_download() as download_info:
            page.get_by_role("button", name="Скачать сравнение · CSV", exact=True).click()
        downloaded = Path(folder) / "comparison.csv"
        download_info.value.save_as(downloaded)
        csv = pd.read_csv(downloaded)
        assert csv.shape == (4, 4)
        assert csv["Рецептура"].tolist() == ["Кукурузный декстрин № 1", "Кукурузный декстрин № 2",
                                           "Более растворимый инулин", "Длинноцепочечный инулин"]
        assert abs(csv.iloc[1]["Снижение сахарозы, %"] - 35.11035653650255) < 1e-12
        assert abs(csv.iloc[3]["Изменение силы, %"] - (-9.15343728945053)) < 1e-12
        if screenshots:
            page.locator('[data-testid="stMain"]').evaluate("e => e.scrollTop = 0")
            page.screenshot(path=str(output / "candidates.png"), full_page=True)
        # Возврат без старых виджетов и проверка более узкого ноутбука.
        page.set_viewport_size({"width": 1024, "height": 768})
        check_page(page, 1, [4])
        page.get_by_text("Результаты", exact=True).click()
        page.get_by_role("heading", name="Что показало исследование?", exact=True).wait_for()
        check_page(page, 2, [4, 5])
        if screenshots:
            page.screenshot(path=str(output / "results-1024.png"), full_page=True)
        assert not errors, errors
        print("Chromium: две страницы, три графика, таблица, наведение, CSV и ширины 1366/1024 проверены; ошибок JavaScript нет.")
        browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser-executable", help="Необязательно: путь к установленному Chromium")
    parser.add_argument("--screenshots", help="Необязательно: папка настоящих скриншотов")
    args = parser.parse_args()
    run(args.browser_executable, args.screenshots)
