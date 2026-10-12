"""Реальные локальные проверки. Проверка DataLens в браузере остаётся отдельной."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import httpx
from datalens_sdk import DataLensClientYC

from deploy import apply, digest, make_plan
from prepare import BUILD, HERE, ROOT, SOURCE_COUNTS, prepare, write_json


def layout_check(dashboard):
    """API описывает координаты как number; SDK может вернуть 36 как 36.0."""
    for tab in dashboard.data["tabs"]:
        layout = tab["layout"]
        assert {row["i"] for row in layout} == {item["id"] for item in tab["items"]}
        for row in layout:
            assert all(float(row[key]).is_integer() for key in ["x", "y", "w", "h"])
            assert row["x"] >= 0 and row["y"] >= 0 and row["w"] > 0 and row["h"] > 0
            assert row["x"] + row["w"] <= 36
        for i, a in enumerate(layout):
            for b in layout[i + 1:]:
                overlap = (a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"]
                           and a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"])
                assert not overlap, (tab["id"], a["i"], b["i"])


def notebook_check():
    import nbformat
    from nbclient import NotebookClient
    from jupyter_client import KernelManager
    from jupyter_client.kernelspec import KernelSpecManager

    paths = [ROOT / "data" / (name + ".csv") for name in SOURCE_COUNTS]
    paths += [p for p in (ROOT / "source").iterdir() if p.is_file()]
    paths += [ROOT / "biscuit_recipe_analysis.ipynb"]
    hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    nb = nbformat.read(ROOT / "biscuit_recipe_analysis.ipynb", as_version=4)
    with tempfile.TemporaryDirectory(prefix="cookies-notebook-") as directory:
        temporary = Path(directory)
        spec = temporary / "python3" / "kernel.json"
        write_json(spec, {"argv": [sys.executable, "-m", "ipykernel_launcher", "-f", "{connection_file}"],
                          "display_name": "Python 3", "language": "python"})
        for name in ["IPYTHONDIR", "JUPYTER_RUNTIME_DIR", "JUPYTER_CONFIG_DIR", "MPLCONFIGDIR", "XDG_CACHE_HOME"]:
            os.environ.setdefault(name, str(temporary / name.lower()))
        manager = KernelManager(kernel_spec_manager=KernelSpecManager(kernel_dirs=[str(temporary)]), kernel_name="python3")
        NotebookClient(nb, timeout=240, km=manager, resources={"metadata": {"path": str(ROOT)}}).execute()
        nbformat.validate(nb)
    for path, expected in hashes.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, f"Результат изменился: {path.name}"
    return {"status": "passed", "code_cells": sum(c.cell_type == "code" for c in nb.cells),
            "csv_reproduced": 10, "original_notebook_and_sources_unchanged": True}


def api_contract_check(snapshot):
    """Официальный SDK и его схемы; ответы заменены HTTP-стабом, сети нет."""
    entries, calls = {}, []
    def handler(request):
        assert request.url.host == "api.datalens.tech"
        assert request.headers["x-dl-api-version"] == "3"
        assert "authorization" not in request.headers
        payload = json.loads(request.content)
        method = request.url.path.rsplit("/", 1)[1]
        calls.append(method)
        if method == "createWorkbook":
            return httpx.Response(200, json={"workbookId": "test-workbook", "title": payload["title"]})
        if method == "getWorkbook":
            return httpx.Response(200, json={"workbookId": "test-workbook", "title": snapshot["config"]["workbook_name"]})
        if method in ["createEditorChart", "createDashboard"]:
            entry = payload["entry"]
            entry_id = "test-entry-" + str(len(entries) + 1)
            entry = {**entry, "entryId": entry_id, "revId": "test-revision", "savedId": "test-revision", "publishedId": "test-revision"}
            entries[entry_id] = entry
            return httpx.Response(200, json={"entry": entry})
        if method in ["getEditorChart", "getDashboard"]:
            entry_id = payload.get("chartId", payload.get("dashboardId"))
            return httpx.Response(200, json={"entry": entries[entry_id]})
        if method in ["updateEditorChart", "updateDashboard"]:
            update = payload["entry"]
            entry_id = update["entryId"]
            entries[entry_id].update(update)
            return httpx.Response(200, json={"entry": entries[entry_id]})
        raise AssertionError(f"Непредусмотренный метод: {method}")

    with tempfile.TemporaryDirectory(prefix="cookies-api-test-") as directory:
        state_path = Path(directory) / "state.json"
        with DataLensClientYC(auth=None, transport=httpx.MockTransport(handler)) as client:
            state = apply(client, snapshot, "test-organization", state_path=state_path)
            assert state["api_readback_verified"] and not state["browser_verified"]
            dashboard = client.get.dashboard(by_id=state["dashboard_id"])
            layout_check(dashboard)
            assert len(dashboard.tabs) == 3
            before = Counter(calls)
            apply(client, snapshot, "test-organization", state_path=state_path)
            after = Counter(calls)
            assert all(after[m] == before[m] for m in ["createWorkbook", "createEditorChart", "createDashboard", "updateEditorChart", "updateDashboard"])
            # Изменение управляемого описания проверяет ветку обновления дашборда.
            changed = json.loads(json.dumps(snapshot))
            changed["config"]["tabs"][0]["note"] += " Проверка обновления."
            apply(client, changed, "test-organization", state_path=state_path)
            assert Counter(calls)["updateDashboard"] == 1
            # Изменение палитры проверяет обновление вкладок каждого Editor-чарта.
            changed["config"]["recipe_colours"]["nutriose"] = "#006AA0"
            apply(client, changed, "test-organization", state_path=state_path)
            assert Counter(calls)["updateEditorChart"] == 18
            # Ручная правка управляемого чарта не должна молча перезаписываться.
            entries[state["charts"]["force_graph"]["id"]]["data"]["prepare"] += "\n// ручное изменение"
            try:
                apply(client, changed, "test-organization", state_path=state_path)
            except AssertionError as exc:
                assert "изменён вручную" in str(exc)
                failed = json.loads(state_path.read_text())
                assert failed["status"] == "applying" and not failed["api_readback_verified"]
            else:
                raise AssertionError("Ручное изменение не было защищено")
    count = Counter(calls)
    assert count["createEditorChart"] == 18 and count["createDashboard"] == 1 and count["createWorkbook"] == 1
    return {"status": "passed_with_mock_transport", "api_version": "3", "charts": 18, "tabs": 3,
            "rerun_without_duplicates": True, "dashboard_update": True, "editor_update": True, "manual_change_protected": True,
            "real_api_called": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--notebook", action="store_true", help="Также выполнить существующий notebook в новом ядре")
    args = parser.parse_args()
    snapshot = prepare()
    before = (ROOT / "data" / "datalens_physical_comparison.csv").read_bytes()
    assert prepare()["fingerprint"] == snapshot["fingerprint"]
    assert (ROOT / "data" / "datalens_physical_comparison.csv").read_bytes() == before
    plan = make_plan(snapshot)
    # В плане нет подключения, датасета, публикации доступа или запроса секретов.
    assert {r["method"] for r in plan["requests"]} == {"createEditorChart", "createDashboard"}
    node = subprocess.run(["node", str(HERE / "check_editor.js"), str(BUILD / "plan.json"), str(BUILD / "data.json")],
                          check=True, capture_output=True, text=True)
    editor = json.loads(node.stdout)
    contract = api_contract_check(snapshot)
    notebook = notebook_check() if args.notebook else {"status": "not_run_in_this_command"}
    report = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(), "input_fingerprint": snapshot["fingerprint"],
        "materialized_csv": {"status": "passed", "rows": 70, "metrics": 14, "reproducible": True},
        "editor_logic": editor, "sdk_contract": contract, "notebook": notebook,
        "real_datalens_api": {"status": "not_run", "reason": "Доступ к аккаунту DataLens не предоставлен для этой проверки."},
        "browser": {"status": "not_run", "reason": "Нужны реальные объекты, аккаунт и проверка интерфейса."},
        "dashboard_created_by_this_check": False, "public_dashboard_url": None,
    }
    write_json(HERE / "validation_report.json", report)
    print(f"Локальные проверки прошли: {editor['prepare_executions']} выполнений Prepare, SDK v3, повтор без дубликатов.")
    print("Реальный API и интерфейс DataLens не тестировались. Отчёт: datalens/validation_report.json.")


if __name__ == "__main__":
    main()
