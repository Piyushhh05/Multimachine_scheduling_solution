"""Independent schedule audit. It recomputes constraints from persisted source rows."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_CEILING

from django.utils import timezone

from scheduler.models import (
    ChangeoverMatrix, LaborShift, MachineCalendar, MachineDowntime,
    MaterialReservation, Operation, ProductMaterial, ScheduleException,
)


def _overlap(a_start, a_end, b_start, b_end):
    return a_start < b_end and b_start < a_end


def _occupied_start(schedule):
    return schedule.start_at - timedelta(minutes=schedule.setup_minutes)


def _issue(operation, issue_type, message, details=None):
    return {"operation": operation, "issue_type": issue_type, "message": message, "details": details or {}}


def validate_run(run, persist=False):
    schedules = list(run.schedules.select_related(
        "operation__order__product", "operation__order__routing", "operation__routing_step__tooling", "machine"
    ).prefetch_related("operation__eligible_machines").order_by("start_at", "machine_id"))
    issues = []
    by_machine = defaultdict(list)
    by_order = defaultdict(list)
    by_tool = defaultdict(list)
    labor_events = defaultdict(list)
    calendar_rows = list(MachineCalendar.objects.all())
    downtime_rows = list(MachineDowntime.objects.all())
    changeovers = defaultdict(list)
    for row in ChangeoverMatrix.objects.order_by("effective_from", "changeover_id"):
        changeovers[(row.machine_id, row.from_setup, row.to_setup)].append(row)
    labor_shifts = {r.weekday: r for r in LaborShift.objects.all()}

    for schedule in schedules:
        op = schedule.operation
        occupied_start = _occupied_start(schedule)
        occupied_end = schedule.end_at
        by_machine[schedule.machine_id].append(schedule)
        by_order[op.order_id].append(schedule)
        if op.routing_step.tooling_id:
            by_tool[op.routing_step.tooling_id].append(schedule)
        labor_events[occupied_start.date()].append((schedule.start_at, schedule.end_at, op.required_workers, op))

        eligible_ids = {machine.machine_id for machine in op.eligible_machines.all()}
        if schedule.machine_id not in eligible_ids:
            issues.append(_issue(op, "NO_ELIGIBLE_MACHINE", f"{op.operation_id} is assigned to non-eligible machine {schedule.machine_id}.", {"machine_id": schedule.machine_id}))

        machine = schedule.machine
        if machine.lifecycle_state != "ACTIVE":
            issues.append(_issue(op, "MACHINE_INACTIVE", f"{schedule.machine_id} is not active for scheduling.", {"machine_id": schedule.machine_id, "lifecycle_state": machine.lifecycle_state}))
        product = op.order.product
        if product.lifecycle_state != "ACTIVE":
            issues.append(_issue(op, "PRODUCT_INACTIVE", f"{product.product_id} is not active for scheduling.", {"product_id": product.product_id, "lifecycle_state": product.lifecycle_state}))
        route = op.order.routing
        if (route.lifecycle_state != "ACTIVE" or route.effective_from > occupied_start.date()
                or (route.effective_to and route.effective_to < schedule.end_at.date())):
            issues.append(_issue(op, "ROUTING_NOT_EFFECTIVE", f"Routing {route.routing_id} is not effective for {op.operation_id}'s scheduled interval.", {"routing_id": route.routing_id, "effective_from": route.effective_from.isoformat(), "effective_to": route.effective_to.isoformat() if route.effective_to else None}))
        if occupied_start.date() < machine.available_from or (machine.available_to and schedule.end_at.date() > machine.available_to):
            issues.append(_issue(op, "CALENDAR_VIOLATION", f"{op.operation_id} falls outside {schedule.machine_id}'s availability dates.", {"available_from": machine.available_from.isoformat(), "available_to": machine.available_to.isoformat() if machine.available_to else None}))

        expected_minutes = int((Decimal(op.duration_minutes) / Decimal(machine.capacity_units_per_hour)).to_integral_value(rounding=ROUND_CEILING))
        actual_minutes = int((schedule.end_at - schedule.start_at).total_seconds() // 60)
        if schedule.processing_minutes != expected_minutes or actual_minutes != schedule.processing_minutes:
            issues.append(_issue(op, "MACHINE_CAPACITY", f"{op.operation_id} processing duration does not reconcile with {schedule.machine_id}'s rated capacity.", {"expected_processing_minutes": expected_minutes, "stored_processing_minutes": schedule.processing_minutes, "actual_elapsed_minutes": actual_minutes, "machine_rate_units_per_hour": str(machine.capacity_units_per_hour)}))

        active_calendar = [c for c in calendar_rows if c.machine_id == schedule.machine_id and c.weekday == occupied_start.weekday()
                           and c.effective_from <= occupied_start.date() and (not c.effective_to or c.effective_to >= occupied_start.date())
                           and occupied_start.time() >= c.shift_start
                           and schedule.end_at <= (timezone.make_aware(datetime.combine(occupied_start.date(), c.shift_start)) + timedelta(minutes=c.capacity_minutes))
                           and schedule.end_at.time() <= c.shift_end]
        if not active_calendar:
            issues.append(_issue(op, "CALENDAR_VIOLATION", f"{op.operation_id} falls outside {schedule.machine_id}'s shift calendar.", {"occupied_start": occupied_start.isoformat(), "end_at": schedule.end_at.isoformat()}))

        for down in downtime_rows:
            if down.machine_id == schedule.machine_id and _overlap(occupied_start, occupied_end, down.start_at, down.end_at):
                issues.append(_issue(op, "DOWNTIME_VIOLATION", f"{op.operation_id} overlaps downtime on {schedule.machine_id}.", {"downtime_id": down.downtime_id}))

    # Machine non-overlap and sequence-dependent setup time.
    for machine_id, rows in by_machine.items():
        rows.sort(key=lambda s: _occupied_start(s))
        previous = None
        for current in rows:
            current_start = _occupied_start(current)
            if previous and current_start < previous.end_at:
                issues.append(_issue(current.operation, "MACHINE_OVERLAP", f"{current.operation_id} overlaps {previous.operation_id} on {machine_id}.", {"conflict_operation_id": previous.operation_id}))
            from_setup = previous.operation.order.product.category if previous else "START"
            to_setup = current.operation.order.product.category
            applicable = [row for row in changeovers.get((machine_id, from_setup, to_setup), [])
                          if row.effective_from <= current_start.date() and (row.effective_to is None or row.effective_to >= current_start.date())]
            expected = max(applicable, key=lambda row: (row.effective_from, row.changeover_id)).changeover_minutes if applicable else None
            if expected is None or current.setup_minutes < expected:
                issues.append(_issue(current.operation, "CHANGEOVER_MISSING", f"{current.operation_id} has {current.setup_minutes} setup minutes; {expected if expected is not None else 'no matrix entry'} required.", {"machine_id": machine_id, "from_setup": from_setup, "to_setup": to_setup, "required_minutes": expected}))
            if previous is None or current.end_at > previous.end_at:
                previous = current

    # Order route precedence.
    operation_map = {op.operation_id: op for op in Operation.objects.filter(order_id__in=by_order.keys()).select_related("order")}
    for order_id, rows in by_order.items():
        rows.sort(key=lambda s: s.operation.sequence_no)
        for previous, current in zip(rows, rows[1:]):
            if current.start_at < previous.end_at:
                issues.append(_issue(current.operation, "PRECEDENCE", f"{current.operation_id} starts before {previous.operation_id} finishes.", {"predecessor_id": previous.operation_id, "predecessor_end": previous.end_at.isoformat()}))

    # Required BOM quantities must be covered by available, released lots at first operation start.
    reservations = defaultdict(list)
    lot_reserved = defaultdict(Decimal)
    for reservation in MaterialReservation.objects.filter(run=run).select_related("lot", "material", "order"):
        reservations[(reservation.order_id, reservation.material_id)].append(reservation)
        lot_reserved[reservation.lot_id] += Decimal(reservation.quantity)
        if reservation.material_id != reservation.lot.material_id:
            issues.append(_issue(None, "MATERIAL_UNAVAILABLE", f"Reservation {reservation.reservation_id} links a material to the wrong inventory lot.", {"reservation_id": reservation.reservation_id, "material_id": reservation.material_id, "lot_material_id": reservation.lot.material_id}))
        if (reservation.lot.available_from > reservation.reserved_at.date()
                or reservation.lot.quality_status != "AVAILABLE"
                or (reservation.lot.expiry_date and reservation.lot.expiry_date < reservation.reserved_at.date())):
            issues.append(_issue(None, "MATERIAL_UNAVAILABLE", f"Reservation {reservation.reservation_id} uses a lot that was not usable at reservation time.", {"reservation_id": reservation.reservation_id, "lot_id": reservation.lot_id, "reserved_at": reservation.reserved_at.isoformat(), "available_from": reservation.lot.available_from.isoformat()}))
    for lot_id, reserved_quantity in lot_reserved.items():
        lot = next((reservation.lot for rows in reservations.values() for reservation in rows if reservation.lot_id == lot_id), None)
        if lot and reserved_quantity > Decimal(lot.quantity):
            issues.append(_issue(None, "MATERIAL_UNAVAILABLE", f"Reservations exceed inventory quantity for lot {lot_id}.", {"lot_id": lot_id, "reserved": str(reserved_quantity), "on_hand": str(lot.quantity)}))
    for order_id, rows in by_order.items():
        order = rows[0].operation.order
        first = min(rows, key=lambda s: s.operation.sequence_no)
        bom = ProductMaterial.objects.filter(product=order.product).select_related("material")
        for req in bom:
            required = Decimal(req.quantity_per_unit) * Decimal(order.quantity)
            lots = reservations.get((order_id, req.material_id), [])
            allocated = sum((Decimal(x.quantity) for x in lots), Decimal("0"))
            if allocated < required:
                issues.append(_issue(first.operation, "MATERIAL_UNAVAILABLE", f"{order_id} has {allocated} of {required} required {req.material.unit} for {req.material_id} reserved.", {"material_id": req.material_id, "required": str(required), "reserved": str(allocated)}))
            for item in lots:
                if (item.lot.quality_status != "AVAILABLE" or item.lot.available_from > first.start_at.date()
                        or (item.lot.expiry_date and item.lot.expiry_date < first.start_at.date())):
                    issues.append(_issue(first.operation, "MATERIAL_UNAVAILABLE", f"{item.lot_id} was not usable when {order_id} started.", {"lot_id": item.lot_id, "available_from": item.lot.available_from.isoformat()}))

    # Labor capacity by day. Setup is a machine occupation; labor is counted during processing.
    for day, events in labor_events.items():
        shift = labor_shifts.get(day.weekday())
        if not shift or day < shift.effective_from or (shift.effective_to and day > shift.effective_to):
            for _, _, _, op in events:
                issues.append(_issue(op, "LABOR_CAPACITY", f"No labor shift is defined for {day.isoformat()}.", {"date": day.isoformat()}))
            continue
        timeline = []
        for start, end, workers, op in events:
            if start.time() < shift.shift_start or end.time() > shift.shift_end:
                issues.append(_issue(op, "LABOR_CAPACITY", f"{op.operation_id} falls outside the defined labor shift on {day.isoformat()}.", {"start": start.isoformat(), "end": end.isoformat(), "shift_start": shift.shift_start.isoformat(), "shift_end": shift.shift_end.isoformat()}))
            timeline.append((start, 1, workers, op))
            timeline.append((end, 0, -workers, op))
        active = 0
        for timestamp, kind, delta, op in sorted(timeline, key=lambda x: (x[0], x[1])):
            active += delta
            if active > shift.workers_available:
                issues.append(_issue(op, "LABOR_CAPACITY", f"Labor demand {active} exceeds {shift.workers_available} workers on {day.isoformat()}.", {"date": day.isoformat(), "demand": active, "available": shift.workers_available}))
                break

    # Shared tooling capacity.
    from scheduler.models import Tooling
    tooling_capacity = {t.tooling_id: t.capacity for t in Tooling.objects.all()}
    for tooling_id, rows in by_tool.items():
        events = []
        for row in rows:
            events.append((row.start_at, 1, row.operation))
            events.append((row.end_at, -1, row.operation))
        active = 0
        for timestamp, delta, op in sorted(events, key=lambda x: (x[0], x[1])):
            active += delta
            if active > tooling_capacity.get(tooling_id, 1):
                issues.append(_issue(op, "TOOLING_CAPACITY", f"Shared tooling {tooling_id} exceeds capacity.", {"tooling_id": tooling_id, "capacity": tooling_capacity.get(tooling_id, 1)}))
                break

    if persist and issues:
        ScheduleException.objects.bulk_create([
            ScheduleException(
                run=run, operation=item["operation"], issue_type=item["issue_type"], severity="ERROR",
                message=item["message"][:300], details=item["details"], detected_at=timezone.now(),
            ) for item in issues
        ], batch_size=500)
    return issues
