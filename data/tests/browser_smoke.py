"""Проверка дизайна Cookies в настоящем локальном браузере.

Сначала: python -m streamlit run app.py
Затем из корня проекта: python data/tests/browser_smoke.py
Проверяет локальное приложение и не публикует его.
"""

import argparse
from pathlib import Path
import tempfile

import pandas as pd
from playwright.sync_api import sync_playwright, expect


def check_page(page, plots, points):
    expect(page.locator('[data-testid="stPlotlyChart"]')).to_have_count(plots)
    titles = ["Снижение количества сахарозы<br>в экспериментальных рецептурах", "Сила разрушения печенья"] if plots == 2 else ["Изменение горечи<br>и подгорелого вкуса"]
    page.wait_for_function(
        "expected => { const p = [...document.querySelectorAll('.js-plotly-plot')]; return p.map(e=>e.data?.[0]?.x?.length).join()===expected.points.join() && p.map(e=>e._fullLayout?.title?.text).join()===expected.titles.join(); }",
        arg={"points": points, "titles": titles},
    )
    page.evaluate("document.fonts.ready")
    assert page.locator('[data-testid="stException"]').count() == 0
    expect(page).to_have_title("Печенье · снижение сахарозы")
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
    expect(page.get_by_role("button", name="Об исследовании", exact=True)).to_have_count(0)
    expect(page.locator('.takeaway')).to_have_count(0)
    # Источники и ограничения сохранены в README и notebook.
    assert page.get_by_role("link", name="Научная статья", exact=True).count() == 0
    assert page.get_by_text("Rodriguez-Garcia et al., 2022", exact=False).count() == 0
    ornaments = page.locator('[data-testid="stAppViewContainer"]').evaluate("""e =>
        ['::before', '::after'].map(pseudo => {
            const s=getComputedStyle(e, pseudo);
            return {display:s.display, position:s.position, left:parseFloat(s.left),
                    right:parseFloat(s.right), width:parseFloat(s.width), opacity:parseFloat(s.opacity),
                    pointer:s.pointerEvents, background:s.backgroundImage};
        })
    """)
    width = page.viewport_size['width']
    if width < 1360:
        assert all(decor['display'] == 'none' for decor in ornaments), ornaments
    else:
        for decor in ornaments:
            assert decor['display'] == 'block' and decor['position'] == 'fixed', decor
            assert decor['pointer'] == 'none' and 'data:image/svg+xml' in decor['background'], decor
        left, right = ornaments
        content = page.locator('.hero' if plots == 2 else '.page-heading').bounding_box()
        assert left['left'] >= 0 and left['left'] + left['width'] < content['x'], left
        assert right['right'] >= 0 and right['right'] + right['width'] < width - content['x'] - content['width'], right
        assert left['width'] > right['width'] and left['opacity'] > right['opacity'], ornaments


