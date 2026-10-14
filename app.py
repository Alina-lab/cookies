"""Две страницы с результатами Cookies; все расчёты уже выполнены в notebook."""

from math import isclose
from pathlib import Path
from base64 import b64encode
from html import escape

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parent
PALETTE = {"control": "#7B7A74", "nutriose": "#A96934", "promitor": "#A24F3A",
           "orafti": "#5D7355", "fibruline": "#6C5446"}
PAGES = ["Результаты", "Следующие испытания"]
SENSORY = {"sensory_flavour_bitter": "Горечь", "sensory_flavour_burnt": "Подгорелый вкус"}
FEATURES = {
    "nutriose": "Меньше изменений горечи и подгорелого вкуса.",
    "promitor": "Подгорелый вкус сильнее, чем у стандарта.",
    "orafti": "Масса рецепта выше; сначала уточнить состав.",
    "fibruline": "Выше сопротивление проколу; размеры меньше.",
}
FILES = {"recipes": (5, ["recipe_id"]),
         "physical_recipe_summary": (75, ["recipe_id", "metric_id"]),
         "sensory_means": (200, ["recipe_id", "metric_id"]),
         "candidate_comparison": (4, ["recipe_id"])}


def number(value, signed=False):
    return format(float(value), "+.2f" if signed else ".2f").replace(".", ",")


@st.cache_data(show_spinner=False)
def load_data():
    tables = {}
    for name, (count, keys) in FILES.items():
        frame = pd.read_csv(ROOT / "data" / f"{name}.csv")
        if len(frame) != count or frame.duplicated(keys).any() or frame[keys].isna().any().any():
            raise ValueError(f"Нарушена структура {name}.csv.")
        tables[name] = frame
    recipes = tables["recipes"].sort_values("recipe_order")
    if recipes.recipe_id.tolist() != list(PALETTE):
        raise ValueError("Ожидались стандарт и четыре экспериментальные рецептуры.")
    control_sugar = recipes.iloc[0].sucrose_g_per_100g_flour
    if not isclose(control_sugar, 29.45):
        raise ValueError("Не совпадает контрольная дозировка сахарозы.")
    for row in recipes.itertuples():
        expected = (control_sugar - row.sucrose_g_per_100g_flour) / control_sugar * 100
        if not isclose(row.sucrose_reduction_pct, expected):
            raise ValueError("Нарушен расчёт снижения сахарозы.")
    force = force_rows(tables).set_index("recipe_id")
    if set(force.index) != set(PALETTE) or not force.n_independent_batches.eq(3).all() or not force.unit.eq("Н").all():
        raise ValueError("Нарушен уровень наблюдений или единица силы разрушения.")
    reference = force.loc["control", "mean_of_batch_means"]
    for row in force.itertuples():
        if not isclose(row.control_mean, reference) or not isclose(row.relative_delta_pct, (row.mean_of_batch_means / reference - 1) * 100):
            raise ValueError("Неверная контрольная основа силы разрушения.")
    sensory = tables["sensory_means"]
    if sensory.metric_id.nunique() != 40 or sensory.new_statistical_tests_allowed.any():
        raise ValueError("Нарушен статус опубликованных сенсорных средних.")
    tastes = sensory[sensory.metric_id.isin(SENSORY)].set_index(["recipe_id", "metric_id"])
    if len(tastes) != 10 or tastes.published_mean.isna().any() or tastes.delta_vs_control_points.isna().any():
        raise ValueError("Недостаточно опубликованных вкусовых средних.")
    for metric in SENSORY:
        reference = tastes.loc[("control", metric), "published_mean"]
        for row in tastes.xs(metric, level="metric_id").itertuples():
            if not isclose(row.control_published_mean, reference) or not isclose(row.delta_vs_control_points, row.published_mean - reference):
                raise ValueError("Неверная контрольная основа сенсорных разностей.")
    candidates = tables["candidate_comparison"].sort_values("recipe_order")
    if candidates.recipe_id.tolist() != list(PALETTE)[1:]:
        raise ValueError("Не совпадает набор кандидатов.")
    recipes = recipes.set_index("recipe_id")
    for row in candidates.itertuples():
        comparisons = [
            (row.sucrose_reduction_pct, recipes.loc[row.recipe_id, "sucrose_reduction_pct"]),
            (row.mean_breaking_strength_n, force.loc[row.recipe_id, "mean_of_batch_means"]),
            (row.breaking_relative_delta_pct, force.loc[row.recipe_id, "relative_delta_pct"]),
            (row.bitterness_delta_points, tastes.loc[(row.recipe_id, "sensory_flavour_bitter"), "delta_vs_control_points"]),
            (row.burnt_taste_delta_points, tastes.loc[(row.recipe_id, "sensory_flavour_burnt"), "delta_vs_control_points"]),
        ]
        if not all(isclose(a, b) for a, b in comparisons):
            raise ValueError("Сводка кандидатов расходится с подготовленными данными.")
    return tables


