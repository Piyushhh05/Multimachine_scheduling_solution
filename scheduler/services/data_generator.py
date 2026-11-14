"""Deterministic synthetic manufacturing master and transaction data generator."""
from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db import connection, transaction
from django.utils import timezone

from scheduler.models import (
    AuditEvent, ChangeoverMatrix, InventoryLot, LaborShift, Machine, MachineCalendar, SchedulingRun,
    MachineDowntime, MachineTooling, Material, Operation, OperationEligibleMachine,
    Product, ProductMaterial, ProductionOrder, Routing, RoutingStep, RoutingStepMachine,
    Tooling,
)

SEED = 20261022
BASE_DATE = date(2026, 11, 16)
CATEGORIES = ["MACHINED", "ASSEMBLY", "ELECTRICAL", "PACKAGING", "FINISHED"]
MACHINE_TYPES = [f"CELL-{c}" for c in "ABCDEFGH"]
STEP_NAMES = ["Cutting", "Machining", "Assembly", "Inspection"]


def _stamp(day: date, hour: int = 8) -> datetime:
    return timezone.make_aware(datetime.combine(day, time(hour, 0)))


def clear_synthetic_data():
    """Delete generated domain rows in dependency order; admin/auth users are untouched."""
    SchedulingRun.objects.all().delete()
    Operation.objects.all().delete()
    ProductionOrder.objects.all().delete()
    RoutingStepMachine.objects.all().delete()
    RoutingStep.objects.all().delete()
    Routing.objects.all().delete()
    ProductMaterial.objects.all().delete()
    InventoryLot.objects.all().delete()
    Material.objects.all().delete()
    MachineDowntime.objects.all().delete()
    MachineCalendar.objects.all().delete()
    LaborShift.objects.all().delete()
    ChangeoverMatrix.objects.all().delete()
    MachineTooling.objects.all().delete()
    Tooling.objects.all().delete()
    Machine.objects.all().delete()
    Product.objects.all().delete()
    AuditEvent.objects.all().delete()
    # SQLite AUTOINCREMENT counters survive DELETE. Reset app sequences so a second
    # generation from the same seed yields the same surrogate keys as the first.
    if connection.vendor == "sqlite":
        try:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM sqlite_sequence WHERE name LIKE 'scheduler_%'")
        except Exception:
            # A fresh database may not have sqlite_sequence until the first autoincrement row.
            pass


