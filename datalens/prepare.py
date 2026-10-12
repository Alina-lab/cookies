"""Проверка готовых таблиц и материализация данных для DataLens, без сети."""

from pathlib import Path
import hashlib
import json

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "datalens"
BUILD = HERE / "build"
SOURCE_COUNTS = {
    "recipes": 5, "physical_measurements": 905, "physical_batch_means": 225,
    "physical_recipe_summary": 75, "sensory_means": 200, "metric_dictionary": 56,
    "statistical_results": 8, "colour_summary": 5, "candidate_comparison": 4,
    "bi_field_dictionary": 234,
}
KEYS = {
    "recipes": ["recipe_id"], "physical_measurements": ["measurement_id"],
    "physical_batch_means": ["recipe_id", "batch_id", "metric_id"],
    "physical_recipe_summary": ["recipe_id", "metric_id"],
    "sensory_means": ["recipe_id", "metric_id"], "metric_dictionary": ["metric_id"],
    "statistical_results": ["statistical_result_id"], "colour_summary": ["recipe_id"],
    "candidate_comparison": ["recipe_id"],
    "bi_field_dictionary": ["source_name", "field_name"],
}


def records(frame):
    """Пропуски становятся JSON null; наблюдения и числовые нули сохраняются."""
    return json.loads(frame.to_json(orient="records", force_ascii=False, double_precision=15))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def load_and_check():
    tables = {}
    for name, count in SOURCE_COUNTS.items():
        table = pd.read_csv(ROOT / "data" / f"{name}.csv")
        assert len(table) == count, f"Изменилось число строк: {name}"
        assert not table.duplicated(KEYS[name]).any(), f"Повтор ключа: {name}"
        assert table[KEYS[name]].notna().all().all(), f"Пустой ключ: {name}"
        tables[name] = table
    recipes = tables["recipes"].sort_values("recipe_order")
    assert recipes.recipe_id.tolist() == ["control", "nutriose", "promitor", "orafti", "fibruline"]
    assert np.allclose(recipes.sucrose_reduction_pct.round(2), [0, 31.48, 35.11, 22.11, 34.84])
    assert np.allclose(recipes.sucrose_reduction_pct, (29.45 - recipes.sucrose_g_per_100g_flour) / 29.45 * 100)
    assert np.allclose(recipes.total_recipe_g_per_100g_flour, [175.95, 175.95, 175.95, 180.16, 175.95])
    tables["recipes"] = recipes
    raw, batches, physical = [tables[n] for n in ["physical_measurements", "physical_batch_means", "physical_recipe_summary"]]
    assert raw.batch_id.nunique() == 15
    assert batches.groupby(["recipe_id", "metric_id"]).batch_id.nunique().eq(3).all()
    assert physical.n_independent_batches.eq(3).all()
    assert raw[["experiment_date", "shared_day_id", "actual_specimen_id"]].isna().all().all()
    computed = raw.groupby(["recipe_id", "batch_id", "metric_id"]).value.mean()
    saved = batches.set_index(["recipe_id", "batch_id", "metric_id"]).batch_mean.reindex(computed.index)
    assert np.allclose(computed, saved, rtol=1e-12)
    means = batches.groupby(["recipe_id", "metric_id"]).batch_mean.mean()
    assert np.allclose(means, physical.set_index(["recipe_id", "metric_id"]).mean_of_batch_means.reindex(means.index))
    controls = physical[physical.recipe_id.eq("control")].set_index("metric_id").mean_of_batch_means
    assert np.allclose(physical.control_mean, physical.metric_id.map(controls))
    assert np.allclose(physical.delta_vs_control, physical.mean_of_batch_means - physical.control_mean)
    sensory = tables["sensory_means"]
    assert sensory.metric_id.nunique() == 40
    assert not sensory.new_statistical_tests_allowed.any()
    assert sensory.n_independent_batches.isna().all()
    sensory_controls = sensory[sensory.recipe_id.eq("control")].set_index("metric_id").published_mean
    assert np.allclose(sensory.control_published_mean, sensory.metric_id.map(sensory_controls))
    assert np.allclose(sensory.delta_vs_control_points, sensory.published_mean - sensory.control_published_mean)
    results = tables["statistical_results"]
    assert results.analysis_unit.eq("production_batch_mean").all()
    assert results[["n_variant_batches", "n_control_batches"]].eq(3).all().all()
    assert results.is_primary_model.sum() == 4
    assert results[results.is_primary_model].method_id.eq("welch_holm").all()
    force = physical[physical.metric_id.eq("breaking_strength_n")].set_index("recipe_id")
    assert np.allclose(results.difference_vs_control, results.recipe_id.map(force.delta_vs_control))
    candidates = tables["candidate_comparison"]
    assert np.allclose(candidates.mean_breaking_strength_n, candidates.recipe_id.map(force.mean_of_batch_means))
    assert np.allclose(candidates.breaking_relative_delta_pct, candidates.recipe_id.map(force.relative_delta_pct))
    assert np.allclose(candidates.sucrose_reduction_pct, candidates.recipe_id.map(recipes.set_index("recipe_id").sucrose_reduction_pct))
    assert candidates.set_index("recipe_id").loc["promitor", "sucrose_reduction_pct"] == candidates.sucrose_reduction_pct.max()
    assert candidates.set_index("recipe_id").loc["fibruline", "absolute_breaking_relative_delta_pct"] == candidates.absolute_breaking_relative_delta_pct.min()
    for column in ["bitterness_delta_points", "burnt_taste_delta_points"]:
        assert candidates.set_index("recipe_id").loc["nutriose", column] == candidates[column].min()
    centres = physical[physical.metric_id.isin(["colour_l_star", "colour_a_star", "colour_b_star"])].pivot(index="recipe_id", columns="metric_id", values="mean_of_batch_means")
    distances = np.linalg.norm(centres - centres.loc["control"], axis=1)
    assert np.allclose(tables["colour_summary"].set_index("recipe_id").delta_e76_vs_control.reindex(centres.index), distances)
    for table in [batches, physical, sensory, results]:
        assert len(table.merge(recipes[["recipe_id"]], on="recipe_id", validate="many_to_one")) == len(table)
        assert len(table.merge(tables["metric_dictionary"][["metric_id"]], on="metric_id", validate="many_to_one")) == len(table)
    for _, fields in tables["bi_field_dictionary"].groupby("file_name"):
        assert set(fields.field_name) == set(tables[Path(fields.file_name.iloc[0]).stem].columns)
    return tables


