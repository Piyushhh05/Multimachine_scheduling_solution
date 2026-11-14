"""CSV/JSON/SQL exports plus held-out deterministic anomaly labels."""
from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, time
from pathlib import Path

from django.apps import apps
from django.db import connection
from django.utils import timezone

from scheduler.models import (
    ChangeoverMatrix, InventoryLot, Machine, Material, Operation, OperationSchedule,
    ProductionOrder, ProductMaterial, ScheduleException, SchedulingRun,
)
from scheduler.services.data_generator import BASE_DATE

EXPORT_MODELS = [
    "Product", "Routing", "Machine", "MachineCalendar", "MachineDowntime", "Material",
    "InventoryLot", "Tooling", "MachineTooling", "RoutingStep", "RoutingStepMachine",
    "ProductMaterial", "LaborShift", "ChangeoverMatrix", "ProductionOrder", "Operation",
    "OperationEligibleMachine", "SchedulingRun", "OperationSchedule", "MaterialReservation",
    "ScheduleException", "AuditEvent",
]


def _json_value(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (int, float, str, bool)) or value is None:
        return value
    return str(value)


def model_rows(model):
    fields = [f for f in model._meta.concrete_fields]
    output = []
    for obj in model.objects.all().order_by(model._meta.pk.name).iterator(chunk_size=1000):
        record = {}
        for field in fields:
            name = field.attname
            record[name] = _json_value(getattr(obj, name))
        output.append(record)
    return output


def export_model(model, target: Path):
    rows = model_rows(model)
    target.mkdir(parents=True, exist_ok=True)
    base = model._meta.db_table
    json_path = target / f"{base}.json"
    csv_path = target / f"{base}.csv"
    json_path.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
    fieldnames = list(rows[0].keys()) if rows else [f.attname for f in model._meta.concrete_fields]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            csv_row = {key: json.dumps(value, sort_keys=True) if isinstance(value, (dict, list)) else value for key, value in row.items()}
            writer.writerow(csv_row)
    return {"table": base, "rows": len(rows), "csv": csv_path.name, "json": json_path.name,
            "sha256_csv": hashlib.sha256(csv_path.read_bytes()).hexdigest()}


def _scenario_records():
    latest = SchedulingRun.objects.filter(status="COMPLETED").first()
    if not latest:
        return [], []
    schedules = list(latest.schedules.select_related("operation__order__product", "machine").order_by("machine_id", "start_at"))
    by_machine = {}
    for schedule in schedules:
        by_machine.setdefault(schedule.machine_id, []).append(schedule)
    scenarios = []

    # 1. Two jobs on one machine are hypothetically assigned the same processing window.
    overlap_pair = next((rows[:2] for rows in by_machine.values() if len(rows) >= 2), None)
    if overlap_pair:
        first, second = overlap_pair
        scenarios.append({
            "scenario_id": "ANOM-001", "issue_type": "MACHINE_OVERLAP", "operation_id": second.operation_id,
            "related_operation_id": first.operation_id, "machine_id": first.machine_id,
            "proposed_start": first.start_at.isoformat(), "proposed_end": first.end_at.isoformat(),
            "proposed_setup_minutes": second.setup_minutes, "expected_detection": "MACHINE_OVERLAP",
            "description": "Move the second operation into the first operation's occupied interval.",
            "evidence": {"conflicting_schedule_id": first.schedule_id},
        })

    # 2. A step is hypothetically started before its predecessor completes.
    second_step = next((s for s in schedules if s.operation.sequence_no > 1), None)
    if second_step:
        predecessor = latest.schedules.filter(operation__order_id=second_step.operation.order_id, operation__sequence_no=second_step.operation.sequence_no - 1).first()
        if predecessor:
            scenarios.append({
                "scenario_id": "ANOM-002", "issue_type": "PRECEDENCE", "operation_id": second_step.operation_id,
                "related_operation_id": predecessor.operation_id, "machine_id": second_step.machine_id,
                "proposed_start": (predecessor.start_at - __import__('datetime').timedelta(minutes=10)).isoformat(),
                "proposed_end": second_step.end_at.isoformat(), "proposed_setup_minutes": second_step.setup_minutes,
                "expected_detection": "PRECEDENCE", "description": "Start a downstream route step before its predecessor is complete.",
                "evidence": {"predecessor_end": predecessor.end_at.isoformat()},
            })

    # 3. Use a delayed lot and propose consuming it one day before its availability date.
    delayed_lot = InventoryLot.objects.filter(available_from__gt=BASE_DATE).select_related("material").first()
    if delayed_lot:
        product_req = ProductMaterial.objects.filter(material=delayed_lot.material).select_related("product").first()
        order = ProductionOrder.objects.filter(product=product_req.product).first() if product_req else None
        op = order.operations.order_by("sequence_no").first() if order else None
        if op:
            proposed_day = delayed_lot.available_from - __import__('datetime').timedelta(days=1)
            proposed = timezone.make_aware(datetime.combine(proposed_day, time(8)))
            scenarios.append({
                "scenario_id": "ANOM-003", "issue_type": "MATERIAL_UNAVAILABLE", "operation_id": op.operation_id,
                "related_operation_id": "", "machine_id": "", "proposed_start": proposed.isoformat(),
                "proposed_end": "", "proposed_setup_minutes": 0, "expected_detection": "MATERIAL_UNAVAILABLE",
                "description": "Attempt to consume a material lot before its available_from date.",
                "evidence": {"material_id": delayed_lot.material_id, "lot_id": delayed_lot.lot_id, "available_from": delayed_lot.available_from.isoformat()},
            })

    # 4. Propose a shift-violating start (04:00) on a normal weekday.
    if schedules:
        row = schedules[0]
        outside = timezone.make_aware(datetime.combine(row.start_at.date(), time(4, 0)))
        scenarios.append({
            "scenario_id": "ANOM-004", "issue_type": "CALENDAR_VIOLATION", "operation_id": row.operation_id,
            "related_operation_id": "", "machine_id": row.machine_id, "proposed_start": outside.isoformat(),
            "proposed_end": (outside + __import__('datetime').timedelta(minutes=row.processing_minutes)).isoformat(),
            "proposed_setup_minutes": 0, "expected_detection": "CALENDAR_VIOLATION",
            "description": "Move an operation outside its machine's 08:00–16:00 weekday calendar.",
            "evidence": {"calendar": "Monday-Friday 08:00-16:00"},
        })

    # 5. Remove a required sequence-dependent setup.
    setup_row = next((s for s in schedules if s.setup_minutes > 0), None)
    if setup_row:
        scenarios.append({
            "scenario_id": "ANOM-005", "issue_type": "CHANGEOVER_MISSING", "operation_id": setup_row.operation_id,
            "related_operation_id": "", "machine_id": setup_row.machine_id, "proposed_start": setup_row.start_at.isoformat(),
            "proposed_end": setup_row.end_at.isoformat(), "proposed_setup_minutes": 0,
            "expected_detection": "CHANGEOVER_MISSING", "description": "Set setup minutes to zero although the machine's from-to matrix requires a changeover.",
            "evidence": {"recorded_setup_minutes": setup_row.setup_minutes},
        })
    return scenarios, latest


