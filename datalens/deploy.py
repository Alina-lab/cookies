"""План и сборка Cookies через официальный DataLens SDK. По умолчанию — без сети."""

import argparse
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path

from datalens_sdk import DashboardChartTab, DashboardTab, DataLensClientYC, EntryLocation, StaticYCIAMAuthProvider
from datalens_sdk.converter.dashboard import DashboardConverter
from datalens_sdk.converter.editor_chart import EditorChartConverter

from prepare import BUILD, HERE, prepare, write_json

STATE = HERE / ".state.json"
TAB_LABELS = {
    "force_graph": "Сила разрушения", "force_table": "Значения", "batches_table": "Партии",
    "statistics_table": "Условная статистика", "physical_graph": "Средние",
    "physical_delta": "Разности", "physical_table": "Таблица", "sensory_graph": "Средние",
    "sensory_delta": "Разности", "sensory_table": "Таблица",
}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def chart_snapshot(key, snapshot):
    """В каждый чарт попадают только необходимые таблицы и поля."""
    result = {"recipes": snapshot["recipes"], "config": {
        name: snapshot["config"][name] for name in ["recipe_colours", "default_physical_metric", "default_sensory_metric", "key_sensory_metrics", "advantages_ru"]
    }}
    if key.startswith("force_"):
        result["physical"] = [r for r in snapshot["physical"] if r["metric_id"] == "breaking_strength_n"]
    elif key.startswith("physical_"):
        result["physical"] = snapshot["physical"]
    if key in ["force_graph", "batches_table"]:
        fields = ["recipe_id", "recipe_name_ru", "recipe_order", "batch_id", "metric_id", "batch_mean", "n_values_observed", "n_values_missing"]
        result["batches"] = [{f: row[f] for f in fields} for row in snapshot["batches"] if row["metric_id"] == "breaking_strength_n"]
    if key.startswith("sensory_"):
        fields = ["recipe_id", "recipe_name_ru", "recipe_order", "metric_id", "metric_label_ru", "published_mean",
                  "control_published_mean", "delta_vs_control_points", "data_status", "definition_status"]
        result["sensory"] = [{f: row[f] for f in fields} for row in snapshot["sensory"]]
    if key == "statistics_table":
        result["statistics"] = snapshot["statistics"]
    if key.startswith("candidate_"):
        result["candidates"] = snapshot["candidates"]
    return result


def chart_tabs(descriptor, snapshot):
    defaults = {"recipe_id": ["all"], "physical_metric_id": [snapshot["config"]["default_physical_metric"]],
                "sensory_metric_id": [snapshot["config"]["default_sensory_metric"]]}
    prefix = "const DATA = " + json.dumps(chart_snapshot(descriptor["key"], snapshot), ensure_ascii=False, allow_nan=False) + ";\n"
    prefix += "const VIEW = " + json.dumps(descriptor["key"]) + ";\n"
    tabs = {
        "meta": "module.exports = {};", "sources": "module.exports = {};",
        "params": "module.exports = " + json.dumps(defaults, ensure_ascii=False) + ";",
        "controls": "module.exports = {controls: []};",
        "prepare": prefix + (HERE / "editor.js").read_text(encoding="utf-8"),
    }
    if descriptor["kind"] == "table":
        tabs["config"] = "module.exports = {size: 'm', paginator: {enabled: false}};"
    elif descriptor["kind"] == "gravity_charts":
        tabs["config"] = "module.exports = {};"
    return tabs


def chart_builder(client, descriptor, snapshot, location):
    factory = getattr(client.create.editor_chart, descriptor["kind"])
    builder = factory(name=descriptor["title"], location=location).description(
        "Cookies: управляется datalens/deploy.py; данные опубликованного эксперимента, без новых наблюдений.")
    for tab, content in chart_tabs(descriptor, snapshot).items():
        builder = getattr(builder, tab)(content)
    return builder


def selector_options(parameter, snapshot):
    if parameter == "recipe_id":
        return "Рецептура", "all", [("all", "Все рецептуры")] + [(r["recipe_id"], r["recipe_name_ru"]) for r in snapshot["recipes"]]
    if parameter == "physical_metric_id":
        labels = {r["metric_id"]: r["metric_selector_label"] for r in snapshot["physical"]}
        options = [(r["metric_id"], labels[r["metric_id"]]) for r in snapshot["metrics"] if r["metric_id"] in labels]
        return "Физический показатель", snapshot["config"]["default_physical_metric"], options
    options = [("key_attributes", "Девять ключевых сенсорных признаков")]
    options += [(r["metric_id"], r["metric_label_ru"]) for r in snapshot["metrics"] if r["domain"] == "sensory"]
    return "Сенсорный показатель", snapshot["config"]["default_sensory_metric"], options


