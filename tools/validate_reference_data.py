#!/usr/bin/env python3
"""Validate the bundled CSV/JSON synthetic reference export with only Python stdlib."""
import argparse
import csv
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path


def read_rows(base, name):
    path = base / f"{name}.csv"
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="data/reference")
    parser.add_argument("--scale", choices=["starter", "full"], default="full")
    args = parser.parse_args()
    base = Path(args.data)
    manifest = json.loads((base / "source_manifest.json").read_text(encoding="utf-8"))
    names = [x["table"] for x in manifest["files"]]
    tables = {name: read_rows(base, name) for name in names}
    fail = {}
    # Baseline volume contract.
    expected = {"products": 100, "machines": 40, "production_orders": 500, "operations": 2000, "materials": 200} if args.scale == "full" else {"products": 20, "machines": 10, "production_orders": 50, "operations": 200, "materials": 40}
    fail["expected_volumes"] = {k: len(tables[k]) == v for k, v in expected.items()}

    # Primary identifiers and deterministic nomenclature.
    key_fields = {"products":"product_id", "routings":"routing_id", "machines":"machine_id", "materials":"material_id",
                  "inventory_lots":"lot_id", "tooling":"tooling_id", "routing_steps":"step_id", "production_orders":"order_id",
                  "operations":"operation_id", "machine_downtime":"downtime_id", "changeover_matrix":"changeover_id"}
    id_checks = {}
    for table, key in key_fields.items():
        values = [r[key] for r in tables[table]]
        id_checks[f"{table}_unique_pk"] = len(values) == len(set(values))
    id_checks["product_id_pattern"] = all(re.fullmatch(r"PROD-2026-\d{6}", r["product_id"]) for r in tables["products"])
    id_checks["machine_id_pattern"] = all(re.fullmatch(r"MACH-\d{3}", r["machine_id"]) for r in tables["machines"])
    id_checks["order_id_pattern"] = all(re.fullmatch(r"PO-2026-\d{6}", r["order_id"]) for r in tables["production_orders"])
    id_checks["operation_id_pattern"] = all(re.fullmatch(r"OP-PO-2026-\d{6}-\d{2}", r["operation_id"]) for r in tables["operations"])
    fail["identifier_checks"] = id_checks

    # Foreign-key joins and relationship cardinality.
    product_ids = {r["product_id"] for r in tables["products"]}
    route_map = {r["routing_id"]: r["product_id"] for r in tables["routings"]}
    machine_ids = {r["machine_id"] for r in tables["machines"]}
    material_ids = {r["material_id"] for r in tables["materials"]}
    order_ids = {r["order_id"] for r in tables["production_orders"]}
    op_ids = {r["operation_id"] for r in tables["operations"]}
    step_map = {r["step_id"]: r["routing_id"] for r in tables["routing_steps"]}
    step_ids = set(step_map)
    fail["foreign_key_checks"] = {
        "routing_product": all(r["product_id"] in product_ids for r in tables["routings"]),
        "routing_step_parent": all(r["routing_id"] in route_map for r in tables["routing_steps"]),
        "machine_calendar_parent": all(r["machine_id"] in machine_ids for r in tables["machine_calendars"]),
        "downtime_machine_parent": all(r["machine_id"] in machine_ids for r in tables["machine_downtime"]),
        "lot_material_parent": all(r["material_id"] in material_ids for r in tables["inventory_lots"]),
        "bom_parent_joins": all(r["product_id"] in product_ids and r["material_id"] in material_ids for r in tables["product_material"]),
        "order_product_route_join": all(r["product_id"] in product_ids and route_map.get(r["routing_id"]) == r["product_id"] for r in tables["production_orders"]),
        "operation_order_step_join": all(r["order_id"] in order_ids and r["routing_step_id"] in step_ids for r in tables["operations"]),
        "operation_eligible_join": all(r["operation_id"] in op_ids and r["machine_id"] in machine_ids for r in tables["operation_eligible_machine"]),
        "step_eligible_join": all(r["routing_step_id"] in step_ids and r["machine_id"] in machine_ids for r in tables["routing_step_machines"]),
        "machine_tooling_join": all(r["machine_id"] in machine_ids and r["tooling_id"] in {x["tooling_id"] for x in tables["tooling"]} for r in tables["machine_tooling"]),
    }

    # Derived operation duration is quantity × the routing's base minutes per unit (minimum 15).
    step_rows = {r["step_id"]: r for r in tables["routing_steps"]}
    order_rows = {r["order_id"]: r for r in tables["production_orders"]}
    fail["derived_values"] = {
        "operation_count_equals_order_steps": len(tables["operations"]) == len(tables["production_orders"]) * 4,
        "operation_duration_reconciles": all(int(o["duration_minutes"]) == max(15, int(step_rows[o["routing_step_id"]]["base_minutes_per_unit"]) * int(order_rows[o["order_id"]]["quantity"])) for o in tables["operations"]),
        "orders_have_four_sequenced_steps": all(sorted(int(o["sequence_no"]) for o in tables["operations"] if o["order_id"] == order_id) == [1,2,3,4] for order_id in order_ids),
        "positive_inventory_quantities": all(float(r["quantity"]) > 0 for r in tables["inventory_lots"]),
        "valid_due_dates": all(r["due_date"] >= r["order_date"] for r in tables["production_orders"]),
        "valid_machine_calendars": all(r["shift_end"] > r["shift_start"] and int(r["weekday"]) in range(5) for r in tables["machine_calendars"]),
    }

    # Ground truth is separate and not mixed into operational features.
    gt = tables["ground_truth"]
    anomalies = tables["anomaly_scenarios"]
    fail["ground_truth_checks"] = {
        "five_required_labels": len(gt) == 5 and {x["issue_type"] for x in gt} == {"MACHINE_OVERLAP","PRECEDENCE","MATERIAL_UNAVAILABLE","CALENDAR_VIOLATION","CHANGEOVER_MISSING"},
        "all_labels_reference_real_operations": all(x["operation_id"] in op_ids for x in gt),
        "scenario_rows_match_labels": {x["scenario_id"] for x in anomalies} == {x["scenario_id"] for x in gt},
        "no_anomaly_feature_in_operations": not any("issue_type" in r or "ground_truth" in r for r in tables["operations"]),
    }

    # Confirm SQL seed contains all reference input and has no orphan FK rows.
    db = sqlite3.connect(base / "reference_seed.sqlite3")
    try:
        fk_rows = db.execute("PRAGMA foreign_key_check").fetchall()
        db_counts = {name: db.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] for name in ["products","machines","materials","production_orders","operations"]}
    finally:
        db.close()
    fail["sqlite_seed_checks"] = {"foreign_key_check_empty": len(fk_rows) == 0, "core_counts_match_csv": db_counts == {k:len(tables[k]) for k in db_counts}}

    flat = []
    for group in fail.values():
        flat.extend(group.values())
    def all_true(x):
        if isinstance(x, dict): return all(all_true(v) for v in x.values())
        return bool(x)
    result = {"seed": manifest["seed"], "scale": manifest["scale"], "row_counts": manifest["row_counts"],
              "checks": fail, "passed": all(all_true(x) for x in fail.values())}
    print(json.dumps(result, indent=2))
    if not result["passed"]:
        raise SystemExit(1)

if __name__ == "__main__": main()
