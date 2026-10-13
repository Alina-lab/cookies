"""Cookies: представление готового исследования без сети и новых статистических тестов."""

from pathlib import Path
from math import isclose

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parent
PALETTE = {"control": "#64748B", "nutriose": "#0072B2", "promitor": "#C65D16",
           "orafti": "#00846A", "fibruline": "#8756A0"}
SECTIONS = ["1. Снижение сахарозы", "2. Изменение качества", "3. Перспективные рецептуры"]
GROUPS = {
    "Механические характеристики": ["breaking_strength_n", "penetration_resistance_ns", "number_of_peaks", "deformation_at_break_mm"],
    "Размеры": ["length_mm", "width_mm", "height_mm"],
    "Влажность и активность воды": ["moisture_pct", "water_activity", "weight_loss_pct"],
    "Цвет": ["colour_l_star", "colour_a_star", "colour_b_star", "colour_delta_e76"],
}
KEY_SENSORY = ["sensory_flavour_sweet", "sensory_first_bite_firmnessofbite", "sensory_fourth_bite_crunchy",
               "sensory_fourth_bite_crumbly", "sensory_flavour_bitter", "sensory_flavour_burnt"]
FILES = {"recipes": (5, ["recipe_id"]), "physical_recipe_summary": (75, ["recipe_id", "metric_id"]),
         "physical_batch_means": (225, ["recipe_id", "batch_id", "metric_id"]),
         "sensory_means": (200, ["recipe_id", "metric_id"]), "metric_dictionary": (56, ["metric_id"]),
         "statistical_results": (8, ["statistical_result_id"]), "colour_summary": (5, ["recipe_id"]),
         "candidate_comparison": (4, ["recipe_id"])}


def number(value, digits=2, signed=False):
    if pd.isna(value):
        return "—"
    return format(float(value), ("+" if signed else "") + f".{digits}f").replace(".", ",")


@st.cache_data(show_spinner=False)
def load_data():
    tables = {}
    for name, (count, keys) in FILES.items():
        frame = pd.read_csv(ROOT / "data" / f"{name}.csv")
        if len(frame) != count or frame.duplicated(keys).any() or frame[keys].isna().any().any():
            raise ValueError(f"Нарушена структура {name}.csv; нужен контроль исходной подготовки.")
        tables[name] = frame
    recipes = tables["recipes"].sort_values("recipe_order")
    if recipes.recipe_id.tolist() != list(PALETTE):
        raise ValueError("Не совпадает набор пяти рецептур.")
    control = recipes.iloc[0].sucrose_g_per_100g_flour
    if not isclose(control, 29.45) or not isclose(recipes.sucrose_reduction_pct.max(), 35.11035653650255):
        raise ValueError("Не совпадают контрольные значения сахарозы.")
    for row in recipes.itertuples():
        if not isclose(row.sucrose_reduction_pct, (control - row.sucrose_g_per_100g_flour) / control * 100):
            raise ValueError("Нарушен расчёт снижения сахарозы.")
    batches = tables["physical_batch_means"]
    if not batches.groupby(["recipe_id", "metric_id"]).batch_id.nunique().eq(3).all():
        raise ValueError("Ожидались три партии на рецептуру и показатель.")
    physical = tables["physical_recipe_summary"]
    if not physical.n_independent_batches.eq(3).all():
        raise ValueError("Нарушен уровень независимых наблюдений.")
    for metric, rows in physical.groupby("metric_id"):
        reference = rows.loc[rows.recipe_id.eq("control"), "mean_of_batch_means"].iloc[0]
        if not all(isclose(value, reference) for value in rows.control_mean):
            raise ValueError(f"Неверная контрольная основа: {metric}.")
    sensory = tables["sensory_means"]
    if sensory.metric_id.nunique() != 40 or sensory.new_statistical_tests_allowed.any():
        raise ValueError("Нарушен статус опубликованных сенсорных средних.")
    return tables


