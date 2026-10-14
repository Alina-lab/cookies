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
    "nutriose": "Горечь и подгорелый вкус изменились меньше всего.",
    "promitor": "Подгорелый вкус ощущался сильнее (+27,62 пункта).",
    "orafti": "Общая масса ингредиентов больше, чем в других рецептурах.",
    "fibruline": "Печенье стало короче и труднее поддавалось проколу.",
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


def chart_style(fig, title, unit, names, height=410):
    labels = [name.replace("Кукурузный декстрин №", "Кукурузный декстрин<br>№")
              .replace("Более растворимый инулин", "Более растворимый<br>инулин")
              .replace("Длинноцепочечный инулин", "Длинноцепочечный<br>инулин") for name in names]
    fig.update_layout(
        template="plotly_white", title={"text": title, "font": {"size": 18}}, height=height,
        font={"family": "Manrope, sans-serif", "size": 14, "color": "#29241F"},
        paper_bgcolor="#FFFDF8", plot_bgcolor="#FFFDF8", separators=", ",
        margin={"l": 16, "r": 44, "t": 58, "b": 58},
        hoverlabel={"bgcolor": "#FFFDF8", "font_color": "#29241F"},
        xaxis={"title": unit, "gridcolor": "#E9E1D5", "automargin": True, "zeroline": False},
        yaxis={"categoryorder": "array", "categoryarray": names, "autorange": "reversed",
               "tickmode": "array", "tickvals": names, "ticktext": labels, "ticklabelstandoff": 16,
               "automargin": True, "showgrid": False},
        legend={"orientation": "h", "y": 1.03, "x": 0},
    )
    return fig


def bar_chart(rows, field, title, unit):
    fig = go.Figure(go.Bar(
        x=rows[field].tolist(), y=rows.recipe_name_ru.tolist(), orientation="h",
        marker_color=rows.recipe_id.map(PALETTE).tolist(),
        text=[number(value) for value in rows[field]], textposition="outside", cliponaxis=False,
        texttemplate="\u2002%{text}",
        customdata=[[number(value), unit] for value in rows[field]],
        hovertemplate="%{y}<br><b>%{customdata[0]} %{customdata[1]}</b><extra></extra>",
    ))
    chart_style(fig, title, unit, rows.recipe_name_ru.tolist())
    fig.update_xaxes(range=[0, rows[field].max() * 1.20])
    fig.update_traces(marker_line_width=0)
    fig.update_layout(bargap=0.42)
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
    chart_style(fig, "Какое усилие нужно,<br>чтобы сломать печенье?", "Средняя сила<br>разрушения, Н", rows.recipe_name_ru.tolist())
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
            textposition="outside", texttemplate="\u2002%{text}", cliponaxis=False,
            customdata=[[label, number(value, signed=True)] for value in rows.delta_vs_control_points],
            hovertemplate="%{y}<br>%{customdata[0]}: <b>%{customdata[1]} пункта</b><extra></extra>",
        ))
    chart_style(fig, "Что изменилось во вкусе?", "Разница со стандартом,<br>пункты шкалы 0–100", rows.recipe_name_ru.tolist(), height=490)
    fig.update_layout(barmode="group", bargap=0.36, bargroupgap=0.24,
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
        st.markdown("Анализ опубликованного эксперимента Университета Рединга: стандартное печенье "
                    "и четыре рецептуры с частичной заменой сахарозы.")
        st.markdown("Каждую рецептуру изготовили в трёх независимых партиях. "
                    "Для выводов о массовом производстве нужны дополнительные испытания.")
        st.markdown("**Ограничения.** Допустимые изменения качества не заданы. "
                    "Все сахара готового печенья не измеряли. Индивидуальные оценки дегустаторов не опубликованы. "
                    "Средние описывают интенсивность ощущений, а не предпочтения покупателей.")
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
        <h1>Печенье</h1>
        <p class="hero-question">Как снижение сахарозы повлияло на вкус и структуру</p>
        <p class="hero-intro">Производителю нужно сократить добавленный сахар, сохранив привычные вкус и структуру печенья.
        Просто убрать его нельзя: сахар влияет на сладость и свойства теста при выпечке.</p>
        <p class="hero-intro">В опубликованном эксперименте сравнили стандарт и четыре рецептуры
        с пищевыми волокнами вместо части сахарозы. Разбираем, сколько сахарозы сократили
        и как изменились вкус и структура.</p>
        <div class="hero-kpis">
          <div class="hero-kpi"><span class="hero-kpi-value" data-kpi="reduction">{number(candidates.sucrose_reduction_pct.max())}%</span>
            <span class="hero-kpi-label">максимальное исследованное<br>снижение сахарозы</span></div>
          <div class="hero-kpi"><span class="hero-kpi-value" data-kpi="variants">{len(candidates)}</span>
            <span class="hero-kpi-label">экспериментальные<br>рецептуры</span></div>
        </div>
      </div>
      <figure class="hero-art">
        <img src="{image}" alt="Иллюстрация: песочное печенье на светлом льне" width="600" height="600">
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
            '<p class="section-intro">Пищевые волокна не расщепляются пищеварительными ферментами в тонком кишечнике. '
            'Ингредиенты с такими волокнами заменяют часть сахарозы по массе, но не обязаны сохранять прежний вкус и структуру.</p>'
            '<div class="ingredient-grid">' + "".join(cards) + '</div></section>')


