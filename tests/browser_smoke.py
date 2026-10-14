"""Необязательная проверка реальной локальной страницы Chromium через Playwright.

Сначала: python -m streamlit run app.py
Тест не публикует приложение и не обращается к DataLens.
"""

import argparse
from pathlib import Path
import tempfile

import pandas as pd
from playwright.sync_api import sync_playwright, expect


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
        page.get_by_role("heading", name="Снижение сахарозы и качество печенья").wait_for()
        page.locator(".js-plotly-plot .main-svg").first.wait_for()
        page.wait_for_function("document.querySelector('.js-plotly-plot')?.data?.[0]?.x?.length === 5")
        expect(page.get_by_role("link", name="GitHub: код и данные", exact=True)).to_have_attribute("href", "https://github.com/Alina-lab/cookies")
        expect(page.get_by_role("link", name="Jupyter Notebook", exact=True)).to_have_attribute("href", "https://github.com/Alina-lab/cookies/blob/main/biscuit_recipe_analysis.ipynb")
        page.get_by_text("Резюме исследования.", exact=True).wait_for()
        assert page.locator('[data-testid="stSidebar"] [data-testid="stMultiSelectTagsContainer"]').evaluate("e => e.clientHeight < 220")
        expect(page.locator('[data-testid="stMetricValue"]')).to_have_text(["29,45 г", "35,11%", "4", "3"])
        assert "Стандартная" in page.locator(".js-plotly-plot .ytick").all_text_contents()
        tags = page.locator('[data-tag] span[title]').evaluate_all("es => es.map(e => [e.clientWidth,e.scrollWidth,e.clientHeight,e.scrollHeight])")
        assert all(width == scroll_width and height == scroll_height for width, scroll_width, height, scroll_height in tags)
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
        assert page.locator('[data-testid="stSidebar"] [data-tag]').last.evaluate(
            "e => e.getBoundingClientRect().bottom <= e.closest('[data-testid=stMultiSelectTagsContainer]').getBoundingClientRect().bottom + 1")
        if screenshots:
            output = Path(screenshots); output.mkdir(parents=True, exist_ok=True)
            page.set_viewport_size({"width": 1366, "height": 1250})
            page.screenshot(path=str(output / "sugar.png"))
            page.set_viewport_size({"width": 1366, "height": 900})
        # Реальное наведение на столбец: подпись и числовое значение доступны.
        page.locator(".js-plotly-plot .barlayer .point").first.hover(force=True)
        expect(page.locator(".hoverlayer").first).to_contain_text("29,45")
        # Фильтр рецептур, контроль и скачивание настоящего файла браузером.
        for label in ["Декстрин № 2", "Более растворимый инулин", "Длинноцепочечный инулин"]:
            page.get_by_role("button", name="Remove " + label, exact=True).click()
        page.wait_for_function("document.querySelector('.js-plotly-plot')?.data?.[0]?.x?.length === 2")
        page.get_by_role("tab", name="Снижение, %", exact=True).click()
        expect(page.get_by_role("tab", name="Снижение, %", exact=True)).to_have_attribute("aria-selected", "true")
        with page.expect_download() as download_info:
            page.get_by_role("button", name="Скачать сравнение · CSV", exact=True).click()
        downloaded = Path(folder) / "comparison.csv"
        download_info.value.save_as(downloaded)
        csv = pd.read_csv(downloaded)
        assert csv["Рецептура"].tolist() == ["Стандартная", "Кукурузный декстрин № 1"]
        assert abs(csv.iloc[0]["Сахароза, г на 100 г муки"] - 29.45) < 1e-12
        # Физические фильтры и отрисовка точек.
        page.get_by_text("2. Изменение качества", exact=True).click()
        page.get_by_role("heading", name="Как изменилась измеренная структура печенья").wait_for()
        page.wait_for_function("document.querySelector('.js-plotly-plot')?.data?.[0]?.x?.length === 6")
        group = page.get_by_role("combobox", name="Группа показателей", exact=True)
        group.click()
        group.press("ArrowDown")
        page.get_by_role("option", name="Влажность и активность воды", exact=True).click()
        metric = page.get_by_role("combobox", name="Физический показатель", exact=True)
        metric.click()
        metric.press("ArrowDown")
        page.get_by_role("option", name="Активность воды · безразм.", exact=True).click()
        page.get_by_text("Разность со стандартом", exact=True).click()
        page.wait_for_function("document.querySelectorAll('.js-plotly-plot')[1]?._fullLayout?.xaxis?.title?.text === 'безразм.'")
        # Один сенсорный признак, переключение на опубликованные средние.
        sensory = page.locator('[data-testid="stMultiSelect"]').nth(1)
        sensory.get_by_role("button", name="Clear all", exact=True).click()
        page.get_by_text("Выберите хотя бы один сенсорный признак для графика и таблицы.", exact=True).wait_for()
        page.get_by_role("combobox", name="Сенсорные признаки", exact=True).fill("Сладость вкуса")
        page.get_by_role("option", name="Третий укус: вкус · Сладость вкуса", exact=True).click()
        page.wait_for_function("document.querySelectorAll('.js-plotly-plot')[2]?.data?.[0]?.x?.length === 1")
        page.get_by_text("Опубликованные средние", exact=True).click()
        page.locator('[data-testid="stPlotlyChart"]').nth(2).scroll_into_view_if_needed()
        page.wait_for_function("document.querySelectorAll('.js-plotly-plot')[2]?._fullLayout?.xaxis?.range?.[1] === 100")
        assert page.locator('[data-testid="stException"]').count() == 0
        if screenshots:
            page.mouse.move(900, 650); page.mouse.wheel(0, -4000)
            page.locator('[data-testid="stMain"]').evaluate("e => e.scrollTop = 450")
            page.screenshot(path=str(output / "quality.png"))
        page.get_by_text("3. Перспективные рецептуры", exact=True).click()
        page.get_by_role("heading", name="Три направления следующих испытаний").wait_for()
        page.get_by_role("heading", name="Сравнение выбранных кандидатов").wait_for()
        if screenshots:
            page.locator('[data-testid="stMain"]').evaluate("e => e.scrollTop = 0")
            page.screenshot(path=str(output / "candidates.png"))
        page.locator('[data-testid="stMultiSelect"]').first.get_by_role("button", name="Clear all", exact=True).click()
        page.get_by_text("Сейчас выбран только стандарт.", exact=False).wait_for()
        page.set_viewport_size({"width": 1024, "height": 768})
        page.get_by_text("1. Снижение сахарозы", exact=True).click()
        page.get_by_role("heading", name="Меньше сахарозы — другое соотношение ингредиентов").wait_for()
        page.wait_for_function("document.querySelector('.js-plotly-plot')?.data?.[0]?.x?.length === 1")
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
        assert page.locator('[data-testid="stException"]').count() == 0
        assert not errors, errors
        print("Chromium: три раздела, фильтры, CSV, графики и ширины 1366/1024 проверены; ошибок JavaScript нет.")
        browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser-executable", help="Необязательно: путь к установленному Chromium")
    parser.add_argument("--screenshots", help="Необязательно: локальная папка настоящих скриншотов")
    args = parser.parse_args()
    run(args.browser_executable, args.screenshots)