def dashboard_builder(client, snapshot, charts, location):
    builder = client.create.dashboard(name=snapshot["config"]["title"], location=location)
    for spec in snapshot["config"]["tabs"]:
        tab = DashboardTab(spec["title"], tab_id=spec["key"])
        for selector in spec["selectors"]:
            title, default, options = selector_options(selector["parameter"], snapshot)
            tab.add_selector(param_name=selector["parameter"], title=title, default_value=default,
                             options=options, required=True, multiselect=False, title_placement="top",
                             item_id=spec["key"] + "_" + selector["parameter"], at=tuple(selector["at"]))
        for group in spec["groups"]:
            descriptions = {r["key"]: r for r in snapshot["config"]["charts"]}
            entries = [DashboardChartTab(chart=charts[key], title=TAB_LABELS.get(key, descriptions[key]["title"]),
                                         description=descriptions[key]["title"], default=(i == 0))
                       for i, key in enumerate(group["charts"])]
            tab.add_chart_group(entries, item_id=group["key"], at=tuple(group["at"]), background="#FFFFFF", border_radius=8)
        tab.add_text(spec["note"], item_id=spec["key"] + "_note", at=tuple(spec["note_at"]), background="#F3F4F6", auto_height=False)
        tab.add_text(spec["footer"], item_id=spec["key"] + "_footer", at=tuple(spec["footer_at"]), background="#F3F4F6", auto_height=False)
        builder.add_tab(tab)
    return builder.description("Опубликованный эксперимент: пять рецептур, три производственные партии; не доказательство эквивалентности.").settings(hide_tabs=False, silent_loading=True)


def make_plan(snapshot):
    """SDK проверяет схемы запросов локально. Подстановочные ID не реальные объекты."""
    location = EntryLocation.workbook("__WORKBOOK_ID__")
    with DataLensClientYC(auth=None) as client:
        requests = []
        ids = {}
        for descriptor in snapshot["config"]["charts"]:
            builder = chart_builder(client, descriptor, snapshot, location)
            payload = EditorChartConverter.from_domain_create(builder.to_spec()).to_payload()
            requests.append({"method": "createEditorChart", "key": descriptor["key"], "body": payload})
            ids[descriptor["key"]] = "__CHART_ID_" + descriptor["key"] + "__"
        dashboard = dashboard_builder(client, snapshot, ids, location)
        payload = DashboardConverter.from_domain_create(dashboard.to_spec()).to_payload()
        requests.append({"method": "createDashboard", "body": payload})
    plan = {"status": "local_plan_only", "api_base": "https://api.datalens.tech", "api_version": "3",
            "fingerprint": snapshot["fingerprint"], "requests": requests}
    write_json(BUILD / "plan.json", plan)
    return plan


def save_state(path, state):
    temporary = path.with_suffix(".tmp")
    write_json(temporary, state)
    temporary.replace(path)