def force_rows(tables):
    return tables["physical_recipe_summary"].query("metric_id == 'breaking_strength_n'").sort_values("recipe_order")


def chart_style(fig, title, unit, names, height=365):
    labels = [name.replace("Кукурузный декстрин №", "Кукурузный декстрин<br>№")
              .replace("Более растворимый инулин", "Более растворимый<br>инулин")
              .replace("Длинноцепочечный инулин", "Длинноцепочечный<br>инулин") for name in names]
    fig.update_layout(
        template="plotly_white", title={"text": title, "font": {"size": 18}}, height=height,
        font={"family": "Manrope, sans-serif", "size": 14, "color": "#29241F"},
        paper_bgcolor="#FFFDF8", plot_bgcolor="#FFFDF8", separators=", ",
        margin={"l": 8, "r": 32, "t": 58, "b": 52},
        hoverlabel={"bgcolor": "#FFFDF8", "font_color": "#29241F"},
        xaxis={"title": unit, "gridcolor": "#E9E1D5", "automargin": True, "zeroline": False},
        yaxis={"categoryorder": "array", "categoryarray": names, "autorange": "reversed",
               "tickmode": "array", "tickvals": names, "ticktext": labels, "automargin": True, "showgrid": False},
        legend={"orientation": "h", "y": 1.03, "x": 0},
    )
    return fig


def bar_chart(rows, field, title, unit):
    fig = go.Figure(go.Bar(
        x=rows[field].tolist(), y=rows.recipe_name_ru.tolist(), orientation="h",
        marker_color=rows.recipe_id.map(PALETTE).tolist(),
        text=[number(value) for value in rows[field]], textposition="outside", cliponaxis=False,
        customdata=[[number(value), unit] for value in rows[field]],
        hovertemplate="%{y}<br><b>%{customdata[0]} %{customdata[1]}</b><extra></extra>",
    ))
    chart_style(fig, title, unit, rows.recipe_name_ru.tolist())
    fig.update_xaxes(range=[0, rows[field].max() * 1.20])
    fig.update_traces(marker_line_width=0)
    return fig


def force_chart(tables):
    rows = force_rows(tables)
    values = rows.mean_of_batch_means
    fig = go.Figure(go.Scatter(
        x=values.tolist(), y=rows.recipe_name_ru.tolist(), mode="markers+text",
        marker={"color": rows.recipe_id.map(PALETTE).tolist(), "size": 14},
        text=[number(value) for value in values], textposition="middle left", cliponaxis=False,
        customdata=[[number(value)] for value in values],
        hovertemplate="%{y}<br><b>%{customdata[0]} Н</b><extra></extra>",
    ))
    chart_style(fig, "Как изменилось усилие излома?", "Средняя сила разрушения, Н", rows.recipe_name_ru.tolist())
    fig.update_xaxes(range=[0, values.max() * 1.22])
    fig.add_vline(x=values.iloc[0], line_color=PALETTE["control"], line_dash="dot", line_width=1)
    return fig