def quality_data(tables):
    """Читаем сводки и существующий ΔE; новых наблюдений или интервалов нет."""
    physical = tables["physical_recipe_summary"].query("metric_role != 'context'").copy()
    physical = physical.rename(columns={"mean_of_batch_means": "value"})
    definitions = tables["metric_dictionary"].set_index("metric_id").definition_ru
    physical["definition_ru"] = physical.metric_id.map(definitions)
    colour = tables["colour_summary"].copy().rename(columns={"delta_e76_vs_control": "value"})
    colour["metric_name_ru"] = "Разница цвета ΔE*76"
    colour["control_mean"] = colour.loc[colour.recipe_id.eq("control"), "value"].iloc[0]
    colour["delta_vs_control"] = colour.value - colour.control_mean
    colour["delta_unit"] = colour.unit
    colour["definition_ru"] = definitions["colour_delta_e76"]
    # У расстояния цветовых центров нет независимых партийных ΔE, SD и CI.
    colour["n_independent_batches"] = pd.NA
    return pd.concat([physical, colour], ignore_index=True).sort_values(["metric_id", "recipe_order"])


def selected_rows(frame, selected):
    return frame[frame.recipe_id.isin(["control", *selected])].copy().sort_values("recipe_order")


def chart_style(fig, title, x_title, names, height=390):
    wrapped = [name.replace("Кукурузный декстрин №", "Кукурузный декстрин<br>№")
               .replace("Более растворимый инулин", "Более растворимый<br>инулин")
               .replace("Длинноцепочечный инулин", "Длинноцепочечный<br>инулин") for name in names]
    fig.update_layout(template="plotly_white", title={"text": title, "font": {"size": 16}},
                      height=height, margin={"l": 8, "r": 48, "t": 56, "b": 46},
                      font={"family": "Arial, sans-serif", "size": 13, "color": "#172B4D"},
                      paper_bgcolor="#FFFFFF", plot_bgcolor="#FFFFFF", separators=", ",
                      hoverlabel={"bgcolor": "#FFFFFF", "font_color": "#172B4D"},
                      legend={"orientation": "h", "y": -0.24, "x": 0},
                      xaxis={"title": x_title, "gridcolor": "#E8EDF2", "zerolinecolor": "#64748B", "automargin": True},
                      yaxis={"title": "Рецептура", "categoryorder": "array", "categoryarray": names,
                             "autorange": "reversed", "automargin": True, "tickmode": "array",
                             "tickvals": names, "ticktext": wrapped})
    return fig


def bar_chart(frame, value, title, unit):
    rows = frame.dropna(subset=[value])
    digits = 4 if rows.get("metric_id", pd.Series(dtype=str)).eq("water_activity").any() else 2
    fig = go.Figure(go.Bar(x=rows[value].tolist(), y=rows.recipe_name_ru.tolist(), orientation="h",
                          marker_color=rows.recipe_id.map(PALETTE).tolist(),
                          text=[number(x, digits) for x in rows[value]], textposition="outside", cliponaxis=False,
                          customdata=[[number(x, digits), unit] for x in rows[value]],
                          hovertemplate="%{y}<br><b>%{customdata[0]} %{customdata[1]}</b><extra></extra>"))
    fig = chart_style(fig, title, unit, rows.recipe_name_ru.tolist())
    low, high = min(0, rows[value].min()), max(0, rows[value].max())
    span = high - low or 1
    fig.update_xaxes(range=[low - (span * 0.14 if low < 0 else 0), high + span * 0.20])
    return fig