def check_chart_labels(page):
    for graph in page.locator('[data-testid="stPlotlyChart"]').all():
        graph.scroll_into_view_if_needed()
        page.wait_for_function("[...document.querySelectorAll('.js-plotly-plot')].every(e => Math.abs(e.clientWidth - e._fullLayout.width) < 2)")
        cropped = graph.evaluate("""e => {
            const box = e.querySelector('.main-svg').getBoundingClientRect();
            return [...e.querySelectorAll('.gtitle, .xtitle, .ytick text, .bartext, .textpoint text')]
              .filter(label => { const r=label.getBoundingClientRect(); return r.left < box.left-2 || r.right > box.right+2; })
              .map(label=>label.textContent);
        }""")
        assert not cropped, cropped
        overlap = graph.evaluate("""e => {
            const t=e.querySelector('.gtitle').getBoundingClientRect(), legend=e.querySelector('.legend');
            if (!legend) return false;
            const r=legend.getBoundingClientRect();
            return Math.min(t.right,r.right)>Math.max(t.left,r.left) && Math.min(t.bottom,r.bottom)>Math.max(t.top,r.top);
        }""")
        assert not overlap, "Легенда перекрывает заголовок"
        # Проверяем реальное расстояние от названий до области построения.
        label_gap = graph.evaluate("""e => {
            const p=e.querySelector('.js-plotly-plot'), svg=e.querySelector('.main-svg').getBoundingClientRect();
            return svg.left+p._fullLayout._size.l-Math.max(...[...e.querySelectorAll('.ytick text')].map(t=>t.getBoundingClientRect().right));
        }""")
        assert label_gap >= 12, f"Названия слишком близко к графику: {label_gap:.1f} px"
        assert graph.evaluate("e=>e.querySelector('.js-plotly-plot')._fullLayout.font.size") >= 14
        if graph.locator('.barlayer .trace').count() == 2:
            gaps = graph.evaluate("""e => {
                const groups=[...e.querySelectorAll('.barlayer .trace')].map(t=>[...t.querySelectorAll('.point path')].map(p=>p.getBoundingClientRect()));
                const within=groups[0].map((a,i)=>{const b=groups[1][i]; return Math.max(a.top,b.top)-Math.min(a.bottom,b.bottom);});
                const between=groups[0].slice(1).map((a,i)=>Math.min(a.top,groups[1][i+1].top)-Math.max(groups[0][i].bottom,groups[1][i].bottom));
                return {within:Math.min(...within), between:Math.min(...between)};
            }""")
            assert gaps['within'] >= 5, gaps
            assert gaps['between'] >= 20, gaps


def screenshot(page, directory, name):
    if directory:
        page.mouse.move(0, 0)
        page.locator('[data-testid="stMain"]').evaluate("e => e.scrollTop=0")
        page.screenshot(path=str(directory / name))


