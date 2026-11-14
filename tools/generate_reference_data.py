#!/usr/bin/env python3
"""Stdlib-only deterministic reference export. The Django command builds the same schema in SQLite."""
import argparse
import csv
import hashlib
import json
import random
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

SEED_DEFAULT = 20261022
BASE_DATE = date(2026, 11, 16)
PLANT_TZ = timezone(timedelta(hours=5, minutes=30))

def iso_dt(value):
    return value.replace(tzinfo=PLANT_TZ).isoformat()

CATEGORIES = ["MACHINED", "ASSEMBLY", "ELECTRICAL", "PACKAGING", "FINISHED"]
MACHINE_TYPES = [f"CELL-{c}" for c in "ABCDEFGH"]


def fmt(value):
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    return value


def build(seed=SEED_DEFAULT, scale="full"):
    rng = random.Random(seed)
    counts = {"starter": (20, 10, 50, 40), "full": (100, 40, 500, 200)}
    if scale not in counts:
        raise ValueError("scale must be starter or full")
    n_products, n_machines, n_orders, n_materials = counts[scale]
    tables = {}

    products = []
    for i in range(1, n_products + 1):
        products.append({"product_id": f"PROD-2026-{i:06d}", "sku_code": f"SKU-{i:04d}",
                         "name": f"{rng.choice(['Precision','Modular','Industrial','Smart'])} {rng.choice(['Drive','Valve','Panel','Unit','Assembly'])} {i:03d}",
                         "category": CATEGORIES[(i-1) % 5], "unit": "EA", "lifecycle_state": "ACTIVE",
                         "created_at": iso_dt(datetime.combine(BASE_DATE - timedelta(days=60), time(8)))})
    tables["products"] = products

    machines = []
    machine_tools = []
    machine_tool_sets = {}
    for i in range(1, n_machines + 1):
        machine_id = f"MACH-{i:03d}"
        mtype = MACHINE_TYPES[(i-1) % len(MACHINE_TYPES)]
        machines.append({"machine_id": machine_id, "name": f"Production Cell {i:02d}", "machine_type": mtype,
                         "capacity_units_per_hour": rng.choice([0.8, 1.0, 1.2, 1.5]), "location": f"Plant {1+(i-1)//20}", "lifecycle_state": "ACTIVE",
                         "available_from": "2026-01-01", "available_to": ""})
        selected = sorted(rng.sample(range(1, 11), 4))
        machine_tool_sets[machine_id] = set(selected)
        for t in selected:
            machine_tools.append({"machine_id": machine_id, "tooling_id": f"TOOL-{t:03d}"})
    tables["machines"] = machines
    tables["tooling"] = [{"tooling_id": f"TOOL-{i:03d}", "name": f"Shared fixture {i:02d}", "capacity": 1, "lifecycle_state": "ACTIVE"} for i in range(1, 11)]
    tables["machine_tooling"] = machine_tools

    materials = [{"material_id": f"MAT-{i:04d}", "name": f"{rng.choice(['Alloy','Polymer','Copper','Fastener','Resin','Circuit'])} stock {i:03d}",
                  "category": rng.choice(["RAW", "COMPONENT", "PACKAGING"]), "unit": ["KG", "L", "M", "EA"][(i-1)%4], "lifecycle_state": "ACTIVE"} for i in range(1, n_materials+1)]
    tables["materials"] = materials

    calendars = [{"machine_id": m["machine_id"], "weekday": d, "shift_start": "08:00:00", "shift_end": "16:00:00", "capacity_minutes": 480,
                  "effective_from": "2026-01-01", "effective_to": "2027-12-31"} for m in machines for d in range(5)]
    tables["machine_calendars"] = calendars
    tables["labor_shifts"] = [{"weekday": d, "shift_start": "08:00:00", "shift_end": "16:00:00", "workers_available": 36,
                               "effective_from": "2026-01-01", "effective_to": "2027-12-31"} for d in range(5)]

    downtimes = []
    di = 1
    for m in machines:
        for _ in range(2):
            day = BASE_DATE + timedelta(days=rng.randint(0, 70))
            while day.weekday() >= 5:
                day += timedelta(days=1)
            start = datetime.combine(day, time(rng.choice([9, 11, 13, 14])))
            downtimes.append({"downtime_id": f"DOWN-{di:05d}", "machine_id": m["machine_id"], "start_at": iso_dt(start),
                              "end_at": iso_dt(start + timedelta(minutes=rng.choice([60,90,120]))),
                              "reason": rng.choice(["Preventive maintenance", "Tool calibration", "Unplanned breakdown"]),
                              "issue_type": "UNPLANNED_DOWNTIME" if di % 3 == 0 else "PLANNED_DOWNTIME"})
            di += 1
    tables["machine_downtime"] = downtimes

    setup_families = ["START"] + CATEGORIES
    changeovers = []
    for m in machines:
        for from_setup in setup_families:
            for to_setup in CATEGORIES:
                mins = 0 if from_setup in ("START", to_setup) else rng.randint(10, 45)
                changeovers.append({"changeover_id": f"CO-{m['machine_id']}-{setup_families.index(from_setup):02d}-{CATEGORIES.index(to_setup):02d}",
                                    "machine_id": m["machine_id"], "from_setup": from_setup, "to_setup": to_setup,
                                    "changeover_minutes": mins, "effective_from": "2026-01-01", "effective_to": "2027-12-31"})
    tables["changeover_matrix"] = changeovers

    delayed = set(rng.sample([m["material_id"] for m in materials], k=max(8, n_materials//10)))
    lots = []
    li = 1
    for mat in materials:
        for lot_n in range(1,4):
            delayed_first = mat["material_id"] in delayed and lot_n == 1
            available = BASE_DATE + timedelta(days=rng.randint(10+(lot_n-1)*2,18+(lot_n-1)*3) if mat["material_id"] in delayed else rng.randint(0,10))
            lots.append({"lot_id": f"LOT-{li:06d}", "material_id": mat["material_id"], "lot_number": f"{mat['material_id']}-L{lot_n:02d}",
                         "quantity": round(rng.uniform(500,1400),3), "available_from": available.isoformat(), "expiry_date": "", "quality_status": "AVAILABLE"})
            li += 1
    tables["inventory_lots"] = lots

    routings = []
    routing_steps = []
    step_eligible = {}
    machine_by_type = {}
    for machine in machines:
        machine_by_type.setdefault(machine["machine_type"], []).append(machine["machine_id"])
    for p in products:
        route_id = f"ROUTE-{p['sku_code']}-01"
        routings.append({"routing_id": route_id, "product_id": p["product_id"], "version": 1,
                         "effective_from": "2026-01-01", "effective_to": "2027-12-31", "lifecycle_state": "ACTIVE",
                         "created_at": iso_dt(datetime.combine(BASE_DATE - timedelta(days=30), time(8)))})
        for seq, name in enumerate(["Cutting", "Machining", "Assembly", "Inspection"], start=1):
            mtype = MACHINE_TYPES[(seq + (int(p["product_id"][-2:]) % len(MACHINE_TYPES))) % len(MACHINE_TYPES)]
            eligible = rng.sample(machine_by_type[mtype], min(3, len(machine_by_type[mtype])))
            tool = f"TOOL-{rng.randint(1,10):03d}" if rng.random() < 0.72 else ""
            if tool:
                ti = int(tool[-3:])
                for machine_id in eligible:
                    machine_tool_sets[machine_id].add(ti)
                    if not any(x["machine_id"] == machine_id and x["tooling_id"] == tool for x in machine_tools):
                        machine_tools.append({"machine_id": machine_id, "tooling_id": tool})
            step_id = f"RSTEP-{p['sku_code']}-{seq:02d}"
            routing_steps.append({"step_id": step_id, "routing_id": route_id, "sequence_no": seq, "name": name,
                                  "base_minutes_per_unit": rng.randint(2,4), "required_workers": rng.choice([1,1,2]),
                                  "tooling_id": tool, "machine_type_required": mtype, "setup_family": p["category"]})
            step_eligible[step_id] = eligible
    tables["routings"] = routings
    tables["routing_steps"] = routing_steps
    tables["routing_step_machines"] = [{"routing_step_id": sid, "machine_id": mid} for sid, mids in step_eligible.items() for mid in mids]
    tables["machine_tooling"] = machine_tools

    product_materials = []
    for p in products:
        for mat in rng.sample(materials, rng.randint(2,4)):
            product_materials.append({"product_id": p["product_id"], "material_id": mat["material_id"], "quantity_per_unit": round(rng.uniform(0.15,1.8),4)})
    tables["product_material"] = product_materials

    products_by_id = {p["product_id"]: p for p in products}
    routes_by_product = {r["product_id"]: r for r in routings}
    step_by_route = {}
    for step in routing_steps:
        step_by_route.setdefault(step["routing_id"], []).append(step)
    orders = []
    operations = []
    op_eligible = []
    for i in range(1, n_orders+1):
        product = rng.choice(products)
        order_day = BASE_DATE - timedelta(days=rng.randint(0,5))
        qty = rng.randint(10,50)
        priority = rng.choices([1,2,3,4,5], weights=[8,17,45,22,8], k=1)[0]
        due = order_day + timedelta(days=rng.randint(8,20) if priority == 1 else rng.randint(12,38))
        order_id = f"PO-2026-{i:06d}"
        route = routes_by_product[product["product_id"]]
        orders.append({"order_id": order_id, "product_id": product["product_id"], "routing_id": route["routing_id"],
                       "quantity": qty, "order_date": order_day.isoformat(), "due_date": due.isoformat(), "priority": priority, "status": "RELEASED",
                       "created_at": iso_dt(datetime.combine(order_day, time(8)))})
        for step in sorted(step_by_route[route["routing_id"]], key=lambda x: x["sequence_no"]):
            op_id = f"OP-{order_id}-{step['sequence_no']:02d}"
            operations.append({"operation_id": op_id, "order_id": order_id, "routing_step_id": step["step_id"],
                              "sequence_no": step["sequence_no"], "duration_minutes": max(15, step["base_minutes_per_unit"]*qty),
                              "required_workers": step["required_workers"], "status": "PENDING"})
            op_eligible.extend({"operation_id": op_id, "machine_id": mid} for mid in step_eligible[step["step_id"]])
    tables["production_orders"] = orders
    tables["operations"] = operations
    tables["operation_eligible_machine"] = op_eligible

    # Scheduling tables exist as headers only in source exports; the Django engine writes assignments.
    tables["scheduling_runs"] = []
    tables["operation_schedules"] = []
    tables["material_reservations"] = []
    tables["schedule_exceptions"] = []
    tables["audit_events"] = [{"event_id": f"EVT-SEED-{seed}-{scale.upper()}", "entity_type": "DATASET", "entity_id": f"{seed}-{scale}",
                                "event_type": "SYNTHETIC_DATASET_GENERATED", "actor": "data-generator", "payload": json.dumps({"seed":seed,"scale":scale,"base_date":BASE_DATE.isoformat()}),
                                "occurred_at": iso_dt(datetime.combine(BASE_DATE, time(8)))}]

    # Separate held-out anomaly labels and payloads. These are never used as schedule input.
    op_by_order_seq = {(o["order_id"], o["sequence_no"]): o for o in operations}
    shared = None
    links_by_op = {}
    for x in op_eligible:
        links_by_op.setdefault(x["operation_id"], set()).add(x["machine_id"])
    for i, op_a in enumerate(operations):
        for op_b in operations[i+1:]:
            common = links_by_op[op_a["operation_id"]] & links_by_op[op_b["operation_id"]]
            if common:
                shared = (op_a, op_b, sorted(common)[0]); break
        if shared: break
    scenarios = []
    if shared:
        a,b,machine_id=shared
        scenarios.append({"scenario_id":"ANOM-001","issue_type":"MACHINE_OVERLAP","operation_id":b["operation_id"],"related_operation_id":a["operation_id"],"machine_id":machine_id,
                          "proposed_start":"2026-11-16T08:00:00+05:30","proposed_end":"2026-11-16T10:00:00+05:30","proposed_setup_minutes":0,"expected_detection":"MACHINE_OVERLAP",
                          "description":"Two otherwise eligible jobs are hypothetically assigned overlapping time on one machine.","evidence":{}})
    second = next((o for o in operations if o["sequence_no"] == 2), None)
    if second:
        pred = op_by_order_seq[(second["order_id"],1)]
        scenarios.append({"scenario_id":"ANOM-002","issue_type":"PRECEDENCE","operation_id":second["operation_id"],"related_operation_id":pred["operation_id"],"machine_id":"",
                          "proposed_start":"2026-11-16T08:00:00+05:30","proposed_end":"2026-11-16T09:00:00+05:30","proposed_setup_minutes":0,"expected_detection":"PRECEDENCE",
                          "description":"A downstream operation is moved ahead of its predecessor.","evidence":{}})
    delayed_lot = next((x for x in lots if x["available_from"] > BASE_DATE.isoformat()), None)
    if delayed_lot:
        related_bom = next((x for x in product_materials if x["material_id"] == delayed_lot["material_id"]), None)
        if related_bom:
            order = next((o for o in orders if o["product_id"] == related_bom["product_id"]), None)
            if order:
                first_op = op_by_order_seq[(order["order_id"],1)]
                day = date.fromisoformat(delayed_lot["available_from"]) - timedelta(days=1)
                scenarios.append({"scenario_id":"ANOM-003","issue_type":"MATERIAL_UNAVAILABLE","operation_id":first_op["operation_id"],"related_operation_id":None,"machine_id":None,
                                  "proposed_start":iso_dt(datetime.combine(day,time(8))),"proposed_end":"","proposed_setup_minutes":0,"expected_detection":"MATERIAL_UNAVAILABLE",
                                  "description":"An order attempts to consume a delayed material lot one day before it is available.","evidence":{"lot_id":delayed_lot["lot_id"],"material_id":delayed_lot["material_id"],"available_from":delayed_lot["available_from"]}})
    first_op = operations[0]
    first_machine = sorted(links_by_op[first_op["operation_id"]])[0]
    scenarios.append({"scenario_id":"ANOM-004","issue_type":"CALENDAR_VIOLATION","operation_id":first_op["operation_id"],"related_operation_id":None,"machine_id":first_machine,
                      "proposed_start":"2026-11-16T04:00:00+05:30","proposed_end":"2026-11-16T05:00:00+05:30","proposed_setup_minutes":0,"expected_detection":"CALENDAR_VIOLATION",
                      "description":"An operation is assigned outside its weekday 08:00–16:00 shift.","evidence":{"calendar":"Monday-Friday 08:00-16:00"}})
    op_change = next((o for o in operations if any(x["operation_id"] == o["operation_id"] for x in op_eligible)), first_op)
    m_id = sorted(links_by_op[op_change["operation_id"]])[0]
    step = next(s for s in routing_steps if s["step_id"] == op_change["routing_step_id"])
    from_family = next(c for c in CATEGORIES if c != step["setup_family"])
    required = next(x["changeover_minutes"] for x in changeovers if x["machine_id"] == m_id and x["from_setup"] == from_family and x["to_setup"] == step["setup_family"])
    scenarios.append({"scenario_id":"ANOM-005","issue_type":"CHANGEOVER_MISSING","operation_id":op_change["operation_id"],"related_operation_id":None,"machine_id":m_id,
                      "proposed_start":"2026-11-16T09:00:00+05:30","proposed_end":"2026-11-16T10:00:00+05:30","proposed_setup_minutes":0,"expected_detection":"CHANGEOVER_MISSING",
                      "description":"A sequence-dependent setup is omitted even though the from-to matrix requires it.","evidence":{"assumed_from_setup":from_family,"required_setup_minutes":required}})
    tables["ground_truth"] = [{"scenario_id":s["scenario_id"],"operation_id":s["operation_id"],"issue_type":s["issue_type"],"ground_truth":"VIOLATION","expected_detection":s["expected_detection"],"description":s["description"]} for s in scenarios]
    tables["anomaly_scenarios"] = [{**s, "evidence":json.dumps(s["evidence"],sort_keys=True)} for s in scenarios]
    return tables


EMPTY_HEADERS = {
    "scheduling_runs": ["run_id", "strategy", "seed", "status", "planning_start", "created_at", "completed_at", "metrics"],
    "operation_schedules": ["schedule_id", "run_id", "operation_id", "machine_id", "start_at", "end_at", "setup_minutes", "processing_minutes", "decision_reason", "created_at"],
    "material_reservations": ["reservation_id", "run_id", "order_id", "material_id", "lot_id", "quantity", "reserved_at"],
    "schedule_exceptions": ["id", "run_id", "operation_id", "issue_type", "severity", "message", "details", "detected_at"],
}


def write_sql_seed(tables, target):
    import sqlite3
    db_path = target / "reference_seed.sqlite3"
    if db_path.exists(): db_path.unlink()
    pk = {"products":"product_id", "routings":"routing_id", "machines":"machine_id", "machine_downtime":"downtime_id",
          "materials":"material_id", "inventory_lots":"lot_id", "tooling":"tooling_id", "routing_steps":"step_id",
          "changeover_matrix":"changeover_id", "production_orders":"order_id", "operations":"operation_id",
          "scheduling_runs":"run_id", "operation_schedules":"schedule_id", "audit_events":"event_id",
          "ground_truth":"scenario_id", "anomaly_scenarios":"scenario_id", "schedule_exceptions":"id"}
    composite = {"machine_calendars":["machine_id","weekday","shift_start","effective_from"],
                 "labor_shifts":["weekday","shift_start","effective_from"],
                 "machine_tooling":["machine_id","tooling_id"], "routing_step_machines":["routing_step_id","machine_id"],
                 "product_material":["product_id","material_id"], "operation_eligible_machine":["operation_id","machine_id"]}
    fks = {
        "routings": {"product_id":("products","product_id")},
        "machine_calendars": {"machine_id":("machines","machine_id")},
        "machine_downtime": {"machine_id":("machines","machine_id")},
        "inventory_lots": {"material_id":("materials","material_id")},
        "machine_tooling": {"machine_id":("machines","machine_id"),"tooling_id":("tooling","tooling_id")},
        "routing_steps": {"routing_id":("routings","routing_id"),"tooling_id":("tooling","tooling_id")},
        "routing_step_machines": {"routing_step_id":("routing_steps","step_id"),"machine_id":("machines","machine_id")},
        "product_material": {"product_id":("products","product_id"),"material_id":("materials","material_id")},
        "changeover_matrix": {"machine_id":("machines","machine_id")},
        "production_orders": {"product_id":("products","product_id"),"routing_id":("routings","routing_id")},
        "operations": {"order_id":("production_orders","order_id"),"routing_step_id":("routing_steps","step_id")},
        "operation_eligible_machine": {"operation_id":("operations","operation_id"),"machine_id":("machines","machine_id")},
        "operation_schedules": {"run_id":("scheduling_runs","run_id"),"operation_id":("operations","operation_id"),"machine_id":("machines","machine_id")},
        "material_reservations": {"run_id":("scheduling_runs","run_id"),"order_id":("production_orders","order_id"),"material_id":("materials","material_id"),"lot_id":("inventory_lots","lot_id")},
        "schedule_exceptions": {"run_id":("scheduling_runs","run_id"),"operation_id":("operations","operation_id")},
        "ground_truth": {"operation_id":("operations","operation_id")},
        "anomaly_scenarios": {"operation_id":("operations","operation_id"),"related_operation_id":("operations","operation_id"),"machine_id":("machines","machine_id")},
    }
    numeric = {"version","weekday","capacity_minutes","workers_available","changeover_minutes","capacity","sequence_no","base_minutes_per_unit","required_workers","quantity","priority","duration_minutes","setup_minutes","processing_minutes","capacity_units_per_hour","quantity_per_unit"}
    db = sqlite3.connect(db_path)
    try:
        db.execute("PRAGMA foreign_keys=OFF")
        for table, rows in sorted(tables.items()):
            cols = list(rows[0].keys()) if rows else EMPTY_HEADERS.get(table, ["record_id"])
            definitions = []
            for col in cols:
                typ = "NUMERIC" if col in numeric else "TEXT"
                nullable = "" if col in {"tooling_id","expiry_date","effective_to","completed_at","related_operation_id","machine_id","operation_id"} else ""
                definitions.append(f'"{col}" {typ}{nullable}')
            if table in pk:
                definitions.append(f'PRIMARY KEY("{pk[table]}")')
            elif table in composite:
                definitions.append("PRIMARY KEY(" + ",".join(f'"{x}"' for x in composite[table]) + ")")
            for col, (parent, parent_pk) in fks.get(table, {}).items():
                if col in cols:
                    definitions.append(f'FOREIGN KEY("{col}") REFERENCES "{parent}"("{parent_pk}")')
            db.execute(f'CREATE TABLE "{table}" ({", ".join(definitions)})')
        for table, rows in sorted(tables.items()):
            if not rows: continue
            cols = list(rows[0].keys())
            placeholders = ",".join("?" for _ in cols)
            colsql = ",".join(f'"{x}"' for x in cols)
            values = []
            nullable_fk_columns = {"tooling_id", "expiry_date", "effective_to", "completed_at", "related_operation_id", "machine_id", "operation_id"}
            for row in rows:
                values.append(tuple(
                    None if (row[c] == "" and c in nullable_fk_columns) else
                    json.dumps(row[c], sort_keys=True) if isinstance(row[c], (dict,list)) else row[c]
                    for c in cols
                ))
            db.executemany(f'INSERT INTO "{table}" ({colsql}) VALUES ({placeholders})', values)
        db.commit()
        db.execute("PRAGMA foreign_keys=ON")
    finally:
        db.close()
    con = sqlite3.connect(db_path)
    try:
        dump = "PRAGMA foreign_keys=OFF;\n" + "\n".join(con.iterdump()) + "\nPRAGMA foreign_keys=ON;\n"
        (target / "seed.sql").write_text(dump, encoding="utf-8")
    finally:
        con.close()


def write_dataset(tables, target, seed=SEED_DEFAULT, scale="full"):
    target.mkdir(parents=True, exist_ok=True)
    manifest = {"seed": seed, "scale": scale, "base_date": BASE_DATE.isoformat(), "files": [], "source": "synthetic-only"}
    for name, rows in sorted(tables.items()):
        json_bytes = (json.dumps(rows, indent=2, sort_keys=True) + "\n").encode()
        json_path = target / f"{name}.json"
        json_path.write_bytes(json_bytes)
        if rows:
            headers = list(rows[0].keys())
        else:
            headers = EMPTY_HEADERS.get(name, ["record_id"])
        csv_path = target / f"{name}.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            writer.writerows(rows)
        manifest["files"].append({"table":name,"rows":len(rows),"csv_sha256":hashlib.sha256(csv_path.read_bytes()).hexdigest(),"json_sha256":hashlib.sha256(json_bytes).hexdigest()})
    manifest["total_rows"] = sum(len(rows) for rows in tables.values())
    manifest["row_counts"] = {name:len(rows) for name,rows in sorted(tables.items())}
    manifest_path = target / "source_manifest.json"
    manifest_path.write_text(json.dumps(manifest,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    write_sql_seed(tables, target)
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=SEED_DEFAULT)
    parser.add_argument("--scale", choices=["starter","full"], default="full")
    parser.add_argument("--output", default="data/reference")
    args = parser.parse_args()
    tables = build(args.seed,args.scale)
    manifest = write_dataset(tables,Path(args.output),args.seed,args.scale)
    print(json.dumps({"seed":args.seed,"scale":args.scale,"total_rows":manifest["total_rows"],"counts":manifest["row_counts"]},indent=2))

if __name__ == "__main__": main()