def force_chart(rows, batches):
    rows = rows.sort_values("recipe_order")
    batches = batches[batches.recipe_id.isin(rows.recipe_id)].sort_values(["recipe_order", "source_replicate"])
    details = [[number(r.batch_mean), str(r.source_replicate), str(r.batch_id)] for r in batches.itertuples()]
    fig = go.Figure(go.Scatter(x=batches.batch_mean.tolist(), y=batches.recipe_name_ru.tolist(), mode="markers",
                              name="Партийные средние", marker={"size": 8, "color": batches.recipe_id.map(PALETTE).tolist()},
                              customdata=details,
                              hovertemplate="%{y}<br>Партия %{customdata[1]}: <b>%{customdata[0]} Н</b>"
                                            "<br>Условный ID: %{customdata[2]}<extra></extra>"))
    fig.add_trace(go.Scatter(x=rows.value.tolist(), y=rows.recipe_name_ru.tolist(), mode="markers+text",
                            name="Среднее и отдельный 95% t-интервал", text=[number(x) for x in rows.value],
                            textposition="top right", cliponaxis=False,
                            marker={"size": 12, "symbol": "diamond", "color": rows.recipe_id.map(PALETTE).tolist()},
                            error_x={"type": "data", "symmetric": False, "color": "#64748B", "thickness": 1.5,
                                     "array": (rows.mean_ci_high - rows.value).tolist(),
                                     "arrayminus": (rows.value - rows.mean_ci_low).tolist()},
                            customdata=[[number(r.value), number(r.mean_ci_low), number(r.mean_ci_high),
                                         number(r.delta_vs_control, signed=True), number(r.relative_delta_pct, signed=True)]
                                        for r in rows.itertuples()],
                            hovertemplate="%{y}<br>Среднее: <b>%{customdata[0]} Н</b><br>95% t-интервал: "
                                          "%{customdata[1]}…%{customdata[2]} Н<br>Разность: %{customdata[3]} Н"
                                          "<br>Изменение: %{customdata[4]}%<br>Независимых партий: 3<extra></extra>"))
    fig = chart_style(fig, "Сила разрушения: три партии и среднее рецептуры", "Сила разрушения, Н",
                      rows.recipe_name_ru.tolist(), height=460)
    low, high = min(0, rows.mean_ci_low.min()), rows.mean_ci_high.max()
    fig.update_xaxes(range=[low, high + (high - low) * 0.14])
    return fig


def sensory_chart(rows, labels, difference):
    field = "delta_vs_control_points" if difference else "published_mean"
    fig = go.Figure()
    for rid, group in rows.groupby("recipe_id", sort=False):
        fig.add_trace(go.Scatter(x=group[field].tolist(), y=group.metric_label_ru.tolist(), mode="markers",
                                name=group.recipe_name_ru.iloc[0], marker={"size": 10, "color": PALETTE[rid]},
                                customdata=[[number(r.published_mean), number(r.control_published_mean),
                                             number(r.delta_vs_control_points, signed=True)] for r in group.itertuples()],
                                hovertemplate="%{y}<br>Среднее: <b>%{customdata[0]}</b><br>Стандарт: %{customdata[1]}"
                                              "<br>Разность: %{customdata[2]} пункта<extra>%{fullData.name}</extra>"))
    title = "Разности опубликованных сенсорных средних" if difference else "Опубликованные сенсорные средние"
    unit = "Разность со стандартом, пункты шкалы 0–100" if difference else "Интенсивность, шкала 0–100"
    fig = chart_style(fig, title, unit, labels, height=max(430, len(labels) * 45 + 180))
    short_labels = [label.replace(" · ", "<br>", 1) for label in labels]
    fig.update_yaxes(title="Сенсорный признак", ticktext=short_labels)
    fig.update_layout(legend={"orientation": "h", "y": -0.18, "x": 0, "font": {"size": 12}},
                      margin={"l": 8, "r": 28, "t": 55, "b": 100})
    if not difference:
        fig.update_xaxes(range=[0, 100])
    else:
        fig.add_vline(x=0, line_color="#64748B", line_dash="dot")
    return fig


def show_plot(fig, key):
    st.plotly_chart(fig, width="stretch", theme=None, key=key,
                    config={"displaylogo": False, "scrollZoom": False,
                            "modeBarButtonsToRemove": ["lasso2d", "select2d"], "locale": "ru"})


def show_table(frame, rename, digits=2):
    view = frame[list(rename)].rename(columns=rename)
    counts = {"source_replicate", "n_values_observed", "n_independent_batches"}
    config = {label: st.column_config.NumberColumn(label, format="%d" if field in counts else f"%.{digits}f")
              for field, label in rename.items() if pd.api.types.is_numeric_dtype(frame[field])}
    st.dataframe(view, hide_index=True, width="stretch", column_config=config)
    return view


def csv_bytes(frame):
    return frame.to_csv(index=False).encode("utf-8-sig")