def sensory_chart(tables):
    fig = go.Figure()
    for metric, label in SENSORY.items():
        rows = tables["sensory_means"].query("metric_id == @metric and recipe_id != 'control'").sort_values("recipe_order")
        bitter = metric == "sensory_flavour_bitter"
        fig.add_trace(go.Bar(
            x=rows.delta_vs_control_points.tolist(), y=rows.recipe_name_ru.tolist(), orientation="h",
            name=label, marker={"color": rows.recipe_id.map(PALETTE).tolist(),
                                "pattern": {"shape": "" if bitter else "/"}},
            text=[number(value, signed=True) for value in rows.delta_vs_control_points],
            textposition="outside", cliponaxis=False,
            customdata=[[label, number(value, signed=True)] for value in rows.delta_vs_control_points],
            hovertemplate="%{y}<br>%{customdata[0]}: <b>%{customdata[1]} пункта</b><extra></extra>",
        ))
    chart_style(fig, "Что изменилось во вкусе?", "Разница со стандартом,<br>пункты шкалы 0–100", rows.recipe_name_ru.tolist(), height=360)
    fig.update_layout(barmode="group", bargap=0.3,
                      margin={"t": 112}, title={"y": 0.98, "yanchor": "top"},
                      legend={"y": 1.04, "yanchor": "bottom", "x": 0, "xanchor": "left"})
    maximum = max(max(trace.x) for trace in fig.data)
    fig.update_xaxes(range=[0, maximum * 1.15])
    fig.add_vline(x=0, line_color=PALETTE["control"], line_width=1)
    return fig


def show_plot(fig, key):
    st.plotly_chart(fig, width="stretch", theme=None, key=key,
                    config={"displaylogo": False, "scrollZoom": False,
                            "modeBarButtonsToRemove": ["lasso2d", "select2d"], "locale": "ru"})


def comparison_table(tables):
    rows = tables["candidate_comparison"].sort_values("recipe_order").copy()
    rows["feature"] = rows.recipe_id.map(FEATURES)
    return rows[["recipe_name_ru", "sucrose_reduction_pct", "breaking_relative_delta_pct", "feature"]].rename(columns={
        "recipe_name_ru": "Рецептура", "sucrose_reduction_pct": "Снижение сахарозы, %",
        "breaking_relative_delta_pct": "Изменение силы, %", "feature": "На что обратить внимание",
    })


def csv_bytes(frame):
    return frame.to_csv(index=False).encode("utf-8-sig")



def asset_uri(name, mime):
    return f"data:{mime};base64," + b64encode((ROOT / "assets" / name).read_bytes()).decode("ascii")


def apply_design():
    css = (ROOT / "assets" / "styles.css").read_text()
    css = css.replace("__MANROPE__", asset_uri("manrope.ttf", "font/ttf"))
    css = css.replace("__LORA__", asset_uri("lora.ttf", "font/ttf"))
    st.html("<style>" + css + "</style>")


def about_research():
    with st.popover("Об исследовании", key="about", type="tertiary"):
        st.markdown("### Об исследовании и источниках")
        st.markdown("Эксперимент Университета Рединга: стандартное печенье и четыре рецептуры с частичной заменой сахарозы.")
        st.markdown("**Ограничения.** На каждую рецептуру — три независимые партии. Допустимые изменения качества не заданы. "
                    "Все сахара готового печенья не измеряли. Индивидуальных сенсорных оценок нет; опубликованные средние "
                    "показывают интенсивность признаков, а не предпочтения покупателей.")
        st.markdown("[Научная статья](https://doi.org/10.1016/j.lwt.2022.113837) · "
                    "[Исходные данные](https://doi.org/10.17864/1947.000384)  \n"
                    "[GitHub](https://github.com/Alina-lab/cookies) · "
                    "[Jupyter Notebook](https://github.com/Alina-lab/cookies/blob/main/biscuit_recipe_analysis.ipynb)")
        st.caption("Rodriguez-Garcia et al., 2022 · Данные CC BY 4.0.")
        st.caption("Изображение печенья создано для оформления проекта. Это не фотография образцов эксперимента. "
                   "Шрифты Manrope и Lora — SIL Open Font License 1.1.")


