"""План и сборка Cookies через официальный DataLens SDK. По умолчанию — без сети."""

import argparse
import hashlib
from importlib.metadata import version
import json
import logging
import os
import re
import subprocess
import traceback
from pathlib import Path

from datalens_sdk import DashboardChartTab, DashboardTab, DataLensClientYC, EntryLocation, StaticYCIAMAuthProvider
from datalens_sdk.domain.dashboard_types import ThemedColor
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


class DeploymentCheckError(AssertionError):
    """Только локально сформированные сведения; без тел API и секретов."""
    def __init__(self, stage, name, reason, expected, actual):
        self.details = {"stage": stage, "object": name, "reason": reason,
                        "expected": expected, "actual": actual}
        super().__init__(json.dumps(self.details, ensure_ascii=False))


def require(condition, stage, name, reason, expected, actual):
    if not condition:
        raise DeploymentCheckError(stage, name, reason, expected, actual)


def canonical(value):
    # bool не является числом JSON. Не удаляем поля, не меняем код или строки.
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {key: canonical(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [canonical(item) for item in value]
    return value


def hash_status(current, target, saved_hash, saved_canonical=None):
    raw, normalized = digest(current), digest(canonical(current))
    expected = digest(canonical(target))
    if normalized == expected:
        status = "unchanged"
    elif raw == saved_hash or normalized == saved_hash or normalized == saved_canonical:
        status = "update_ready"
    else:
        status = "conflict"
    # Saved state is user input too: never echo arbitrary text as a hash.
    clean = lambda h: h if isinstance(h, str) and re.fullmatch(r"[0-9a-f]{64}", h) else None
    return {"status": status, "expected_status": "unchanged_or_update_ready",
            "saved_hash": clean(saved_hash), "saved_canonical_hash": clean(saved_canonical),
            "actual_hash": raw, "actual_canonical_hash": normalized, "target_canonical_hash": expected,
            "integer_float_normalization": raw != normalized}


def differing_paths(a, b, prefix="$", limit=20):
    """Показываем только пути известных полей, никогда значения или исходный код."""
    if digest(canonical(a)) == digest(canonical(b)):
        return []
    if isinstance(a, dict) and isinstance(b, dict):
        paths = []
        for key in a.keys() | b.keys():
            safe_key = key if re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", str(key)) else "<key>"
            path = prefix + "." + safe_key
            paths += [path] if key not in a or key not in b else differing_paths(a[key], b[key], path, limit)
        return sorted(set(paths))[:limit]
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [path for i, (x, y) in enumerate(zip(a, b))
                for path in differing_paths(x, y, prefix + f"[{i}]", limit)][:limit]
    return [prefix]


def remote(stage, name, operation):
    logger = logging.getLogger("datalens_sdk")
    old_propagate = logger.propagate
    handler = logging.NullHandler()
    logger.addHandler(handler)
    logger.propagate = False  # SDK иначе логирует неочищенное message из ответа API.
    try:
        return operation()
    except DeploymentCheckError:
        raise
    except Exception as exc:
        status = getattr(getattr(exc, "context", None), "status_code", None)
        raise DeploymentCheckError(stage, name, "Ошибка чтения/записи API; тело и заголовки скрыты",
                                   "successful_api_response", {"error_class": type(exc).__name__,
                                   "http_status": status if isinstance(status, int) else None}) from None
    finally:
        logger.propagate = old_propagate
        logger.removeHandler(handler)


def check_binding(state, org_id, workbook_id):
    require(state.get("org_id") == org_id, "state_binding", "Организация", "Состояние относится к другой организации",
            "matching_organization", "matching" if state.get("org_id") == org_id else "different")
    require(not workbook_id or not state.get("workbook_id") or workbook_id == state["workbook_id"],
            "state_binding", "Воркбук", "Воркбук не совпадает с локальным состоянием", "matching_workbook", "different")


def previous_dashboard_report(client, snapshot, ids, location, current, saved_hash):
    """Реконструкция известной версии, доверяем только точному сохранённому хешу.

    Код из Git не исполняется. Читаем только JSON конфигурации до смены дизайна.
    """
    commit = "8a4497bdacd7ff7fa1f57b53cb17d6cb5aaaf4ea"
    try:
        result = subprocess.run(["git", "show", commit + ":datalens/dashboard.json"],
                                cwd=HERE.parent, capture_output=True, check=True, timeout=10)
        config = json.loads(result.stdout.decode("utf-8"))
        previous = {**snapshot, "config": config}
        builder = dashboard_builder(client, previous, ids, location, legacy_style=True)
        baseline = DashboardConverter.from_domain_create(builder.to_spec()).to_payload()["entry"]["data"]
    except Exception:
        # Не выводим stderr, содержимое Git или непроверенный снимок.
        return {"previous_baseline_verified": False, "previous_baseline_status": "reconstruction_unavailable"}
    if digest(baseline) != saved_hash:
        return {"previous_baseline_verified": False, "previous_baseline_status": "saved_hash_not_matched",
                "reconstructed_hash": digest(baseline), "previous_config_commit": commit}
    paths = differing_paths(baseline, current, limit=200)
    # Никакого автопринятия текущего содержимого и записи нового хеша.
    return {"previous_baseline_verified": True, "previous_baseline_status": "anchored_to_saved_hash",
            "previous_config_commit": commit, "different_from_saved_paths": paths,
            "saved_and_actual_semantically_equal": digest(canonical(baseline)) == digest(canonical(current))}


def diagnose(client, snapshot, org_id, workbook_id="", state_path=STATE):
    """Только getWorkbook/getEditorChart/getDashboard. Файл состояния не меняется."""
    require(state_path.exists(), "state_load", "Состояние", "Отсутствует .state.json", "existing_state", "missing")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    check_binding(state, org_id, workbook_id)
    require(bool(state.get("workbook_id")), "state_load", "Воркбук", "Нет сохранённого ID", "existing_id", "missing")
    remote("get_workbook", "Воркбук", lambda: client.get.workbook(by_id=state["workbook_id"]))
    location = EntryLocation.workbook(state["workbook_id"])
    objects, ids = [], {}
    for descriptor in snapshot["config"]["charts"]:
        key, title = descriptor["key"], descriptor["title"]
        saved = state.get("charts", {}).get(key, {})
        if not saved.get("id"):
            objects.append({"key": key, "object": title, "stage": "get_editor_chart", "status": "missing_id",
                            "expected_status": "existing_object"})
            continue
        ids[key] = saved["id"]
        try:
            chart = remote("get_editor_chart", title, lambda: client.get.editor_chart(by_id=saved["id"], workbook_id=state["workbook_id"]))
            target = chart_tabs(descriptor, snapshot)
            current = {name: chart.data.get(name) for name in target}
            row = hash_status(current, target, saved.get("hash"), saved.get("canonical_hash"))
            row.update({"key": key, "object": title, "stage": "compare_editor_chart",
                        "different_from_target_paths": differing_paths(current, target),
                        "reason": "Конфликт с сохранённой и целевой версиями; возможна ручная правка или неизвестная нормализация" if row["status"] == "conflict" else "Сопоставление выполнено"})
        except DeploymentCheckError as exc:
            row = {**exc.details, "key": key, "status": "api_error"}
        objects.append(row)
    if state.get("dashboard_id") and len(ids) == len(snapshot["config"]["charts"]):
        title = snapshot["config"]["title"]
        try:
            dashboard = remote("get_dashboard", title, lambda: client.get.dashboard(by_id=state["dashboard_id"], workbook_id=state["workbook_id"]))
            builder = dashboard_builder(client, snapshot, ids, location)
            target = DashboardConverter.from_domain_create(builder.to_spec()).to_payload()["entry"]["data"]
            current = dict(dashboard.data)
            row = hash_status(current, target, state.get("dashboard_hash"), state.get("dashboard_canonical_hash"))
            row.update({"key": "dashboard", "object": title, "stage": "compare_dashboard",
                        "different_from_target_paths": differing_paths(current, target),
                        "reason": "Конфликт с сохранённой и целевой версиями; возможна ручная правка или неизвестная нормализация" if row["status"] == "conflict" else "Сопоставление выполнено"})
            if row["status"] == "conflict":
                row.update(previous_dashboard_report(client, snapshot, ids, location, current, state.get("dashboard_hash")))
        except DeploymentCheckError as exc:
            row = {**exc.details, "key": "dashboard", "status": "api_error"}
        objects.append(row)
    else:
        objects.append({"key": "dashboard", "object": snapshot["config"]["title"], "stage": "get_dashboard",
                        "status": "missing_id_or_chart_ids", "expected_status": "existing_object"})
    return {"mode": "read_only", "safe_to_update": all(row["status"] in ["unchanged", "update_ready"] for row in objects),
            "state_modified": False, "objects": objects}


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
        "meta": json.dumps({"links": {}}, allow_nan=False), "sources": "module.exports = {};",
        "params": "module.exports = " + json.dumps(defaults, ensure_ascii=False) + ";",
        "controls": "module.exports = {controls: []};",
        "prepare": prefix + (HERE / "editor.js").read_text(encoding="utf-8"),
    }
    if descriptor["kind"] == "table":
        tabs["config"] = "module.exports = {size: 'm', paginator: {enabled: false}};"
    elif descriptor["kind"] == "gravity_charts":
        tabs["config"] = "module.exports = {};"
    return tabs


def validate_chart_tabs(tabs):
    """Meta — JSON-текст, остальные вкладки содержат JavaScript.

    Формат: https://yandex.cloud/ru/docs/datalens/charts/editor/tabs#meta
    SDK проверяет тип str, но не JSON внутри строки.
    """
    try:
        meta = json.loads(tabs["meta"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Meta должна содержать JSON-объект с links, а не JavaScript") from exc
    if not isinstance(meta, dict) or not isinstance(meta.get("links"), dict):
        raise ValueError("Meta должна содержать JSON-объект с объектом links")
    if any(not isinstance(k, str) or not isinstance(v, str) for k, v in meta["links"].items()):
        raise ValueError("Meta.links должна сопоставлять строковые алиасы строковым ID")


def preflight(snapshot):
    for descriptor in snapshot["config"]["charts"]:
        validate_chart_tabs(chart_tabs(descriptor, snapshot))


def chart_builder(client, descriptor, snapshot, location):
    factory = getattr(client.create.editor_chart, descriptor["kind"])
    builder = factory(name=descriptor["title"], location=location).description(
        "Cookies: управляется datalens/deploy.py; данные опубликованного эксперимента, без новых наблюдений.")
    tabs = chart_tabs(descriptor, snapshot)
    validate_chart_tabs(tabs)
    for tab, content in tabs.items():
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


def dashboard_builder(client, snapshot, charts, location, legacy_style=False):
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
            tab.add_chart_group(entries, item_id=group["key"], at=tuple(group["at"]), background="#FFFFFF" if legacy_style else ThemedColor(light="#FFFFFF", dark="#343535"), border_radius=8)
        tab.add_text(spec["note"], item_id=spec["key"] + "_note", at=tuple(spec["note_at"]), background="#F3F4F6" if legacy_style else ThemedColor(light="#F3F4F6", dark="#292A2B"), auto_height=False, border_radius=None if legacy_style else 8)
        tab.add_text(spec["footer"], item_id=spec["key"] + "_footer", at=tuple(spec["footer_at"]), background="#F3F4F6" if legacy_style else ThemedColor(light="#F3F4F6", dark="#292A2B"), auto_height=False, border_radius=None if legacy_style else 8)
        builder.add_tab(tab)
    return builder.description("Опубликованный эксперимент: пять рецептур, три производственные партии; не доказательство эквивалентности.").settings(hide_tabs=False, silent_loading=True)


def make_plan(snapshot):
    """SDK проверяет схемы запросов локально. Подстановочные ID не реальные объекты."""
    preflight(snapshot)
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


def apply(client, snapshot, org_id, workbook_id="", state_path=STATE, update_only=False):
    preflight(snapshot)  # До сети и изменения локального состояния.
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"org_id": org_id, "charts": {}}
    if update_only:
        required = {c["key"] for c in snapshot["config"]["charts"]}
        if (not state.get("workbook_id") or not state.get("dashboard_id")
                or not required.issubset(state.get("charts", {}))
                or any(not state["charts"][key].get("id") for key in required)):
            raise ValueError("Для --update-only нужно сохранённое состояние воркбука, дашборда и всех чартов; восстановите .state.json")
    check_binding(state, org_id, workbook_id)
    if update_only:
        report = diagnose(client, snapshot, org_id, workbook_id, state_path)
        for row in report["objects"]:
            require(row["status"] in ["unchanged", "update_ready"], row["stage"], row["object"],
                    "Управляемый объект изменён вручную или не совпадает с сохранённой версией; обновление остановлено",
                    row.get("expected_status"), row)
    state.update({"status": "applying", "api_readback_verified": False, "browser_verified": False,
                  "requested_fingerprint": snapshot["fingerprint"]})
    save_state(state_path, state)
    destination = state.get("workbook_id") or workbook_id
    workbook = remote("get_or_create_workbook", "Воркбук", lambda: client.get.workbook(by_id=destination) if destination else client.create.workbook(name=snapshot["config"]["workbook_name"]).build())
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
            chart = remote("get_editor_chart", descriptor["title"], lambda: client.get.editor_chart(by_id=saved["id"], workbook_id=workbook.id))
            current_tabs = {name: chart.data.get(name) for name in tabs}
            comparison = hash_status(current_tabs, tabs, saved.get("hash"), saved.get("canonical_hash"))
            require(comparison["status"] != "conflict", "compare_editor_chart", descriptor["title"],
                    "Управляемый чарт изменён вручную; автоматическая перезапись остановлена", "saved_or_target", comparison)
            if comparison["status"] != "unchanged":
                update = chart.update.mode("publish")
                for tab, content in tabs.items():
                    update = getattr(update, tab)(content)
                chart = remote("update_editor_chart", descriptor["title"], update.execute)
        else:
            chart = remote("create_editor_chart", descriptor["title"], lambda: chart_builder(client, descriptor, snapshot, location).build())
        require(bool(chart.id), "editor_id", descriptor["title"], "API не вернул идентификатор чарта", "existing_id", "missing")
        state["charts"][key] = {"id": chart.id, "hash": expected_hash, "canonical_hash": digest(canonical(tabs))}
        charts[key] = chart
        save_state(state_path, state)
    builder = dashboard_builder(client, snapshot, charts, location)
    expected = DashboardConverter.from_domain_create(builder.to_spec()).to_payload()["entry"]
    if state.get("dashboard_id"):
        dashboard = remote("get_dashboard", snapshot["config"]["title"], lambda: client.get.dashboard(by_id=state["dashboard_id"], workbook_id=workbook.id))
        expected_hash = digest(expected["data"])
        comparison = hash_status(dict(dashboard.data), expected["data"], state.get("dashboard_hash"), state.get("dashboard_canonical_hash"))
        require(comparison["status"] != "conflict", "compare_dashboard", snapshot["config"]["title"],
                "Дашборд изменён вручную; автоматическая перезапись остановлена", "saved_or_target", comparison)
        if comparison["status"] != "unchanged":
            replacement = {**dict(dashboard.raw), **expected, "entryId": dashboard.id}
            dashboard = remote("update_dashboard", snapshot["config"]["title"], lambda: client.raw.replace.dashboard(target=dashboard, response_snapshot={"entry": replacement}).mode("publish").execute())
    else:
        dashboard = remote("create_dashboard", snapshot["config"]["title"], builder.build)
    require(bool(dashboard.id), "dashboard_id", snapshot["config"]["title"], "API не вернул идентификатор дашборда", "existing_id", "missing")
    state["dashboard_id"] = dashboard.id
    state["dashboard_hash"] = digest(expected["data"])
    state["dashboard_canonical_hash"] = digest(canonical(expected["data"]))
    state["fingerprint"] = snapshot["fingerprint"]
    save_state(state_path, state)
    # Read-back подтверждает содержание объектов, но не отрисовку в браузере.
    for key, chart in charts.items():
        fetched = remote("readback_editor_chart", key, lambda: client.get.editor_chart(by_id=chart.id, workbook_id=workbook.id))
        tabs = chart_tabs(next(c for c in snapshot["config"]["charts"] if c["key"] == key), snapshot)
        require(all(fetched.data.get(tab) == value for tab, value in tabs.items()), "readback_editor_chart", key, "Не совпало содержимое чарта", digest(tabs), digest({tab: fetched.data.get(tab) for tab in tabs}))
    fetched = remote("readback_dashboard", snapshot["config"]["title"], lambda: client.get.dashboard(by_id=dashboard.id, workbook_id=workbook.id))
    require(digest(canonical(dict(fetched.data))) == state["dashboard_canonical_hash"], "readback_dashboard", snapshot["config"]["title"], "Не совпало содержимое дашборда", state["dashboard_canonical_hash"], digest(canonical(dict(fetched.data))))
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
    parser.add_argument("--export-tabs", action="store_true", help="Также выгрузить вкладки Editor в отдельные JSON/JS-файлы для ручной сборки")
    parser.add_argument("--update-only", action="store_true", help="Только обновить существующие ID: при неполном состоянии остановиться до API")
    parser.add_argument("--diagnose", action="store_true", help="Только чтение существующих объектов API; не меняет состояние или объекты")
    args = parser.parse_args()
    if args.diagnose and (args.apply or args.update_only):
        parser.error("--diagnose нельзя совмещать с --apply/--update-only")
    if args.update_only and not args.apply:
        parser.error("--update-only используется вместе с --apply")
    require(version("datalens-sdk") == "3.2.0", "sdk_version", "SDK", "Установите зависимости requirements.txt", "3.2.0", version("datalens-sdk"))
    snapshot = prepare()
    plan = make_plan(snapshot)
    if args.export_tabs:
        for request in plan["requests"]:
            if "key" in request:
                for tab, content in request["body"]["entry"]["data"].items():
                    path = BUILD / "editor" / request["key"] / (tab + (".json" if tab == "meta" else ".js"))
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(content + "\n", encoding="utf-8")
        print("Вкладки Editor выгружены в datalens/build/editor/.")
    if not args.apply and not args.diagnose:
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
            if args.diagnose:
                report = diagnose(client, snapshot, org_id, os.environ.get("DATALENS_WORKBOOK_ID", ""))
                print(json.dumps(report, ensure_ascii=False, indent=2))
                return 0 if report["safe_to_update"] else 2
            state = apply(client, snapshot, org_id, os.environ.get("DATALENS_WORKBOOK_ID", ""), update_only=args.update_only)
    except DeploymentCheckError as exc:
        print(json.dumps(exc.details, ensure_ascii=False, indent=2))
        return 2
    except Exception as exc:
        frames = traceback.extract_tb(exc.__traceback__)
        frame = frames[-1] if frames else None
        print(json.dumps({"stage": "unclassified_local_or_sdk", "error_class": type(exc).__name__,
                          "location": {"file": Path(frame.filename).name, "line": frame.lineno, "function": frame.name} if frame else None,
                          "expected": "successful_execution", "actual": "failed"}, ensure_ascii=False))
        # Тело ошибки API и HTTP-заголовки не печатаем: они могут содержать данные авторизации.
        raise SystemExit(f"Сборка не завершена ({type(exc).__name__}). Проверьте доступ, конфигурацию и локальное состояние; секреты в лог не записаны.") from None
    print("Объекты созданы/обновлены, содержание проверено через API:", state["dashboard_url"])
    print("Отрисовку, селекторы и доступ нужно проверить в браузере по инструкции. Публичный доступ скрипт не включает.")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DeploymentCheckError as exc:
        print(json.dumps(exc.details, ensure_ascii=False, indent=2))
        raise SystemExit(2) from None
    except AssertionError as exc:
        # prepare.py содержит проверки данных до API: показываем место, не значения.
        frame = traceback.extract_tb(exc.__traceback__)[-1]
        print(json.dumps({"stage": "local_preparation", "object": Path(frame.filename).name,
                          "reason": "Не пройдена проверка локальных данных или схемы SDK",
                          "expected": "invariant_passed", "actual": "AssertionError",
                          "line": frame.lineno, "function": frame.name}, ensure_ascii=False))
        raise SystemExit(2) from None