def comparison_table(tables, selected):
    recipes = selected_rows(tables["recipes"], selected)
    force = tables["physical_recipe_summary"].query("metric_id == 'breaking_strength_n'")
    cols = ["recipe_id", "mean_of_batch_means", "delta_vs_control", "relative_delta_pct", "n_independent_batches"]
    return recipes.merge(force[cols], on="recipe_id", validate="one_to_one")[
        ["recipe_name_ru", "sucrose_g_per_100g_flour", "sucrose_removed_g_per_100g_flour", "sucrose_reduction_pct",
         "fibre_ingredient_g_per_100g_flour", "total_recipe_g_per_100g_flour", "mean_of_batch_means",
         "delta_vs_control", "relative_delta_pct", "n_independent_batches"]].rename(columns={
            "recipe_name_ru": "Рецептура", "sucrose_g_per_100g_flour": "Сахароза, г на 100 г муки",
            "sucrose_removed_g_per_100g_flour": "Удалено сахарозы, г на 100 г муки",
            "sucrose_reduction_pct": "Снижение добавленной сахарозы, %",
            "fibre_ingredient_g_per_100g_flour": "Новый ингредиент, г на 100 г муки",
            "total_recipe_g_per_100g_flour": "Общая масса рецептуры, г на 100 г муки",
            "mean_of_batch_means": "Средняя сила разрушения, Н", "delta_vs_control": "Разность силы, Н",
            "relative_delta_pct": "Изменение силы, %", "n_independent_batches": "Независимых партий"})


def sugar_section(tables, selected):
    st.header("Меньше сахарозы — другое соотношение ингредиентов")
    st.write("Производство хочет сократить добавленную сахарозу и понять последствия для продукта. "
             "Сравниваем четыре исследованных варианта; универсальный предел снижения по ним установить нельзя.")
    recipes = selected_rows(tables["recipes"], selected)
    sugar_tab, reduction_tab, ingredient_tab = st.tabs(["Сахароза", "Снижение, %", "Заменяющий ингредиент"])
    with sugar_tab:
        show_plot(bar_chart(recipes, "sucrose_g_per_100g_flour", "Добавленная сахароза", "г на 100 г муки"), "sucrose_amount")
    with reduction_tab:
        show_plot(bar_chart(recipes, "sucrose_reduction_pct", "Снижение относительно стандарта", "%"), "sucrose_reduction")
    with ingredient_tab:
        show_plot(bar_chart(recipes, "fibre_ingredient_g_per_100g_flour", "Масса добавленного коммерческого ингредиента",
                            "г на 100 г муки"), "ingredient_dose")
    st.caption("29,45 г — неизменный знаменатель расчёта. Проценты относятся к добавленной сахарозе, а не ко всем сахарам продукта.")
    st.info("«Г на 100 г муки» — навески для рецепта с 100 г муки. Это не состав 100 г готового печенья. "
            "Масса добавленного ингредиента не равна массе чистого пищевого волокна.")
    st.subheader("Состав выбранных рецептур")
    show_table(recipes, {"recipe_name_ru": "Рецептура", "sucrose_g_per_100g_flour": "Сахароза, г",
                        "sucrose_removed_g_per_100g_flour": "Удалено, г", "sucrose_reduction_pct": "Снижение, %",
                        "fibre_ingredient_g_per_100g_flour": "Новый ингредиент, г", "total_recipe_g_per_100g_flour": "Всего, г"})
    st.caption("Все граммы в таблице — на 100 г муки. У более растворимого инулина общая масса 180,16 г; у остальных 175,95 г. "
               "Исходная рецептура сохранена, различие ограничивает сравнимость.")
    with st.expander("Все ингредиенты рецептур"):
        columns = {"flour": "Мука", "butter": "Сливочное масло", "sucrose": "Сахароза",
                   "nutriose_ingredient": "Кукурузный декстрин № 1", "promitor_ingredient": "Кукурузный декстрин № 2",
                   "orafti_ingredient": "Более растворимый инулин", "fibruline_ingredient": "Длинноцепочечный инулин",
                   "skimmed_milk_powder": "Сухое обезжиренное молоко", "water": "Вода", "salt": "Соль",
                   "sodium_bicarbonate": "Бикарбонат натрия", "ammonium_bicarbonate": "Бикарбонат аммония"}
        composition = recipes.set_index("recipe_name_ru")[[k + "_g_per_100g_flour" for k in columns]].T
        composition.index = list(columns.values())
        st.dataframe(composition.rename_axis("Ингредиент, г на 100 г муки"), width="stretch")
    with st.expander("Что мы меняем в рецептуре и зачем?"):
        st.markdown("""
**Сахароза** — обычный столовый сахар. Он влияет на сладость, поведение теста, цвет и структуру после выпечки.

**Пищевые волокна** не расщепляются пищеварительными ферментами человека в тонком кишечнике. Растворимые волокна взаимодействуют с водой и могут заменить часть сахарозы по массе, но воспроизводить её сладость и технологические свойства не обязаны.

**Резистентные декстрины кукурузного происхождения** получают переработкой крахмала. В исследовании два разных коммерческих продукта; объединять их в один ингредиент нельзя.

**Инулин** — волокно из цепочек фруктозных звеньев. Оба изученных инулинсодержащих ингредиента происходят из цикория. Длина цепочек и растворимость могут различаться.

Если из домашнего рецепта убрать часть сахара и добавить другой ингредиент, печенье может стать менее сладким, плотнее, изменить цвет или сильнее крошиться. Такая замена сама по себе не доказывает меньшую калорийность или большую пользу.
""")
        st.dataframe(tables["recipes"][["recipe_name_ru", "ingredient_original"]]
                     .fillna("Без заменителя").rename(columns={"recipe_name_ru": "Рецептура", "ingredient_original": "Название в статье"}),
                     hide_index=True, width="stretch")
        st.caption("Orafti HSI описан в статье как гидролизованный высокорастворимый инулин. Fibruline Instant — растворимый инулин "
                   "с цепочками разной длины: название «Длинноцепочечный» не означает исключительно длинные цепи.")