def hero(tables):
    candidates = tables["candidate_comparison"]
    image = asset_uri("cookies-hero.webp", "image/webp")
    st.html(f"""
    <section class="hero">
      <div class="hero-copy">
        <p class="eyebrow">Исследование рецептур / Университет Рединга</p>
        <h1>Cookies</h1>
        <p class="hero-question">Сахарозы меньше.<br>А что с печеньем?</p>
        <p class="hero-intro">Четыре рецептуры с пищевыми волокнами и стандартное печенье.
        Сравниваем, сколько сахара заменили и что изменилось в качестве.</p>
        <div class="hero-kpis">
          <div class="hero-kpi"><span class="hero-kpi-value" data-kpi="reduction">{number(candidates.sucrose_reduction_pct.max())}%</span>
            <span class="hero-kpi-label">максимальное исследованное<br>снижение сахарозы</span></div>
          <div class="hero-kpi"><span class="hero-kpi-value" data-kpi="variants">{len(candidates)}</span>
            <span class="hero-kpi-label">экспериментальные<br>рецептуры</span></div>
        </div>
      </div>
      <figure class="hero-art">
        <img src="{image}" alt="Иллюстрация: песочное печенье на светлом льне" width="600" height="600">
        <figcaption>Иллюстрация для проекта · не образцы эксперимента</figcaption>
      </figure>
    </section>
    """)


def ingredient_cards():
    icons = [
        '<path d="M10 17 24 9l14 8v16l-14 8-14-8Zm0 0 14 8 14-8M24 25v16"/>',
        '<path d="M24 8c-9 0-9 23 0 29 9-6 9-29 0-29ZM18 16h12M17 23h14M19 30h10M24 10v26M24 38 10 24c0 12 5 18 14 18 9 0 14-6 14-18Z"/>',
        '<path d="M24 20c-10-13-17-8-10-1l10 1c12-14 20-8 12-1ZM24 20v6c-10 0-6 8 0 16 6-8 10-16 0-16ZM23 30l7 2M20 34l6 2"/>',
    ]
    copy = [
        ("Сахароза", "Обычный столовый сахар. Влияет на сладость, цвет, структуру и поведение теста."),
        ("Кукурузные декстрины", "Два разных волокносодержащих ингредиента из переработанного кукурузного крахмала."),
        ("Инулин из цикория", "В эксперименте использовали два разных ингредиента с этим пищевым волокном."),
    ]
    cards = []
    for icon, (title, description) in zip(icons, copy):
        svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48" fill="none" stroke="#93562F" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{icon}</svg>'
        uri = "data:image/svg+xml;base64," + b64encode(svg.encode()).decode()
        cards.append(f'<article class="ingredient-card"><img class="ingredient-icon" src="{uri}" alt="" aria-hidden="true">'
                     f'<div><h3>{title}</h3><p>{description}</p></div></article>')
    st.html('<section class="ingredients"><h2>Зачем заменять сахар пищевыми волокнами?</h2>'
            '<p class="section-intro">Волокна не перевариваются в тонком кишечнике как обычный сахар. '
            'Ими можно заменить часть сахара по массе, но прежний вкус и структура не гарантированы.</p>'
            '<div class="ingredient-grid">' + "".join(cards) + '</div></section>')


def results_page(tables):
    hero(tables)
    ingredient_cards()
    left, right = st.columns(2, gap="medium")
    with left, st.container(key="sugar_panel"):
        recipes = tables["recipes"].query("recipe_id != 'control'").sort_values("recipe_order")
        show_plot(bar_chart(recipes, "sucrose_reduction_pct", "Сколько сахарозы заменили?", "Снижение сахарозы, %"), "sugar")
        st.html('<p class="chart-note"><strong>Больше всего — с декстрином № 2.</strong> '
                'Проценты считаем от стандартного рецепта: 29,45 г сахарозы на 100 г муки, а не готового печенья.</p>')
    with right, st.container(key="force_panel"):
        show_plot(force_chart(tables), "force")
        st.html('<p class="chart-note"><strong>Ближе по среднему — длинноцепочечный инулин.</strong> '
                'Сила разрушения — усилие излома. Меньшее значение само по себе не означает худшее качество. '
                '<span class="note-detail">Средние трёх партий; прежнее качество пока не доказано.</span></p>')
    st.html('<div class="takeaway"><span class="takeaway-label">Что это значит</span>'
            '<p>Сахарозы в рецептах стало меньше, а усилие излома и вкус изменились. '
            'Следующий шаг — испытать варианты под конкретную производственную задачу.</p></div>')


