"""Две страницы с результатами Cookies; все расчёты уже выполнены в notebook."""

from math import isclose
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parent
PALETTE = {"control": "#64748B", "nutriose": "#0072B2", "promitor": "#C65D16",
           "orafti": "#00846A", "fibruline": "#8756A0"}
PAGES = ["Результаты", "Следующие испытания"]
SENSORY = {"sensory_flavour_bitter": "Горечь", "sensory_flavour_burnt": "Подгорелый вкус"}
FEATURES = {
    "nutriose": "Меньше изменений горечи и подгорелого вкуса.",
    "promitor": "Подгорелый вкус сильнее, чем у стандарта.",
    "orafti": "Масса рецепта выше; сначала уточнить состав.",
    "fibruline": "Выше сопротивление проколу; размеры меньше.",
}
FIBRE_INTRO = (
    "Сахароза — обычный столовый сахар. В печенье он влияет на сладость, цвет, структуру и поведение теста при выпечке. "
    "Пищевые волокна — компоненты растений или переработанных растительных углеводов. "
    "Они не перевариваются в тонком кишечнике так, как обычный сахар. "
    "В эксперименте часть сахарозы заменили кукурузными декстринами и ингредиентами с инулином из цикория. "
    "Такие ингредиенты могут заменить часть сахара по массе, но не обязательно сохраняют прежний вкус и структуру."
)
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
        template="plotly_white", title={"text": title, "font": {"size": 19}}, height=height,
        font={"family": "Arial, sans-serif", "size": 14, "color": "#172B4D"},
        paper_bgcolor="#FFFFFF", plot_bgcolor="#FFFFFF", separators=", ",
        margin={"l": 8, "r": 32, "t": 58, "b": 52},
        hoverlabel={"bgcolor": "#FFFFFF", "font_color": "#172B4D"},
        xaxis={"title": unit, "gridcolor": "#E8EDF2", "automargin": True},
        yaxis={"categoryorder": "array", "categoryarray": names, "autorange": "reversed",
               "tickmode": "array", "tickvals": names, "ticktext": labels, "automargin": True},
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
    fig.add_vline(x=0, line_color="#64748B", line_width=1)
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


def results_page(tables):
    st.title("Что показало исследование?")
    st.write("Производство хочет уменьшить добавленный сахар и понять, какие рецептуры стоит испытывать дальше.")
    st.markdown("**Зачем заменять сахар пищевыми волокнами?**\n\n" + FIBRE_INTRO)
    left, right = st.columns(2)
    candidates = tables["candidate_comparison"]
    left.metric("Самое большое снижение сахарозы", number(candidates.sucrose_reduction_pct.max()) + "%")
    right.metric("Варианты с заменой сахара", len(candidates))
    left, right = st.columns(2, gap="large")
    with left:
        recipes = tables["recipes"].query("recipe_id != 'control'").sort_values("recipe_order")
        show_plot(bar_chart(recipes, "sucrose_reduction_pct", "Сколько сахарозы заменили?", "Снижение сахарозы, %"), "sugar")
        st.caption("Больше всего сахарозы заменили с декстрином № 2. Проценты считаем от стандартного рецепта: "
                   "29,45 г сахарозы на 100 г муки. Граммы относятся к рецепту, а не к 100 г готового печенья.")
    with right:
        show_plot(bar_chart(force_rows(tables), "mean_of_batch_means", "Как изменилось усилие излома?", "Средняя сила разрушения, Н"), "force")
        st.caption("Сила разрушения — усилие, при котором печенье ломается. Во всех вариантах среднее ниже стандарта; "
                   "ближе всего — длинноцепочечный инулин. Меньшее усилие не означает худший продукт.")
    st.write("В изученных рецептурах сахарозы меньше, а усилие излома и вкус отличаются от стандарта. "
             "Следующий шаг — проверить подходящие варианты на дополнительных партиях.")