def quality_section(tables, selected):
    st.header("Как изменилась измеренная структура печенья")
    quality = quality_data(tables)
    rows = selected_rows(quality[quality.metric_id.eq("breaking_strength_n")], selected)
    batches = tables["physical_batch_means"].query("metric_id == 'breaking_strength_n'")
    st.warning("Только три независимые производственные партии на рецептуру. Печенья внутри партии не увеличивают число производственных экспериментов.")
    show_plot(force_chart(rows, batches), "force")
    st.caption("Кружки — три партийных средних, ромбы — среднее рецептуры. Линии — сохранённые отдельные 95% t-интервалы средних "
               "(df=2), условные при независимых приблизительно нормальных партиях. Это не допуски и не интервалы разности с контролем.")
    show_table(rows, {"recipe_name_ru": "Рецептура", "value": "Среднее, Н", "control_mean": "Стандарт, Н",
                      "delta_vs_control": "Разность, Н", "relative_delta_pct": "Изменение, %", "sd_between_batches": "SD партий, Н"})
    st.write("Сила разрушения — усилие прибора при изломе; она зависит от структуры и геометрии. Близость одного среднего к стандарту "
             "не означает сохранение всего качества или доказанную эквивалентность.")
    with st.expander("Партийные значения и статистические подробности"):
        show_table(selected_rows(batches, selected), {"recipe_name_ru": "Рецептура", "source_replicate": "Условная партия",
                                                     "batch_mean": "Средняя сила, Н", "n_values_observed": "Измерений внутри партии"})
        model = st.selectbox("Статистическая модель", ["welch_holm", "dunnett_equal_variance"],
                             format_func=lambda x: "Основной анализ: Welch + Holm" if x == "welch_holm" else "Чувствительность: Dunnett, общая дисперсия",
                             key="stat_model")
        statistics = tables["statistical_results"].query("method_id == @model")
        statistics = statistics[statistics.recipe_id.isin(selected)]
        if statistics.empty:
            st.info("Для сравнения со стандартом выберите хотя бы одну экспериментальную рецептуру.")
        else:
            show_table(statistics, {"recipe_name_ru": "Вариант", "difference_vs_control": "Разность, Н", "p_adjusted": "p с поправкой",
                                    "ci_low": "Нижняя граница, Н", "ci_high": "Верхняя граница, Н"}, digits=3)
            st.caption("Welch: отдельные 98,75% интервалы Welch–Bonferroni для четырёх разностей, номинальный семейный уровень 95%; "
                       "это не интервалы Holm. Dunnett: совместные 95% интервалы четырёх разностей при общей дисперсии.")
            st.write(statistics.assumptions_ru.iloc[0])
        st.warning("Исследовательские сравнения при n=3 условны. p > 0,05 не доказывает одинаковое качество; границы эквивалентности не заданы.")
    st.divider()
    st.subheader("Физические характеристики")
    group = st.selectbox("Группа показателей", list(GROUPS), key="physical_group")
    dictionary = tables["metric_dictionary"].set_index("metric_id")
    metric = st.selectbox("Физический показатель", GROUPS[group], key="physical_metric",
                          format_func=lambda x: f"{dictionary.loc[x, 'metric_name_ru']} · {dictionary.loc[x, 'unit']}")
    difference = st.radio("Форма физического сравнения", ["Значения", "Разность со стандартом"], horizontal=True, key="physical_mode") == "Разность со стандартом"
    physical = selected_rows(quality[quality.metric_id.eq(metric)], selected)
    unit = physical.delta_unit.iloc[0] if difference else physical.unit.iloc[0]
    title = dictionary.loc[metric, "metric_name_ru"] + (": разность со стандартом" if difference else "")
    show_plot(bar_chart(physical, "delta_vs_control" if difference else "value", title, unit), "physical")
    st.write(dictionary.loc[metric, "definition_ru"])
    show_table(physical, {"recipe_name_ru": "Рецептура", "value": "Значение", "unit": "Единица", "control_mean": "Стандарт",
                          "delta_vs_control": "Разность", "delta_unit": "Ед. разности", "n_independent_batches": "Независимых партий"},
               digits=4 if metric == "water_activity" else 2)
    if group == "Влажность и активность воды":
        st.caption("Влажность — количество воды; aw — её доступность для процессов. Эти показатели не устанавливают срок хранения. "
                   "Разности влажности и потерь массы — процентные пункты.")
    if group == "Цвет":
        st.caption("Численное отличие цвета не означает привлекательность для покупателя. ΔE*76 — расстояние средних цветовых центров, "
                   "без независимых партийных ΔE, SD и интервалов; каждый центр рассчитан по трём партиям.")
        if metric == "colour_delta_e76":
            st.caption("У длинноцепочечного инулина ΔE из Excel ≈0,90, а в статье 1,1. Оба значения и флаг расхождения сохранены в данных.")
    with st.expander("Определения и ограничения физических измерений"):
        st.write("Твёрдость укуса — ощущаемое усилие, хрусткость связана с характером разрушения и звуком, крошливость — с распадом на мелкие части. "
                 "Приборные пики и сила не заменяют эти сенсорные оценки.")
        st.write("Единицы силы (Н) и размеров (мм) сверены с методами статьи; противоречивые подписи Excel не исправлены в исходниках. "
                 "Есть одно отсутствующее измерение изделия. Метки партий условные, общие дни рецептур не подтверждены.")
    st.divider()
    st.subheader("Сенсорные характеристики")
    st.write("Опубликованные средние интенсивности по шкале 0–100. Индивидуальных оценок дегустаторов нет: новые тесты и интервалы не строятся.")
    sensory_labels = tables["sensory_means"].drop_duplicates("metric_id").set_index("metric_id").metric_label_ru.to_dict()
    chosen = st.multiselect("Сенсорные признаки", list(sensory_labels), default=KEY_SENSORY,
                            format_func=sensory_labels.get, placeholder="Выберите признаки", key="sensory_metrics")
    sensory_difference = st.radio("Форма сенсорного сравнения", ["Разности со стандартом", "Опубликованные средние"],
                                  horizontal=True, key="sensory_mode") == "Разности со стандартом"
    if not chosen:
        st.info("Выберите хотя бы один сенсорный признак для графика и таблицы.")
    else:
        sensory = selected_rows(tables["sensory_means"][tables["sensory_means"].metric_id.isin(chosen)], selected)
        labels = [sensory_labels[x] for x in chosen]
        show_plot(sensory_chart(sensory, labels, sensory_difference), "sensory")
        show_table(sensory, {"metric_label_ru": "Признак", "recipe_name_ru": "Рецептура", "published_mean": "Среднее, 0–100",
                            "control_published_mean": "Стандарт, 0–100", "delta_vs_control_points": "Разность, пункты"})
        if sensory.definition_status.ne("supported_by_S1").any():
            st.caption("Для части выбранных признаков определения и границы шкалы в S1 отсутствуют; исходные оценки оставлены без изменений.")
    st.caption("Интенсивность сладости, горечи или хрусткости не равна потребительскому предпочтению. Все 40 признаков доступны в селекторе.")