def apply(client, snapshot, org_id, workbook_id="", state_path=STATE):
    state = json.loads(state_path.read_text()) if state_path.exists() else {"org_id": org_id, "charts": {}}
    assert state["org_id"] == org_id, "Состояние относится к другой организации"
    assert not workbook_id or not state.get("workbook_id") or workbook_id == state["workbook_id"], "Воркбук не совпадает с локальным состоянием"
    state.update({"status": "applying", "api_readback_verified": False, "browser_verified": False,
                  "requested_fingerprint": snapshot["fingerprint"]})
    save_state(state_path, state)
    destination = state.get("workbook_id") or workbook_id
    workbook = client.get.workbook(by_id=destination) if destination else client.create.workbook(name=snapshot["config"]["workbook_name"]).build()
    state["workbook_id"] = workbook.id
    save_state(state_path, state)
    location = EntryLocation.workbook(workbook.id)
    charts = {}
    for descriptor in snapshot["config"]["charts"]:
        key = descriptor["key"]
        tabs = chart_tabs(descriptor, snapshot)
        expected_hash = digest(tabs)
        saved = state["charts"].get(key)
        if saved:
            chart = client.get.editor_chart(by_id=saved["id"], workbook_id=workbook.id)
            current_tabs = {name: chart.data.get(name) for name in tabs}
            current_hash = digest(current_tabs)
            assert current_hash in [saved["hash"], expected_hash], "Управляемый чарт изменён вручную; автоматическая перезапись остановлена"
            if current_hash != expected_hash:
                update = chart.update.mode("publish")
                for tab, content in tabs.items():
                    update = getattr(update, tab)(content)
                chart = update.execute()
        else:
            chart = chart_builder(client, descriptor, snapshot, location).build()
        assert chart.id, "API не вернул идентификатор чарта"
        state["charts"][key] = {"id": chart.id, "hash": expected_hash}
        charts[key] = chart
        save_state(state_path, state)
    builder = dashboard_builder(client, snapshot, charts, location)
    expected = DashboardConverter.from_domain_create(builder.to_spec()).to_payload()["entry"]
    if state.get("dashboard_id"):
        dashboard = client.get.dashboard(by_id=state["dashboard_id"], workbook_id=workbook.id)
        expected_hash = digest(expected["data"])
        current_hash = digest(dict(dashboard.data))
        assert current_hash in [state["dashboard_hash"], expected_hash], "Дашборд изменён вручную; автоматическая перезапись остановлена"
        if current_hash != expected_hash:
            replacement = {**dict(dashboard.raw), **expected, "entryId": dashboard.id}
            dashboard = client.raw.replace.dashboard(target=dashboard, response_snapshot={"entry": replacement}).mode("publish").execute()
    else:
        dashboard = builder.build()
    assert dashboard.id, "API не вернул идентификатор дашборда"
    state["dashboard_id"] = dashboard.id
    state["dashboard_hash"] = digest(expected["data"])
    state["fingerprint"] = snapshot["fingerprint"]
    save_state(state_path, state)
    # Read-back подтверждает содержание объектов, но не отрисовку в браузере.
    for key, chart in charts.items():
        fetched = client.get.editor_chart(by_id=chart.id, workbook_id=workbook.id)
        tabs = chart_tabs(next(c for c in snapshot["config"]["charts"] if c["key"] == key), snapshot)
        assert all(fetched.data.get(tab) == value for tab, value in tabs.items()), f"Не совпало содержимое чарта: {key}"
    fetched = client.get.dashboard(by_id=dashboard.id, workbook_id=workbook.id)
    assert digest(dict(fetched.data)) == state["dashboard_hash"], "Не совпало содержимое дашборда"
    state["api_readback_verified"] = True
    state["status"] = "created_and_api_readback_verified"
    state["browser_verified"] = False
    state["public_access_enabled_by_script"] = False
    state["dashboard_url"] = "https://datalens.yandex/" + dashboard.id
    save_state(state_path, state)
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Создать/обновить объекты через официальный API; без флага только локальный план")
    parser.add_argument("--export-tabs", action="store_true", help="Также выгрузить вкладки Editor в отдельные JS-файлы для ручной сборки")
    args = parser.parse_args()
    assert version("datalens-sdk") == "3.2.0", "Установите зафиксированный SDK из datalens/requirements.txt"
    snapshot = prepare()
    plan = make_plan(snapshot)
    if args.export_tabs:
        for request in plan["requests"]:
            if "key" in request:
                for tab, content in request["body"]["entry"]["data"].items():
                    path = BUILD / "editor" / request["key"] / (tab + ".js")
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(content + "\n", encoding="utf-8")
        print("Вкладки Editor выгружены в datalens/build/editor/.")
    if not args.apply:
        print(f"Локальный план: {len(plan['requests']) - 1} чартов, 3 вкладки. Объекты DataLens не созданы.")
        return
    org_id = os.environ.get("DATALENS_ORG_ID")
    if not org_id:
        raise SystemExit("Нужен DATALENS_ORG_ID в локальном окружении. Настройка: datalens/README.md.")
    token = os.environ.get("DATALENS_IAM_TOKEN")
    auth = StaticYCIAMAuthProvider(org_id=org_id, token=token) if token else None
    options = {"auth": auth} if auth else {}  # При отсутствии токена SDK использует yc CLI.
    try:
        with DataLensClientYC(**options) as client:
            state = apply(client, snapshot, org_id, os.environ.get("DATALENS_WORKBOOK_ID", ""))
    except Exception as exc:
        # Тело ошибки API и HTTP-заголовки не печатаем: они могут содержать данные авторизации.
        raise SystemExit(f"Сборка не завершена ({type(exc).__name__}). Проверьте доступ, конфигурацию и локальное состояние; секреты в лог не записаны.") from None
    print("Объекты созданы/обновлены, содержание проверено через API:", state["dashboard_url"])
    print("Отрисовку, селекторы и доступ нужно проверить в браузере по инструкции. Публичный доступ скрипт не включает.")


if __name__ == "__main__":
    main()