def export_ground_truth(target: Path):
    scenarios, _ = _scenario_records()
    target.mkdir(parents=True, exist_ok=True)
    csv_path = target / "ground_truth.csv"
    json_path = target / "ground_truth.json"
    scenario_path = target / "anomaly_scenarios.csv"
    scenario_json_path = target / "anomaly_scenarios.json"
    rows = [{"scenario_id": s["scenario_id"], "operation_id": s["operation_id"], "issue_type": s["issue_type"],
             "ground_truth": "VIOLATION", "expected_detection": s["expected_detection"], "description": s["description"]} for s in scenarios]
    json_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else ["scenario_id", "operation_id", "issue_type", "ground_truth", "expected_detection", "description"])
        writer.writeheader(); writer.writerows(rows)
    fields = ["scenario_id", "issue_type", "operation_id", "related_operation_id", "machine_id", "proposed_start", "proposed_end", "proposed_setup_minutes", "expected_detection", "description", "evidence"]
    with scenario_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for s in scenarios:
            item = dict(s); item["evidence"] = json.dumps(item["evidence"], sort_keys=True); writer.writerow(item)
    scenario_json_path.write_text(json.dumps(scenarios, indent=2, sort_keys=True), encoding="utf-8")
    return {"ground_truth_rows": len(rows), "scenario_rows": len(scenarios), "ground_truth_sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest()}


def export_all(target: str | Path):
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    manifest = []
    for model_name in EXPORT_MODELS:
        model = apps.get_model("scheduler", model_name)
        manifest.append(export_model(model, target))
    labels = export_ground_truth(target)
    # SQLite seed snapshot; portable with the documented SQLite/Django setup.
    sql_path = target / "seed.sql"
    with connection.cursor() as cursor:
        table_names = [apps.get_model("scheduler", model_name)._meta.db_table for model_name in EXPORT_MODELS]
        statements = ["PRAGMA foreign_keys=OFF;", "BEGIN TRANSACTION;"]
        for table in table_names:
            cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=%s", [table])
            create_row = cursor.fetchone()
            if not create_row or not create_row[0]:
                continue
            create_sql = create_row[0]
            statements.append(create_sql + ";")
            cursor.execute(f'SELECT * FROM "{table}"')
            columns = [d[0] for d in cursor.description]
            for row in cursor.fetchall():
                vals = []
                for value in row:
                    if value is None: vals.append("NULL")
                    elif isinstance(value, (int, float)): vals.append(str(value))
                    else: vals.append("'" + str(value).replace("'", "''") + "'")
                statements.append(f'INSERT INTO "{table}" ({", ".join(chr(34)+c+chr(34) for c in columns)}) VALUES ({", ".join(vals)});')
            cursor.execute("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=%s AND sql IS NOT NULL", [table])
            statements.extend([row[0] + ";" for row in cursor.fetchall()])
        statements.extend(["COMMIT;", "PRAGMA foreign_keys=ON;"])
        sql_path.write_text("\n".join(statements) + "\n", encoding="utf-8")
    summary = {"tables": manifest, "labels": labels, "total_rows": sum(item["rows"] for item in manifest)}
    (target / "export_manifest.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def logical_dataset_digest():
    """Stable digest excluding runtime-created scheduling metadata timestamps."""
    digest = hashlib.sha256()
    for model_name in EXPORT_MODELS:
        model = apps.get_model("scheduler", model_name)
        rows = model_rows(model)
        # Runtime timestamps are not part of source-data reproducibility.
        for row in rows:
            for key in list(row):
                if key in {"created_at", "completed_at", "detected_at", "reserved_at", "occurred_at"}:
                    row.pop(key, None)
        digest.update(model_name.encode())
        digest.update(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode())
    return digest.hexdigest()