def candidate_section(tables, selected):
    st.header("Три направления следующих испытаний")
    candidates = tables["candidate_comparison"].sort_values("recipe_order")
    sugar = candidates.loc[candidates.sucrose_reduction_pct.idxmax()]
    force = candidates.loc[candidates.absolute_breaking_relative_delta_pct.idxmin()]
    bitter = candidates.loc[candidates.bitterness_delta_points.abs().idxmin()]
    burnt = candidates.loc[candidates.burnt_taste_delta_points.abs().idxmin()]
    directions = [("Сокращение сахарозы", sugar.recipe_name_ru, f"{number(sugar.sucrose_reduction_pct)}% — максимум исследованного набора."),
                  ("Среднее усилие излома", force.recipe_name_ru, f"{number(force.breaking_relative_delta_pct, signed=True)}% от стандарта — наименьшее абсолютное отклонение."),
                  ("Отдельные вкусовые сдвиги", bitter.recipe_name_ru if bitter.recipe_id == burnt.recipe_id else bitter.recipe_name_ru + " / " + burnt.recipe_name_ru,
                   f"Горечь: {number(bitter.bitterness_delta_points, signed=True)} п.; подгорелый вкус: {number(burnt.burnt_taste_delta_points, signed=True)} п. — меньшие абсолютные сдвиги.")]
    for column, (label, title, detail) in zip(st.columns(3), directions):
        with column:
            with st.container(border=True):
                st.caption(label.upper())
                st.markdown(f"**{title}**")
                st.write(detail)
    st.caption("Направления определены по всем четырём вариантам и не меняются при фильтрации. Это разные приоритеты, без общего рейтинга качества.")
    rows = candidates[candidates.recipe_id.isin(selected)]
    if rows.empty:
        st.info("Сейчас выбран только стандарт. Добавьте экспериментальные рецептуры в боковой панели для таблицы кандидатов.")
        return
    st.subheader("Сравнение выбранных кандидатов")
    show_table(rows, {"recipe_name_ru": "Рецептура", "sucrose_reduction_pct": "Снижение, %",
                      "mean_breaking_strength_n": "Средняя сила, Н", "breaking_relative_delta_pct": "Изменение силы, %",
                      "bitterness_delta_points": "Горечь, п.", "burnt_taste_delta_points": "Подгорелый вкус, п.", "colour_delta_e76": "ΔE*76"})
    for row in rows.itertuples():
        with st.expander(row.recipe_name_ru + " — основания и ограничения", expanded=len(rows) == 1):
            reasons = []
            if row.recipe_id == sugar.recipe_id:
                reasons.append("Наибольшее исследованное снижение добавленной сахарозы.")
            if row.recipe_id == force.recipe_id:
                reasons.append("Наиболее близкая к стандарту средняя сила разрушения среди вариантов.")
            if row.recipe_id in [bitter.recipe_id, burnt.recipe_id]:
                reasons.append("Меньшие изменения отдельных негативных вкусовых признаков по опубликованным средним.")
            st.markdown("**Почему рассматривать:** " + (" ".join(reasons) or "Сначала уточнить состав и повторить испытания."))
            st.markdown("**Физические изменения:** " + row.physical_changes_ru)
            st.markdown("**Сенсорные наблюдения:** " + row.sensory_changes_ru)
            st.markdown("**Дальнейшие испытания:** " + row.recommendation_ru)
            st.markdown("**Ограничения и уверенность:** " + row.limitations_ru + " " + row.confidence_ru)
    st.warning("Перспективность для испытаний не означает готовность к производству. Нужны дополнительные независимые партии, "
               "согласованные допуски, проверка сенсорного профиля и потребительские испытания.")