def results_page(tables):
    hero(tables)
    ingredient_cards()
    left, right = st.columns(2, gap="medium")
    with left, st.container(key="sugar_panel"):
        recipes = tables["recipes"].query("recipe_id != 'control'").sort_values("recipe_order")
        show_plot(bar_chart(recipes, "sucrose_reduction_pct", "Сколько сахарозы заменили?", "Снижение сахарозы, %"), "sugar")
        st.html('<p class="chart-note"><strong>Больше всего сахарозы убрали с декстрином № 2.</strong> '
                'Процент показывает сокращение добавленной сахарозы относительно стандарта (29,45 г на 100 г муки). '
                'Все сахара готового печенья не измеряли.</p>')
    with right, st.container(key="force_panel"):
        show_plot(force_chart(tables), "force")
        st.html('<p class="chart-note"><strong>Длинноцепочечный инулин ближе всего к стандарту по средней силе разрушения.</strong> '
                'Прибор измеряет усилие, необходимое, чтобы сломать печенье. Эта величина не описывает всё качество продукта. '
                '<span class="note-detail">Каждую рецептуру изготовили в трёх независимых партиях. '
                'Для решения о внедрении нужны дополнительные испытания и согласованные требования к качеству.</span></p>')
    candidates = tables["candidate_comparison"].set_index("recipe_id")
    dextrin = candidates.loc["promitor"]
    inulin = candidates.loc["fibruline"]
    summary = (
        f'В четырёх рецептурах использовали на {number(candidates.sucrose_reduction_pct.min())}–'
        f'{number(candidates.sucrose_reduction_pct.max())}% меньше добавленной сахарозы, чем в стандартной. '
        f'С декстрином № 2 сократили сахарозу на {number(dextrin.sucrose_reduction_pct)}%, '
        f'но для разрушения печенья в среднем потребовалось на {number(-dextrin.breaking_relative_delta_pct)}% меньше усилия, '
        f'а подгорелый вкус оценили на {number(dextrin.burnt_taste_delta_points)} пункта выше по шкале интенсивности 0–100. '
        f'С длинноцепочечным инулином сахарозы на {number(inulin.sucrose_reduction_pct)}% меньше, '
        f'а средняя сила разрушения ближе всего к стандарту ({number(inulin.breaking_relative_delta_pct, signed=True)}%), '
        'хотя размеры и сопротивление проникновению тоже изменились. '
        'Одного варианта для всех задач эти данные не выделяют. '
        'Производству нужно выбрать приоритет, задать требования к качеству и повторить испытания.'
    )
    st.html('<div class="takeaway"><span class="takeaway-label">Что это значит</span>'
            f'<p>{summary}</p></div>')