def physical_view(tables):
    """Одна строка рецептура × показатель, с готовым контролем до фильтрации."""
    physical = tables["physical_recipe_summary"]
    result = physical[physical.metric_role.ne("context")].copy()
    definitions = tables["metric_dictionary"][["metric_id", "definition_ru", "section_ru"]]
    result = result.merge(definitions, on="metric_id", validate="many_to_one")
    result["value"] = result.mean_of_batch_means
    result["n_batches_used"] = result.n_independent_batches
    result["data_kind"] = "batch_based_physical_summary"
    colour = tables["colour_summary"]
    derived = colour[["recipe_id", "recipe_name_ru", "recipe_order", "metric_id", "unit", "qc_flags"]].copy()
    derived = derived.merge(definitions, on="metric_id", validate="many_to_one")
    derived["metric_name_ru"] = "Разница цвета ΔE*76"
    derived["value"] = colour.delta_e76_vs_control.to_numpy()
    derived["control_mean"] = 0.0  # Расстояние контрольного центра до самого себя.
    derived["delta_vs_control"] = derived.value
    derived["delta_unit"] = derived.unit
    derived["relative_delta_allowed"] = False
    derived["n_batches_used"] = colour.n_batches_used_per_recipe.to_numpy()
    derived["data_kind"] = "derived_colour_centre_distance"
    derived["uncertainty_available"] = "not_estimated_for_derived_distance"
    derived["source_file"] = colour.source_file.to_numpy()
    derived["source_sheet"] = colour.source_sheet.to_numpy()
    derived["article_delta_e76_reported"] = colour.article_delta_e76_reported.to_numpy()
    derived["recipe_qc_flags"] = derived.recipe_id.map(tables["recipes"].set_index("recipe_id").qc_flags)
    colour_cells = physical[physical.metric_id.isin(["colour_l_star", "colour_a_star", "colour_b_star"])].groupby("recipe_id").source_cells.agg(";".join)
    derived["source_cells"] = derived.recipe_id.map(colour_cells)
    result = pd.concat([result, derived], ignore_index=True)
    result["metric_selector_label"] = result.metric_name_ru + " · " + result.unit
    columns = ["recipe_id", "recipe_name_ru", "recipe_order", "metric_id", "metric_name_ru", "metric_selector_label",
               "section_ru", "unit", "delta_unit", "value", "control_mean", "delta_vs_control", "relative_delta_pct",
               "relative_delta_allowed", "sd_between_batches", "n_independent_batches", "n_batches_used", "data_kind",
               "mean_ci_low", "mean_ci_high", "mean_ci_level", "mean_ci_method", "mean_ci_scope", "mean_ci_assumptions_ru",
               "uncertainty_available", "definition_ru", "qc_flags", "recipe_qc_flags", "source_file", "source_sheet",
               "source_cells", "article_delta_e76_reported"]
    result = result[columns].sort_values(["metric_id", "recipe_order"]).reset_index(drop=True)
    flag_labels = {
        "breaking_header_unit_conflict_resolved_by_methods": "Подпись силы противоречива; Н подтверждены методикой статьи.",
        "local_label_repeated_keep_source_rows": "Локальные метки изделий повторяются; исходные записи сохранены.",
        "one_expected_item_measurement_missing": "В третьей партии отсутствует одно измерение; оно не восстановлено.",
        "dimensions_documentation_cm_vs_header_mm": "В документации конфликт см/мм; использованы подтверждённые мм.",
        "computed_colour_distance_differs_from_article_summary": "ΔE по Excel отличается от сводки статьи (около 0,90 против 1,1).",
        "claimed_30_percent_differs_from_recipe_table": "Фактическое снижение сахарозы отличается от заявленных 30%.",
        "orafti_total_recipe_mass_differs": "Общая масса этой рецептуры 180,16 г против 175,95 г на 100 г муки.",
    }
    def explain_flags(row):
        flags = ";".join(str(row[key]) for key in ["qc_flags", "recipe_qc_flags"] if pd.notna(row[key])).split(";")
        unknown = set(flags) - set(flag_labels) - {""}
        assert not unknown, f"Нужен перевод флагов: {unknown}"
        return " ".join(flag_labels[f] for f in dict.fromkeys(flags) if f)
    result["qc_note_ru"] = result.apply(explain_flags, axis=1)
    assert len(result) == 70 and not result.duplicated(["recipe_id", "metric_id"]).any()
    assert result[result.data_kind.eq("derived_colour_centre_distance")].n_independent_batches.isna().all()
    assert result[result.data_kind.eq("derived_colour_centre_distance")].sd_between_batches.isna().all()
    assert result.mean_ci_low.notna().sum() == 5
    for metric, frame in result.groupby("metric_id"):
        assert frame.unit.nunique() == 1
        control = frame[frame.recipe_id.eq("control")].value.iloc[0]
        assert np.allclose(frame.control_mean, control), metric
        for recipe in tables["recipes"].recipe_id:
            selected = frame[frame.recipe_id.eq(recipe)]
            assert np.allclose(selected.control_mean, control), (metric, recipe)
    return result


def prepare():
    tables = load_and_check()
    physical = physical_view(tables)
    physical.to_csv(ROOT / "data" / "datalens_physical_comparison.csv", index=False, encoding="utf-8-sig")
    config = json.loads((HERE / "dashboard.json").read_text(encoding="utf-8"))
    snapshot = {
        "recipes": records(tables["recipes"]), "physical": records(physical),
        "batches": records(tables["physical_batch_means"]), "sensory": records(tables["sensory_means"]),
        "statistics": records(tables["statistical_results"][tables["statistical_results"].is_primary_model]),
        "candidates": records(tables["candidate_comparison"]), "metrics": records(tables["metric_dictionary"]),
        "config": config,
    }
    fingerprint = hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()
    snapshot["fingerprint"] = fingerprint
    write_json(BUILD / "data.json", snapshot)
    print(f"Проверено 10 исходных CSV. Витрина: {len(physical)} строк, 14 показателей. Снимок {fingerprint[:12]}.")
    return snapshot


if __name__ == "__main__":
    prepare()