def run(executable=None, screenshots=None):
    output = Path(screenshots) if screenshots else None
    if output:
        output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as driver, tempfile.TemporaryDirectory(prefix="cookies-browser-") as folder:
        options = {"headless": True}
        if executable:
            options.update(executable_path=executable, args=["--no-sandbox", "--disable-dev-shm-usage"])
        browser = driver.chromium.launch(**options)
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto("http://127.0.0.1:8501", wait_until="networkidle")
        for width, height in [(1440, 1100), (1024, 900), (390, 844)]:
            page.set_viewport_size({"width": width, "height": height})
            page.get_by_text("Результаты", exact=True).click()
            page.get_by_role("heading", name="Печенье", exact=True).wait_for()
            check_page(page, 2, [4, 5])
            expect(page.locator(".hero-kpi-value")).to_have_text(["35,11%", "4"])
            expect(page.locator(".ingredient-card")).to_have_count(3)
            expect(page.locator(".hero-art img")).to_have_attribute("alt", "Иллюстрация: песочное печенье на светлом льне")
            page.wait_for_function("[...document.querySelectorAll('.hero-art img, .ingredient-icon')].every(e=>e.complete && e.naturalWidth>0)")
            assert page.locator(".hero").evaluate("e=>getComputedStyle(e).gridTemplateColumns.split(' ').length") == (1 if width == 390 else 2)
            assert page.locator(".ingredient-grid").evaluate("e=>getComputedStyle(e).gridTemplateColumns.split(' ').length") == (1 if width == 390 else 3)
            assert page.locator(".hero h1").evaluate("e=>getComputedStyle(e).fontFamily").startswith("Lora")
            check_chart_labels(page)
            assert page.locator(".js-plotly-plot").nth(1).evaluate("e=>e.data[0].type") == "scatter"
            for index, value, selector in [(0, "31,48", ".barlayer .point path"), (1, "61,91", ".scatterlayer .point")]:
                graph=page.locator(".js-plotly-plot").nth(index)
                graph.locator(selector).first.hover(force=True)
                expect(graph.locator(".hoverlayer")).to_contain_text(value)
            screenshot(page, output, f"results-{width}.png")
            page.get_by_text("Следующие испытания", exact=True).click()
            page.get_by_role("heading", name="Сравнение экспериментальных рецептур", exact=True).wait_for()
            check_page(page, 1, [4])
            expect(page.locator('[data-testid="stDataFrame"]')).to_have_count(1)
            page.locator('[data-testid="stDataFrame"] canvas').first.wait_for()
            expect(page.locator(".recommendation")).to_have_count(3)
            assert page.locator(".recommendation").evaluate_all("es=>es.map(e=>e.dataset.recipeId)") == ["promitor", "fibruline", "nutriose"]
            expect(page.locator(".recommendation-result")).to_have_text(["35,11%", "-9,15%", "+5,67 / +5,94"])
            expect(page.locator(".recommendation-story")).to_have_count(3)
            expect(page.locator('.recommendation[data-recipe-id="promitor"] .next-test strong')).to_have_count(0)
            expect(page.locator('.recommendation .next-test strong')).to_have_count(2)
            if width != 390:
                heights=page.locator('.recommendation').evaluate_all('es=>es.map(e=>e.getBoundingClientRect().height)')
                assert max(heights)-min(heights) < 2, heights
            assert page.locator(".recommendation-grid").evaluate("e=>getComputedStyle(e).gridTemplateColumns.split(' ').length") == (1 if width == 390 else 3)
            check_chart_labels(page)
            assert page.locator(".js-plotly-plot").evaluate("e=>e.data.map(t=>t.name)") == ["Горечь", "Подгорелый вкус"]
            for index, value in [(0, "+5,67"), (1, "+5,94")]:
                # У короткого столбца SVG-группа включает подпись за его краем.
                # Наводим курсор на сам столбец, а не на центр всей группы.
                page.locator(".barlayer .trace").nth(index).locator(".point path").first.hover(force=True)
                expect(page.locator(".hoverlayer")).to_contain_text(value)
            # Таблица остаётся доступной при внутренней прокрутке на телефоне.
            if width == 390:
                scroll=page.locator('[data-testid="stDataFrame"] .dvn-scroller')
                scroll.evaluate("e=>e.scrollLeft=e.scrollWidth-e.clientWidth")
                assert scroll.evaluate("e=>e.scrollLeft>0")
                scroll.evaluate("e=>e.scrollLeft=0")
            with page.expect_download() as info:
                page.get_by_role("button", name="Скачать сравнение · CSV", exact=True).click()
            downloaded=Path(folder)/f"comparison-{width}.csv"
            info.value.save_as(downloaded)
            csv=pd.read_csv(downloaded)
            assert csv.shape == (4, 4)
            assert csv["Рецептура"].tolist() == ["Кукурузный декстрин № 1", "Кукурузный декстрин № 2",
                                               "Более растворимый инулин", "Длинноцепочечный инулин"]
            assert abs(csv.iloc[1]["Снижение сахарозы, %"]-35.11035653650255)<1e-12
            assert csv.columns.tolist() == ["Рецептура", "Снижение сахарозы, %", "Изменение силы разрушения, %", "Особенности"]
            assert abs(csv.iloc[3]["Изменение силы разрушения, %"]-(-9.15343728945053))<1e-12
            # Два кадра дают браузеру завершить отрисовку canvas таблицы.
            page.evaluate("new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))")
            screenshot(page, output, f"candidates-{width}.png")
            if output:
                page.locator('.recommendations').scroll_into_view_if_needed()
                page.screenshot(path=str(output/f"recommendations-{width}.png"))
            assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
        assert not errors, errors
        print("Chromium: обе страницы, боковой декор и его скрытие на узких экранах, иллюстрация и иконки, карточки, три графика, таблица и CSV проверены на 1440/1024/390 px; ошибок JavaScript нет.")
        browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser-executable", help="Необязательно: путь к установленному Chromium")
    parser.add_argument("--screenshots", help="Папка для скриншотов страниц и карточек рекомендаций")
    args = parser.parse_args()
    run(args.browser_executable, args.screenshots)