def candidates_page(tables):
    st.title("Какие рецептуры стоит испытывать?")
    comparison = comparison_table(tables)
    st.dataframe(comparison, hide_index=True, width="stretch", row_height=48,
                 column_config={"Рецептура": st.column_config.TextColumn(width="medium"),
                                "Снижение сахарозы, %": st.column_config.NumberColumn(format="%.2f"),
                                "Изменение силы, %": st.column_config.NumberColumn(format="%+.2f"),
                                "На что обратить внимание": st.column_config.TextColumn(width="large")})
    show_plot(sensory_chart(tables), "taste")
    st.caption("По опубликованным средним горечь и подгорелый вкус сильнее, чем у стандарта. "
               "У декстрина № 1 они изменились меньше всего. Здесь оценивали выраженность вкуса, а не предпочтения покупателей.")
    candidates = tables["candidate_comparison"].set_index("recipe_id")
    names = candidates.recipe_name_ru
    sugar = candidates.sucrose_reduction_pct.idxmax()
    force = candidates.absolute_breaking_relative_delta_pct.idxmin()
    taste = candidates.index[(candidates.bitterness_delta_points.abs() == candidates.bitterness_delta_points.abs().min())
                             & (candidates.burnt_taste_delta_points.abs() == candidates.burnt_taste_delta_points.abs().min())]
    st.markdown(f"- Если главное — сократить сахарозу: **{names[sugar]}**. Здесь снижение максимальное. "
                "Дальше проверить усилие излома и подгорелый вкус.\n"
                f"- Если важна близкая к стандарту сила разрушения: **{names[force]}**. Среднее ближе остальных. "
                "Размеры и сопротивление проколу требуют отдельной проверки.\n"
                f"- Если важны небольшие изменения горечи и подгорелого вкуса: **{names[taste[0]]}**. "
                "Эти признаки изменились меньше. При повторении проверить усилие излома и высоту печенья.")
    st.write("Эти варианты стоит испытать дальше. Исследование пока не подтверждает, что они сохраняют все свойства стандартного печенья.")
    st.download_button("Скачать сравнение · CSV", csv_bytes(comparison), file_name="cookies_comparison.csv", mime="text/csv")


def main():
    st.set_page_config(page_title="Cookies · результаты исследования", page_icon="🍪", layout="wide")
    st.markdown("""<style>
    .stMainBlockContainer {max-width: 1280px; padding-top: 2.5rem; padding-bottom: 2rem;}
    h1 {letter-spacing: -0.03em; font-size: 2rem;}
    [data-testid="stMetricValue"] {font-size: 2rem;}
    [data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p {color: #465874;}
    [data-testid="stCaptionContainer"] {opacity: 1;}
    </style>""", unsafe_allow_html=True)
    try:
        tables = load_data()
    except (OSError, ValueError, KeyError) as exc:
        st.error(f"Не удалось открыть данные исследования: {exc}")
        st.stop()
    st.caption("COOKIES · СНИЖЕНИЕ САХАРОЗЫ В ПЕСОЧНОМ ПЕЧЕНЬЕ")
    page = st.radio("Страница", PAGES, horizontal=True, label_visibility="collapsed", key="page")
    if page == PAGES[0]:
        results_page(tables)
    else:
        candidates_page(tables)
    st.caption("Что важно учесть: на каждую рецептуру — три независимые партии. Допустимые изменения качества не заданы. "
               "Все сахара готового печенья не измеряли.")
    st.caption("[Научная статья](https://doi.org/10.1016/j.lwt.2022.113837) · "
               "[Исходные данные](https://doi.org/10.17864/1947.000384) · "
               "[GitHub](https://github.com/Alina-lab/cookies) · "
               "[Подробный анализ: Jupyter Notebook](https://github.com/Alina-lab/cookies/blob/main/biscuit_recipe_analysis.ipynb)  \n"
               "Rodriguez-Garcia et al., 2022 · Данные CC BY 4.0.")


if __name__ == "__main__":
    main()