def recommendation_cards(tables):
    candidates = tables["candidate_comparison"].set_index("recipe_id")
    sugar = candidates.sucrose_reduction_pct.idxmax()
    force = candidates.absolute_breaking_relative_delta_pct.idxmin()
    taste = candidates.index[(candidates.bitterness_delta_points.abs() == candidates.bitterness_delta_points.abs().min())
                             & (candidates.burnt_taste_delta_points.abs() == candidates.burnt_taste_delta_points.abs().min())][0]
    choices = [
        (sugar, "Максимально сократить сахарозу", number(candidates.loc[sugar, "sucrose_reduction_pct"]) + "%",
         "снижение добавленной сахарозы", "Усилие излома и подгорелый вкус в следующих партиях."),
        (force, "Сохранить близкое усилие излома", number(candidates.loc[force, "breaking_relative_delta_pct"], signed=True) + "%",
         "изменение средней силы разрушения", "Размеры и сопротивление проколу: они тоже изменились."),
        (taste, "Меньше изменений вкуса", number(candidates.loc[taste, "bitterness_delta_points"], signed=True) + " / " +
         number(candidates.loc[taste, "burnt_taste_delta_points"], signed=True),
         "горечь / подгорелый вкус, пункты 0–100", "Усилие излома и высоту печенья при повторении."),
    ]
    cards = "".join(
        f'<article class="recommendation" data-recipe-id="{rid}" style="--recipe-color:{PALETTE[rid]}">'
        f'<p class="priority">{priority}</p><h3>{escape(candidates.loc[rid, "recipe_name_ru"])}</h3>'
        f'<div class="recommendation-result">{value}</div><p class="result-label">{label}</p>'
        f'<p class="next-test"><strong>Проверить дальше</strong>{next_test}</p></article>'
        for rid, priority, value, label, next_test in choices)
    st.html('<section class="recommendations"><h2>Три направления следующих испытаний</h2>'
            '<div class="recommendation-grid">' + cards + '</div></section>')


def candidates_page(tables):
    st.html('<div class="page-heading"><p class="eyebrow">От результатов — к следующему испытанию</p>'
            '<h1>Какие рецептуры стоит испытывать?</h1>'
            '<p>Сравним четыре варианта и выберем, что проверять в следующих партиях.</p></div>')
    comparison = comparison_table(tables)
    with st.container(key="comparison_panel"):
        st.dataframe(comparison, hide_index=True, width="stretch", row_height=46,
                     column_config={"Рецептура": st.column_config.TextColumn(width="medium"),
                                    "Снижение сахарозы, %": st.column_config.NumberColumn(format="%.2f"),
                                    "Изменение силы, %": st.column_config.NumberColumn(format="%+.2f"),
                                    "На что обратить внимание": st.column_config.TextColumn(width="large")})
    with st.container(key="taste_panel"):
        show_plot(sensory_chart(tables), "taste")
        st.html('<p class="chart-note"><strong>Горечь и подгорелый вкус выше стандарта по опубликованным средним.</strong> '
                'У декстрина № 1 они изменились меньше всего. Это интенсивность ощущений, а не предпочтения покупателей.</p>')
    recommendation_cards(tables)
    st.download_button("Скачать сравнение · CSV", csv_bytes(comparison), file_name="cookies_comparison.csv", mime="text/csv",
                       type="tertiary", key="download")
    st.html('<div class="takeaway"><span class="takeaway-label">Следующий шаг</span>'
            '<p>Эти рецептуры стоит испытать дальше. Сохранение всех свойств стандартного печенья пока не подтверждено.</p></div>')


def main():
    st.set_page_config(page_title="Cookies · исследование рецептур", page_icon="🍪", layout="wide")
    apply_design()
    try:
        tables = load_data()
    except (OSError, ValueError, KeyError) as exc:
        st.error(f"Не удалось открыть данные исследования: {exc}")
        st.stop()
    with st.container(key="topbar", horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"):
        st.html('<div class="project-label">ПРОЕКТ № 3 <span>Аналитика рецептур</span></div>')
        about_research()
    with st.container(key="navigation"):
        page = st.radio("Страница", PAGES, horizontal=True, label_visibility="collapsed", key="page")
    if page == PAGES[0]:
        results_page(tables)
    else:
        candidates_page(tables)


if __name__ == "__main__":
    main()