def main():
    st.set_page_config(page_title="Cookies · снижение сахарозы", page_icon="🍪", layout="wide")
    st.markdown("""<style>
    .stMainBlockContainer {max-width: 1320px; padding-top: 4rem; padding-bottom: 2rem;}
    h1 {letter-spacing: -0.035em;} h2 {letter-spacing: -0.02em;}
    [data-testid="stMetricValue"] {font-size: 1.85rem;}
    [data-testid="stMetricLabel"] {font-size: 0.9rem;}
    [data-testid="stSidebar"] h1 {font-size: 1.7rem;}
    [data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p {color: inherit;}
    [data-testid="stCaptionContainer"] {opacity: 0.85;}
    [data-testid="stSidebar"] [data-testid="stMultiSelect"] .react-aria-ComboBox > [role="group"] {max-height: none;}
    [data-testid="stMultiSelectTagsContainer"] {max-height: none;}
    [data-testid="stMultiSelectTagsContainer"] [data-tag] {height: auto; max-width: 100%;}
    [data-testid="stMultiSelectTagsContainer"] [data-tag] span[title] {
        white-space: normal; text-overflow: clip; overflow: visible; max-width: none; min-width: 0;
    }
    </style>""", unsafe_allow_html=True)
    try:
        tables = load_data()
    except (OSError, ValueError, KeyError) as exc:
        st.error(f"Не удалось загрузить проверенные данные: {exc}")
        st.stop()
    recipes = tables["recipes"].sort_values("recipe_order")
    names = recipes.set_index("recipe_id").recipe_name_ru.to_dict()
    with st.sidebar:
        st.title("Cookies")
        st.caption("Исследование рецептур · Университет Рединга")
        selected = st.multiselect("Экспериментальные рецептуры", recipes.recipe_id.iloc[1:].tolist(),
                                  default=recipes.recipe_id.iloc[1:].tolist(), format_func=names.get, placeholder="Выберите варианты", key="recipes")
        st.caption("Стандартная рецептура всегда остаётся основой сравнения. Без выбранных вариантов показан только стандарт.")
        st.divider()
        st.download_button("Скачать сравнение · CSV", csv_bytes(comparison_table(tables, selected)),
                           file_name="cookies_comparison.csv", mime="text/csv", key="download_comparison", width="stretch")
        st.caption("Выбранные рецептуры + стандарт; числа и единицы, UTF-8 с BOM.")
        st.divider()
        st.markdown("[Исходный набор](https://doi.org/10.17864/1947.000384) · [Научная статья](https://doi.org/10.1016/j.lwt.2022.113837)")
        st.caption("Nguyen et al., 2022 · CC BY 4.0. Анализ и аудит сохранены в репозитории.")
    st.caption("АНАЛИТИКА РЕЦЕПТУР / COOKIES")
    st.title("Снижение сахарозы и качество печенья")
    st.write("Насколько можно снизить добавленную сахарозу с помощью пищевых волокон и какие рецептуры стоит проверить на производстве?")
    kpis = [("Сахароза стандарта", number(recipes.iloc[0].sucrose_g_per_100g_flour) + " г", "На 100 г муки"),
            ("Максимальное снижение", number(recipes.sucrose_reduction_pct.max()) + "%", "Среди четырёх вариантов"),
            ("Варианты рецептур", str(len(recipes) - 1), "Плюс стандарт"),
            ("Независимые партии", str(int(tables["physical_recipe_summary"].n_independent_batches.min())), "На рецептуру")]
    for column, (label, value, help_text) in zip(st.columns(4), kpis):
        with column:
            with st.container(border=True):
                st.metric(label, value)
                st.caption(help_text)
    st.caption("KPI описывают весь эксперимент и сохраняются при выборе отдельных вариантов.")
    section = st.radio("Раздел исследования", SECTIONS, horizontal=True, label_visibility="collapsed", key="section")
    if section == SECTIONS[0]:
        sugar_section(tables, selected)
    elif section == SECTIONS[1]:
        quality_section(tables, selected)
    else:
        candidate_section(tables, selected)
    st.divider()
    with st.expander("Границы исследования и происхождение данных"):
        st.write("Три партии на рецептуру; отдельные печенья вложены в партии. Эквивалентность не подтверждена. "
                 "Индивидуальная сенсорика, общее содержание сахаров готового продукта, потребительские предпочтения, "
                 "хранение и экономика производства не измерены. Тип и дозировка заменителя меняются вместе: "
                 "универсальную зависимость качества от процента замены по этим четырём вариантам не строим.")
        st.write("Приложение читает готовые CSV из Jupyter Notebook. Новые статистические тесты и интервалы в интерфейсе не рассчитываются. "
                 "Оригинальные Excel, PDF и DOCX, аудит, словарь показателей и источники сохранены в репозитории.")


if __name__ == "__main__":
    main()