@transaction.atomic
def generate_dataset(seed: int = SEED, scale: str = "full") -> dict:
    """Build valid baseline records. Ground-truth anomalies are exported separately."""
    rng = random.Random(seed)
    if scale == "starter":
        counts = {"products": 20, "machines": 10, "orders": 50, "materials": 40, "operations_per_order": 4}
    elif scale == "full":
        counts = {"products": 100, "machines": 40, "orders": 500, "materials": 200, "operations_per_order": 4}
    else:
        raise ValueError("scale must be 'starter' or 'full'")

    clear_synthetic_data()

    # 1) Parent/master records: products, machines, tools, materials.
    products = []
    for i in range(1, counts["products"] + 1):
        products.append(Product(
            product_id=f"PROD-2026-{i:06d}", sku_code=f"SKU-{i:04d}",
            name=f"{rng.choice(['Precision', 'Modular', 'Industrial', 'Smart'])} {rng.choice(['Drive', 'Valve', 'Panel', 'Unit', 'Assembly'])} {i:03d}",
            category=CATEGORIES[(i - 1) % len(CATEGORIES)], unit="EA", created_at=_stamp(BASE_DATE - timedelta(days=60)),
        ))
    Product.objects.bulk_create(products)

    tools = [Tooling(tooling_id=f"TOOL-{i:03d}", name=f"Shared fixture {i:02d}", capacity=1) for i in range(1, 11)]
    Tooling.objects.bulk_create(tools)
    tool_by_id = {t.tooling_id: t for t in Tooling.objects.all()}

    machines = []
    for i in range(1, counts["machines"] + 1):
        machines.append(Machine(
            machine_id=f"MACH-{i:03d}", name=f"Production Cell {i:02d}",
            machine_type=MACHINE_TYPES[(i - 1) % len(MACHINE_TYPES)],
            capacity_units_per_hour=Decimal(str(rng.choice([0.8, 1.0, 1.2, 1.5]))),
            location=f"Plant {1 + (i - 1) // 20}", available_from=date(2026, 1, 1),
        ))
    Machine.objects.bulk_create(machines)
    machine_by_type = {}
    for machine in Machine.objects.all():
        machine_by_type.setdefault(machine.machine_type, []).append(machine)

    # Machine-tool compatibility; the tool shared across eligible operations has capacity 1.
    machine_tool_links = []
    machine_tools = {m.machine_id: set() for m in Machine.objects.all()}
    for machine in Machine.objects.all():
        selected = rng.sample(tools, k=4)
        for tool in selected:
            machine_tools[machine.machine_id].add(tool.tooling_id)
            machine_tool_links.append(MachineTooling(machine=machine, tooling=tool))
    MachineTooling.objects.bulk_create(machine_tool_links)

    materials = []
    material_units = ["KG", "L", "M", "EA"]
    for i in range(1, counts["materials"] + 1):
        materials.append(Material(
            material_id=f"MAT-{i:04d}", name=f"{rng.choice(['Alloy', 'Polymer', 'Copper', 'Fastener', 'Resin', 'Circuit'])} stock {i:03d}",
            category=rng.choice(["RAW", "COMPONENT", "PACKAGING"]), unit=material_units[(i - 1) % len(material_units)],
        ))
    Material.objects.bulk_create(materials)
    material_list = list(Material.objects.all())

    # 2) Repeating machine/labor calendars and planned downtime.
    calendar_rows = []
    for machine in Machine.objects.all():
        for weekday in range(5):
            calendar_rows.append(MachineCalendar(
                machine=machine, weekday=weekday, shift_start=time(8, 0), shift_end=time(16, 0),
                capacity_minutes=480, effective_from=date(2026, 1, 1), effective_to=date(2027, 12, 31),
            ))
    MachineCalendar.objects.bulk_create(calendar_rows)
    LaborShift.objects.bulk_create([
        LaborShift(weekday=d, shift_start=time(8), shift_end=time(16), workers_available=36,
                   effective_from=date(2026, 1, 1), effective_to=date(2027, 12, 31))
        for d in range(5)
    ])

    downtime_rows = []
    dt_counter = 1
    for machine in Machine.objects.all():
        for _ in range(2):
            day = BASE_DATE + timedelta(days=rng.randint(0, 70))
            while day.weekday() >= 5:
                day += timedelta(days=1)
            start_hour = rng.choice([9, 11, 13, 14])
            start = _stamp(day, start_hour)
            downtime_rows.append(MachineDowntime(
                downtime_id=f"DOWN-{dt_counter:05d}", machine=machine, start_at=start,
                end_at=start + timedelta(minutes=rng.choice([60, 90, 120])),
                reason=rng.choice(["Preventive maintenance", "Tool calibration", "Unplanned breakdown"]),
                issue_type="PLANNED_DOWNTIME" if dt_counter % 3 else "UNPLANNED_DOWNTIME",
            ))
            dt_counter += 1
    MachineDowntime.objects.bulk_create(downtime_rows)

    # 3) Changeover matrix. START->family is an initial setup; category transitions are explicit.
    setup_families = ["START"] + CATEGORIES
    changeovers = []
    for machine in Machine.objects.all():
        for from_setup in setup_families:
            for to_setup in CATEGORIES:
                minutes = 0 if from_setup in ("START", to_setup) else rng.randint(10, 45)
                changeovers.append(ChangeoverMatrix(
                    changeover_id=f"CO-{machine.machine_id}-{setup_families.index(from_setup):02d}-{CATEGORIES.index(to_setup):02d}",
                    machine=machine, from_setup=from_setup, to_setup=to_setup,
                    changeover_minutes=minutes, effective_from=date(2026, 1, 1), effective_to=date(2027, 12, 31),
                ))
    ChangeoverMatrix.objects.bulk_create(changeovers)

    # 4) Inventory lots: delayed first availability on a controlled subset, stock remains sufficient overall.
    lots = []
    lot_counter = 1
    delayed_materials = set(rng.sample([m.material_id for m in material_list], k=max(8, counts["materials"] // 10)))
    for material in material_list:
        delayed_first = material.material_id in delayed_materials
        for lot_index in range(1, 4):
            available = BASE_DATE + timedelta(days=(rng.randint(10 + (lot_index - 1) * 2, 18 + (lot_index - 1) * 3) if delayed_first else rng.randint(0, 10)))
            lots.append(InventoryLot(
                lot_id=f"LOT-{lot_counter:06d}", material=material, lot_number=f"{material.material_id}-L{lot_index:02d}",
                quantity=Decimal(str(rng.randint(500, 1400))), available_from=available,
                expiry_date=None, quality_status="AVAILABLE",
            ))
            lot_counter += 1
    InventoryLot.objects.bulk_create(lots)

    # 5) Product routing, routing steps, and N:M eligible-machine bridge.
    product_routes = {}
    route_rows = []
    route_by_product = {}
    for product in Product.objects.all():
        route_id = f"ROUTE-{product.sku_code}-01"
        route = Routing(
            routing_id=route_id, product=product, version=1,
            effective_from=date(2026, 1, 1), effective_to=date(2027, 12, 31),
            lifecycle_state="ACTIVE", created_at=_stamp(BASE_DATE - timedelta(days=30)),
        )
        route_rows.append(route)
        route_by_product[product.product_id] = route
    Routing.objects.bulk_create(route_rows)

    routing_steps = []
    step_machine_specs = []
    for product in Product.objects.all():
        route = route_by_product[product.product_id]
        for seq in range(1, counts["operations_per_order"] + 1):
            machine_type = MACHINE_TYPES[(seq + (int(product.product_id[-2:]) % len(MACHINE_TYPES))) % len(MACHINE_TYPES)]
            candidates = machine_by_type[machine_type]
            tooling = rng.choice(tools) if rng.random() < 0.72 else None
            eligible = rng.sample(candidates, k=min(3, len(candidates)))
            if tooling:
                for m in eligible:
                    if tooling.tooling_id not in machine_tools[m.machine_id]:
                        MachineTooling.objects.get_or_create(machine=m, tooling=tooling)
                        machine_tools[m.machine_id].add(tooling.tooling_id)
            step = RoutingStep(
                step_id=f"RSTEP-{product.sku_code}-{seq:02d}", routing=route, sequence_no=seq,
                name=STEP_NAMES[seq - 1], base_minutes_per_unit=rng.randint(2, 4),
                required_workers=rng.choice([1, 1, 2]), tooling=tooling,
                machine_type_required=machine_type, setup_family=product.category,
            )
            routing_steps.append(step)
            step_machine_specs.append((step.step_id, [m.machine_id for m in eligible]))
    RoutingStep.objects.bulk_create(routing_steps)
    step_by_id = {s.step_id: s for s in RoutingStep.objects.select_related("routing")}
    RoutingStepMachine.objects.bulk_create([
        RoutingStepMachine(routing_step=step_by_id[step_id], machine_id=machine_id)
        for step_id, machine_ids in step_machine_specs for machine_id in machine_ids
    ])

    # 6) BOM bridge, N:M between products and required materials.
    bom_rows = []
    for product in Product.objects.all():
        for material in rng.sample(material_list, k=rng.randint(2, 4)):
            bom_rows.append(ProductMaterial(
                product=product, material=material,
                quantity_per_unit=Decimal(str(round(rng.uniform(0.15, 1.8), 4))),
            ))
    ProductMaterial.objects.bulk_create(bom_rows)

    # 7) Orders and order-specific operation instances using valid parent IDs.
    product_list = list(Product.objects.all())
    order_rows = []
    order_specs = []
    for i in range(1, counts["orders"] + 1):
        product = rng.choice(product_list)
        order_date = BASE_DATE - timedelta(days=rng.randint(0, 5))
        quantity = rng.randint(10, 50)
        priority = rng.choices([1, 2, 3, 4, 5], weights=[8, 17, 45, 22, 8], k=1)[0]
        due_date = order_date + timedelta(days=rng.randint(12, 38) if priority > 1 else rng.randint(8, 20))
        order_id = f"PO-2026-{i:06d}"
        order = ProductionOrder(
            order_id=order_id, product=product, routing=route_by_product[product.product_id],
            quantity=quantity, order_date=order_date, due_date=due_date,
            priority=priority, status="RELEASED", created_at=_stamp(order_date),
        )
        order_rows.append(order)
        order_specs.append((order_id, product.product_id, quantity))
    ProductionOrder.objects.bulk_create(order_rows)
    order_by_id = {o.order_id: o for o in ProductionOrder.objects.select_related("product", "routing")}
    operations = []
    operation_eligibility_specs = []
    for order_id, product_id, quantity in order_specs:
        order = order_by_id[order_id]
        steps = sorted([s for s in step_by_id.values() if s.routing_id == order.routing_id], key=lambda x: x.sequence_no)
        for step in steps:
            duration = max(15, step.base_minutes_per_unit * quantity)
            op = Operation(
                operation_id=f"OP-{order_id}-{step.sequence_no:02d}", order=order, routing_step=step,
                sequence_no=step.sequence_no, duration_minutes=duration, required_workers=step.required_workers,
            )
            operations.append(op)
    Operation.objects.bulk_create(operations)
    operation_by_step_order = {(o.order_id, o.routing_step_id): o for o in Operation.objects.select_related("order", "routing_step")}
    for order_id, product_id, quantity in order_specs:
        order = order_by_id[order_id]
        for step in sorted([s for s in step_by_id.values() if s.routing_id == order.routing_id], key=lambda x: x.sequence_no):
            op = operation_by_step_order[(order_id, step.step_id)]
            operation_eligibility_specs.append((op.operation_id, [m_id for s_id, m_ids in step_machine_specs if s_id == step.step_id for m_id in m_ids]))
    op_by_id = {o.operation_id: o for o in Operation.objects.all()}
    OperationEligibleMachine.objects.bulk_create([
        OperationEligibleMachine(operation=op_by_id[op_id], machine_id=machine_id)
        for op_id, machine_ids in operation_eligibility_specs for machine_id in machine_ids
    ])

    # Immutable source event: generated master/transaction snapshot.
    AuditEvent.objects.create(
        event_id=f"EVT-SEED-{seed}-{scale.upper()}", entity_type="DATASET", entity_id=f"{seed}-{scale}",
        event_type="SYNTHETIC_DATASET_GENERATED", actor="data-generator",
        payload={"seed": seed, "scale": scale, "base_date": BASE_DATE.isoformat(), "orders": counts["orders"], "operations": len(operations)},
        occurred_at=_stamp(BASE_DATE),
    )

    return {
        "seed": seed, "scale": scale, "base_date": BASE_DATE.isoformat(),
        "counts": {
            "products": Product.objects.count(), "routings": Routing.objects.count(),
            "routing_steps": RoutingStep.objects.count(), "machines": Machine.objects.count(),
            "machine_calendars": MachineCalendar.objects.count(), "downtimes": MachineDowntime.objects.count(),
            "materials": Material.objects.count(), "inventory_lots": InventoryLot.objects.count(),
            "orders": ProductionOrder.objects.count(), "operations": Operation.objects.count(),
            "operation_eligible_machines": OperationEligibleMachine.objects.count(),
        },
    }