def recommendation_cards(tables):
    candidates = tables["candidate_comparison"].set_index("recipe_id")
    sugar = candidates.sucrose_reduction_pct.idxmax()
    force = candidates.absolute_breaking_relative_delta_pct.idxmin()
    taste = candidates.index[(candidates.bitterness_delta_points.abs() == candidates.bitterness_delta_points.abs().min())
                             & (candidates.burnt_taste_delta_points.abs() == candidates.burnt_taste_delta_points.abs().min())][0]
    choices = [
        (sugar, "Сильнее сократить сахарозу", number(candidates.loc[sugar, "sucrose_reduction_pct"]) + "%",
         "снижение добавленной сахарозы",
         f'Печенье ломалось с меньшим средним усилием (−{number(-candidates.loc[sugar, "breaking_relative_delta_pct"])}%), '
         f'а средняя оценка подгорелого вкуса была выше на {number(candidates.loc[sugar, "burnt_taste_delta_points"])} пункта. '
         'Перед внедрением нужно понять, приемлемы ли эти изменения.',
         "При следующей выпечке проверьте силу разрушения и подгорелый вкус по согласованным требованиям."),
        (force, "Сохранить привычную прочность", number(candidates.loc[force, "breaking_relative_delta_pct"], signed=True) + "%",
         "изменение средней силы разрушения",
         f'Сахарозы на {number(candidates.loc[force, "sucrose_reduction_pct"])}% меньше, '
         'а средняя сила разрушения ближе к стандарту, чем у остальных вариантов. '
         'Но печенье стало короче на 7,87%, а сопротивление проникновению выросло на 32,24%.',
         "В новой серии выпечек сравните размеры, сопротивление проникновению и силу разрушения со стандартом."),
        (taste, "Меньше менять горечь и подгорелый вкус", number(candidates.loc[taste, "bitterness_delta_points"], signed=True) + " / " +
         number(candidates.loc[taste, "burnt_taste_delta_points"], signed=True),
         "горечь / подгорелый вкус, пункты 0–100",
         'Горечь и подгорелый вкус по средним оценкам изменились меньше, чем у других вариантов. '
         f'Однако для разрушения печенья в среднем потребовалось на {number(-candidates.loc[taste, "breaking_relative_delta_pct"])}% меньше усилия.',
         "При следующем испытании проверьте высоту печенья, силу разрушения и оценки вкусовых признаков."),
    ]
    cards = "".join(
        f'<article class="recommendation" data-recipe-id="{rid}" style="--recipe-color:{PALETTE[rid]}">'
        f'<p class="priority">{priority}</p><h3>{escape(candidates.loc[rid, "recipe_name_ru"])}</h3>'
        f'<div class="recommendation-result">{value}</div><p class="result-label">{label}</p>'
        f'<p class="recommendation-story">{story}</p>'
        f'<p class="next-test"><strong>Проверить дальше</strong>{next_test}</p></article>'
        for rid, priority, value, label, story, next_test in choices)
    st.html('<section class="recommendations"><h2>Три направления следующих испытаний</h2>'
            '<div class="recommendation-grid">' + cards + '</div></section>')


def candidates_page(tables):
    st.html('<div class="page-heading"><p class="eyebrow">От результатов — к следующему испытанию</p>'
            '<h1>Какие рецептуры стоит испытывать?</h1>'
            '<p>У каждой рецептуры свои преимущества и ограничения. Выбираем направления следующих испытаний.</p></div>')
    comparison = comparison_table(tables)
    with st.container(key="comparison_panel"):
        st.dataframe(comparison, hide_index=True, width="stretch", row_height=46,
                     column_config={"Рецептура": st.column_config.TextColumn(width=210),
                                    "Снижение сахарозы, %": st.column_config.NumberColumn(format="%.2f", width=174),
                                    "Изменение силы, %": st.column_config.NumberColumn(format="%+.2f", width=152),
                                    "На что обратить внимание": st.column_config.TextColumn(width="large")})
    with st.container(key="taste_panel"):
        show_plot(sensory_chart(tables), "taste")
        st.html('<p class="chart-note"><strong>По опубликованным средним горечь и подгорелый вкус ощущались сильнее, чем у стандарта.</strong> '
                'С декстрином № 1 разница меньше всего. Здесь оценивают интенсивность ощущений, а не то, насколько печенье понравилось покупателям.</p>')
    recommendation_cards(tables)
    st.download_button("Скачать сравнение · CSV", csv_bytes(comparison), file_name="cookies_comparison.csv", mime="text/csv",
                       type="tertiary", key="download")
    st.html('<div class="takeaway"><span class="takeaway-label">Следующий шаг</span>'
            '<p>Выбирать стоит под конкретную задачу производства. Ни один вариант пока не подтверждён как полноценная замена стандарта. '
            'Сначала нужно согласовать требования к качеству и повторить испытания выбранных рецептур.</p></div>')


def main():
    st.set_page_config(page_title="Печенье · снижение сахарозы", page_icon="🍪", layout="wide")
    apply_design()
    try:
        tables = load_data()
    except (OSError, ValueError, KeyError) as exc:
        st.error(f"Не удалось открыть данные исследования: {exc}")
        st.stop()
    with st.container(key="topbar", horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"):
        st.html('<div class="project-label">Аналитика рецептур</div>')
        about_research()
    with st.container(key="navigation"):
        page = st.radio("Страница", PAGES, horizontal=True, label_visibility="collapsed", key="page")
    if page == PAGES[0]:
        results_page(tables)
    else:
        candidates_page(tables)


if __name__ == "__main__":
    main()
