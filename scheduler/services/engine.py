"""Deterministic priority-first, earliest-feasible-finish production scheduler."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_CEILING

from django.db import transaction
from django.utils import timezone

from scheduler.models import (
    AuditEvent, ChangeoverMatrix, InventoryLot, LaborShift, MachineCalendar,
    MachineDowntime, Material, MaterialReservation, Operation, OperationSchedule,
    ProductionOrder, ScheduleException, SchedulingRun,
)
from scheduler.services.validator import validate_run


def _aware(day: date, at: time) -> datetime:
    return timezone.make_aware(datetime.combine(day, at))


def _overlap(a_start, a_end, b_start, b_end):
    return a_start < b_end and b_start < a_end


def _interval_occupied(task):
    return task["start_at"] - timedelta(minutes=task["setup_minutes"]), task["end_at"]


class SchedulingEngine:
    def __init__(self, run: SchedulingRun):
        self.run = run
        self.machine_tasks = defaultdict(list)
        self.labor_tasks = []
        self.tooling_tasks = defaultdict(list)
        self.machine_calendars = defaultdict(list)
        self.downtime = defaultdict(list)
        self.changeovers = defaultdict(list)
        self.labor_shifts = {}
        self.machine_tooling = defaultdict(set)
        self.inventory = defaultdict(list)
        self.bom = defaultdict(list)
        self.reservation_rows = []
        self.schedule_rows = []
        self.exception_rows = []
        self.inventory_remaining = {}
        self.reservation_counter = 0
        self.next_schedule_number = OperationSchedule.objects.count() + 1
        self._load_reference_data()

    def _load_reference_data(self):
        for row in MachineCalendar.objects.all():
            self.machine_calendars[row.machine_id].append(row)
        for row in MachineDowntime.objects.filter(end_at__gte=self.run.planning_start - timedelta(days=1)):
            self.downtime[row.machine_id].append((row.start_at, row.end_at, row.reason))
        for row in ChangeoverMatrix.objects.order_by("effective_from", "changeover_id"):
            self.changeovers[(row.machine_id, row.from_setup, row.to_setup)].append(row)
        for row in LaborShift.objects.all():
            self.labor_shifts[row.weekday] = row
        active_materials = set(Material.objects.filter(lifecycle_state="ACTIVE").values_list("material_id", flat=True))
        for row in InventoryLot.objects.select_related("material").all().order_by("available_from", "lot_id"):
            if row.quality_status == "AVAILABLE" and row.material_id in active_materials:
                self.inventory[row.material_id].append(row)
                self.inventory_remaining[row.lot_id] = Decimal(row.quantity)
        from scheduler.models import MachineTooling, ProductMaterial
        for row in MachineTooling.objects.all():
            self.machine_tooling[row.machine_id].add(row.tooling_id)
        for row in ProductMaterial.objects.all():
            self.bom[row.product_id].append((row.material_id, Decimal(row.quantity_per_unit)))

    def _material_readiness(self, order):
        requirements = [(material_id, per_unit * Decimal(order.quantity)) for material_id, per_unit in self.bom[order.product_id]]
        ready_at = self.run.planning_start
        allocations = []
        for material_id, required in requirements:
            remaining = required
            latest_available = None
            lots = self.inventory[material_id]
            for lot in lots:
                free_qty = self.inventory_remaining.get(lot.lot_id, Decimal("0"))
                if free_qty <= 0:
                    continue
                take = min(free_qty, remaining)
                allocations.append((lot, take))
                remaining -= take
                if latest_available is None or lot.available_from > latest_available:
                    latest_available = lot.available_from
                if remaining <= 0:
                    break
            if remaining > 0:
                return None, [], f"{material_id}: short by {remaining} {lots[0].material.unit if lots else 'units'}"
            if latest_available:
                ready_at = max(ready_at, _aware(latest_available, time(8, 0)))
        return ready_at, allocations, ""

    def _labor_capacity(self, day: date):
        shift = self.labor_shifts.get(day.weekday())
        if not shift or day < shift.effective_from or (shift.effective_to and day > shift.effective_to):
            return None
        return shift

    def _find_labor_conflict_end(self, start, end, workers, capacity):
        overlapping = [t for t in self.labor_tasks if _overlap(start, end, t["start_at"], t["end_at"])]
        if not overlapping:
            return None
        points = sorted({start, end, *[max(start, t["start_at"]) for t in overlapping], *[min(end, t["end_at"]) for t in overlapping]})
        for left, right in zip(points, points[1:]):
            if right <= left:
                continue
            used = sum(t["workers"] for t in overlapping if t["start_at"] < right and t["end_at"] > left)
            if used + workers > capacity:
                blockers = [t["end_at"] for t in overlapping if t["start_at"] < right and t["end_at"] > left]
                return min(blockers) if blockers else None
        return None

    def _find_tool_conflict_end(self, tooling_id, start, end, capacity):
        overlapping = [t for t in self.tooling_tasks[tooling_id] if _overlap(start, end, t["start_at"], t["end_at"])]
        if len(overlapping) < capacity:
            return None
        return min(t["end_at"] for t in overlapping)

    def _changeover_minutes(self, machine_id, from_setup, to_setup, on_date):
        rows = self.changeovers.get((machine_id, from_setup, to_setup), [])
        active = [row for row in rows if row.effective_from <= on_date and (row.effective_to is None or row.effective_to >= on_date)]
        if not active:
            return None
        return max(active, key=lambda row: (row.effective_from, row.changeover_id)).changeover_minutes

    def _try_machine(self, operation, order, machine, earliest_start):
        # Operation duration is the routing's baseline at a 1.0 machine rate.
        # Scale it by the selected machine's rated throughput and round up to a
        # whole minute so a faster/slower machine changes the feasible slot.
        effective_minutes = int((Decimal(operation.duration_minutes) / Decimal(machine.capacity_units_per_hour)).to_integral_value(rounding=ROUND_CEILING))
        duration = timedelta(minutes=effective_minutes)
        calendars = self.machine_calendars.get(machine.machine_id, [])
        if not calendars:
            return None, "NO_CALENDAR_SLOT"
        required_tool = operation.routing_step.tooling_id
        if machine.lifecycle_state != "ACTIVE":
            return None, "MACHINE_INACTIVE"
        route = order.routing
        if route.lifecycle_state != "ACTIVE":
            return None, "ROUTING_NOT_EFFECTIVE"
        if earliest_start.date() < machine.available_from:
            earliest_start = _aware(machine.available_from, time(8, 0))
        if machine.available_to and earliest_start.date() > machine.available_to:
            return None, "NO_CALENDAR_SLOT"
        if required_tool and required_tool not in self.machine_tooling[machine.machine_id]:
            return None, "TOOLING_CAPACITY"
        # This MVP uses a non-inserting dispatch policy: append to the machine's last job.
        # It keeps sequence-dependent setup state unambiguous and makes every decision auditable.
        if self.machine_tasks[machine.machine_id]:
            machine_tail = max(task["end_at"] for task in self.machine_tasks[machine.machine_id])
            earliest_start = max(earliest_start, machine_tail)

        last_calendar_day = earliest_start.date() + timedelta(days=365)
        day = earliest_start.date()
        route_was_effective = False
        while day <= last_calendar_day:
            if machine.available_to and day > machine.available_to:
                break
            if day < route.effective_from:
                day += timedelta(days=1)
                continue
            if route.effective_to and day > route.effective_to:
                break
            route_was_effective = True
            for cal in calendars:
                if cal.weekday != day.weekday() or day < cal.effective_from or (cal.effective_to and day > cal.effective_to):
                    continue
                shift_start = _aware(day, cal.shift_start)
                declared_shift_end = _aware(day, cal.shift_end)
                shift_end = min(declared_shift_end, shift_start + timedelta(minutes=cal.capacity_minutes))
                cursor = max(shift_start, earliest_start)
                # The operation including setup must fit in a single defined shift.
                while cursor < shift_end:
                    prior = [t for t in self.machine_tasks[machine.machine_id] if t["end_at"] <= cursor]
                    prior_task = max(prior, key=lambda t: t["end_at"]) if prior else None
                    from_setup = prior_task["setup_family"] if prior_task else "START"
                    changeover = self._changeover_minutes(machine.machine_id, from_setup, order.product.category, day)
                    if changeover is None:
                        return None, "CHANGEOVER_MISSING"
                    setup_delta = timedelta(minutes=changeover)
                    processing_start = cursor + setup_delta
                    processing_end = processing_start + duration
                    if processing_end > shift_end:
                        break

                    # Machine occupancy includes setup; a conflicting task pushes the cursor forward.
                    machine_conflicts = []
                    for task in self.machine_tasks[machine.machine_id]:
                        task_start, task_end = _interval_occupied(task)
                        if _overlap(cursor, processing_end, task_start, task_end):
                            machine_conflicts.append(task_end)
                    if machine_conflicts:
                        cursor = max(cursor + timedelta(minutes=1), min(machine_conflicts))
                        continue

                    # Planned downtime blocks both setup and processing.
                    down_conflicts = [d for d in self.downtime[machine.machine_id] if _overlap(cursor, processing_end, d[0], d[1])]
                    if down_conflicts:
                        cursor = max(cursor + timedelta(minutes=1), min(d[1] for d in down_conflicts))
                        continue

                    labor = self._labor_capacity(day)
                    if not labor:
                        break
                    labor_start = _aware(day, labor.shift_start)
                    labor_end = _aware(day, labor.shift_end)
                    if cursor < labor_start:
                        cursor = labor_start
                        continue
                    if processing_end > labor_end:
                        break
                    labor_conflict_end = self._find_labor_conflict_end(processing_start, processing_end, operation.required_workers, labor.workers_available)
                    if labor_conflict_end:
                        cursor = max(cursor + timedelta(minutes=1), labor_conflict_end - setup_delta)
                        continue

                    if required_tool:
                        tool_capacity = operation.routing_step.tooling.capacity
                        tool_conflict_end = self._find_tool_conflict_end(required_tool, processing_start, processing_end, tool_capacity)
                        if tool_conflict_end:
                            cursor = max(cursor + timedelta(minutes=1), tool_conflict_end - setup_delta)
                            continue

                    return {
                        "machine": machine, "start_at": processing_start, "end_at": processing_end,
                        "occupied_start": cursor, "setup_minutes": changeover,
                        "processing_minutes": effective_minutes,
                        "setup_family": order.product.category,
                    }, ""
            day += timedelta(days=1)
        return None, "NO_CALENDAR_SLOT" if route_was_effective else "ROUTING_NOT_EFFECTIVE"

    def _append_exception(self, operation, issue_type, message, details=None):
        self.exception_rows.append(ScheduleException(
            run=self.run, operation=operation, issue_type=issue_type, severity="ERROR",
            message=message[:300], details=details or {}, detected_at=timezone.now(),
        ))

    def _allocate_materials(self, order, allocations, reserved_at):
        for lot, quantity in allocations:
            self.inventory_remaining[lot.lot_id] -= quantity
            self.reservation_counter += 1
            self.reservation_rows.append(MaterialReservation(
                reservation_id=f"RES-{self.run.run_id[-12:]}-{self.reservation_counter:06d}",
                run=self.run, order=order, material=lot.material, lot=lot,
                quantity=quantity, reserved_at=reserved_at,
            ))

    def run_schedule(self):
        orders = list(ProductionOrder.objects.filter(status="RELEASED").select_related("product", "routing").order_by("priority", "due_date", "order_id"))
        operations = list(Operation.objects.filter(order__in=orders).select_related("order__product", "routing_step__tooling").prefetch_related("eligible_machines").order_by("order_id", "sequence_no"))
        by_order = defaultdict(list)
        for operation in operations:
            by_order[operation.order_id].append(operation)

        for order in orders:
            order_ops = by_order[order.order_id]
            if order.product.lifecycle_state != "ACTIVE":
                self._append_exception(order_ops[0] if order_ops else None, "PRODUCT_INACTIVE", f"Order {order.order_id} references a non-active product.", {"product_id": order.product_id})
                continue
            ready_at, allocations, material_error = self._material_readiness(order)
            if ready_at is None:
                self._append_exception(order_ops[0] if order_ops else None, "MATERIAL_UNAVAILABLE", f"Order {order.order_id} cannot be released: {material_error}", {"order_id": order.order_id})
                continue
            predecessor_end = max(self.run.planning_start, ready_at)
            order_failed = False
            material_reserved = False
            for operation in order_ops:
                eligible = list(operation.eligible_machines.all())
                candidates = []
                for machine in eligible:
                    candidate, reason = self._try_machine(operation, order, machine, predecessor_end)
                    if candidate:
                        candidates.append(candidate)
                if not candidates:
                    issue = "NO_ELIGIBLE_MACHINE" if not eligible else "NO_CALENDAR_SLOT"
                    # Distinguish missing tooling / missing changeover when every candidate failed for that reason.
                    if eligible:
                        reasons = [self._try_machine(operation, order, m, predecessor_end)[1] for m in eligible]
                        if reasons and all(x == "TOOLING_CAPACITY" for x in reasons):
                            issue = "TOOLING_CAPACITY"
                        elif reasons and all(x == "CHANGEOVER_MISSING" for x in reasons):
                            issue = "CHANGEOVER_MISSING"
                        elif reasons and all(x == "MACHINE_INACTIVE" for x in reasons):
                            issue = "MACHINE_INACTIVE"
                        elif reasons and all(x == "ROUTING_NOT_EFFECTIVE" for x in reasons):
                            issue = "ROUTING_NOT_EFFECTIVE"
                    self._append_exception(operation, issue, f"No feasible machine slot for {operation.operation_id}.", {"eligible_machine_count": len(eligible), "ready_at": predecessor_end.isoformat()})
                    order_failed = True
                    break
                selected = min(candidates, key=lambda item: (item["end_at"], item["start_at"], item["machine"].machine_id))
                machine = selected["machine"]
                schedule_id = f"SCH-2026-{self.next_schedule_number:06d}"
                self.next_schedule_number += 1
                row = OperationSchedule(
                    schedule_id=schedule_id, run=self.run, operation=operation, machine=machine,
                    start_at=selected["start_at"], end_at=selected["end_at"],
                    setup_minutes=selected["setup_minutes"], processing_minutes=selected["processing_minutes"],
                    decision_reason=(f"Eligible {machine.machine_id}; rate {machine.capacity_units_per_hour}x; earliest feasible finish; setup {selected['setup_minutes']} min; priority {order.priority}"),
                    created_at=timezone.now(),
                )
                self.schedule_rows.append(row)
                task = {
                    "schedule_id": schedule_id, "operation": operation, "order": order,
                    "machine_id": machine.machine_id, "start_at": selected["start_at"],
                    "end_at": selected["end_at"], "setup_minutes": selected["setup_minutes"],
                    "setup_family": order.product.category, "workers": operation.required_workers,
                    "tooling_id": operation.routing_step.tooling_id,
                }
                self.machine_tasks[machine.machine_id].append(task)
                self.labor_tasks.append(task)
                if task["tooling_id"]:
                    self.tooling_tasks[task["tooling_id"]].append(task)
                predecessor_end = selected["end_at"]
                if operation.sequence_no == 1 and not material_reserved:
                    self._allocate_materials(order, allocations, selected["start_at"])
                    material_reserved = True

            if order_failed:
                # Earlier completed steps remain as a visible partial plan; no later step is fabricated.
                continue

        OperationSchedule.objects.bulk_create(self.schedule_rows, batch_size=500)
        MaterialReservation.objects.bulk_create(self.reservation_rows, batch_size=500)
        ScheduleException.objects.bulk_create(self.exception_rows, batch_size=500)

        # Append decision history, never overwrite previous runs.
        audit_rows = [AuditEvent(
            event_id=f"EVT-{self.run.run_id[-14:]}-{i:06d}", entity_type="OPERATION_SCHEDULE",
            entity_id=row.schedule_id, event_type="SCHEDULE_ASSIGNED", actor="scheduler",
            payload={"run_id": self.run.run_id, "operation_id": row.operation_id, "machine_id": row.machine_id,
                     "start_at": row.start_at.isoformat(), "end_at": row.end_at.isoformat(),
                     "setup_minutes": row.setup_minutes, "decision_reason": row.decision_reason},
            occurred_at=timezone.now(),
        ) for i, row in enumerate(self.schedule_rows, start=1)]

        # Update the current operation lifecycle state and append an immutable
        # transition event for every change. Prior schedule versions remain intact.
        scheduled_ids = {row.operation_id for row in self.schedule_rows}
        state_events = []
        state_updates = []
        event_number = len(audit_rows) + 1
        for operation in operations:
            new_status = "SCHEDULED" if operation.operation_id in scheduled_ids else "BLOCKED"
            if operation.status != new_status:
                old_status = operation.status
                operation.status = new_status
                state_updates.append(operation)
                state_events.append(AuditEvent(
                    event_id=f"EVT-{self.run.run_id[-14:]}-{event_number:06d}",
                    entity_type="OPERATION", entity_id=operation.operation_id,
                    event_type="OPERATION_STATE_CHANGED", actor="scheduler",
                    payload={"run_id": self.run.run_id, "from_status": old_status, "to_status": new_status},
                    occurred_at=timezone.now(),
                ))
                event_number += 1
        if state_updates:
            Operation.objects.bulk_update(state_updates, ["status"], batch_size=500)
        if audit_rows or state_events:
            AuditEvent.objects.bulk_create(audit_rows + state_events, batch_size=500)

        issues = validate_run(self.run, persist=True)
        metrics = self._calculate_metrics()
        metrics["validator_exception_count"] = len(issues)
        metrics["total_exceptions"] = len(issues) + len(self.exception_rows)
        metrics["feasible"] = metrics["total_exceptions"] == 0 and metrics["unscheduled_operations"] == 0
        self.run.metrics = metrics
        self.run.status = "COMPLETED"
        self.run.completed_at = timezone.now()
        self.run.save(update_fields=["metrics", "status", "completed_at"])
        AuditEvent.objects.create(
            event_id=f"EVT-{self.run.run_id[-14:]}-DONE", entity_type="SCHEDULING_RUN", entity_id=self.run.run_id,
            event_type="SCHEDULING_RUN_COMPLETED", actor="scheduler",
            payload={"scheduled_operations": metrics["scheduled_operations"], "unscheduled_operations": metrics["unscheduled_operations"], "metrics": metrics},
            occurred_at=self.run.completed_at,
        )
        return metrics

    def _calculate_metrics(self):
        schedules = self.schedule_rows
        if schedules:
            first_start = min(r.start_at - timedelta(minutes=r.setup_minutes) for r in schedules)
            last_end = max(r.end_at for r in schedules)
            makespan_hours = round((last_end - first_start).total_seconds() / 3600, 2)
        else:
            first_start = last_end = None
            makespan_hours = 0.0
        order_completions = {}
        for row in schedules:
            order_id = row.operation.order_id
            order_completions[order_id] = max(order_completions.get(order_id, row.end_at), row.end_at)
        all_orders = list(ProductionOrder.objects.filter(status="RELEASED"))
        by_order_op_count = {o.order_id: len([x for x in schedules if x.operation.order_id == o.order_id]) for o in all_orders}
        expected_ops = {o.order_id: Operation.objects.filter(order=o).count() for o in all_orders}
        completed = [o for o in all_orders if by_order_op_count[o.order_id] == expected_ops[o.order_id] and expected_ops[o.order_id] > 0]
        tardiness_days = []
        on_time = 0
        for order in completed:
            completion = order_completions[order.order_id]
            late_days = (completion.date() - order.due_date).days
            tardiness_days.append(max(0, late_days))
            if late_days <= 0:
                on_time += 1
        machine_minutes = defaultdict(int)
        setup_minutes = 0
        for row in schedules:
            machine_minutes[row.machine_id] += row.processing_minutes
            setup_minutes += row.setup_minutes
        busy = sum(machine_minutes.values())
        total_capacity = 0
        if first_start and last_end:
            from django.db.models import Sum
            d = first_start.date()
            while d <= last_end.date():
                if d.weekday() < 5:
                    active_capacity = MachineCalendar.objects.filter(weekday=d.weekday(), effective_from__lte=d).filter(Q_effective_to(d)).aggregate(total=Sum("capacity_minutes"))["total"] or 0
                    total_capacity += active_capacity
                d += timedelta(days=1)
        utilization = round(100 * busy / total_capacity, 2) if total_capacity else 0.0
        bottleneck = max(machine_minutes, key=machine_minutes.get) if machine_minutes else None
        urgent_completed = [o for o in completed if o.priority <= 2]
        urgent_ontime = sum(1 for o in urgent_completed if (order_completions[o.order_id].date() - o.due_date).days <= 0)
        return {
            "total_orders": len(all_orders), "completed_orders": len(completed),
            "scheduled_operations": len(schedules), "total_operations": Operation.objects.filter(order__status="RELEASED").count(),
            "unscheduled_operations": max(0, Operation.objects.filter(order__status="RELEASED").count() - len(schedules)),
            "makespan_hours": makespan_hours,
            "total_tardiness_days": sum(tardiness_days), "average_tardiness_days": round(sum(tardiness_days) / len(tardiness_days), 2) if tardiness_days else 0,
            "on_time_rate_pct": round(100 * on_time / len(completed), 2) if completed else 0,
            "machine_utilization_pct": utilization, "total_setup_hours": round(setup_minutes / 60, 2),
            "bottleneck_machine": bottleneck,
            "bottleneck_load_hours": round(machine_minutes.get(bottleneck, 0) / 60, 2) if bottleneck else 0,
            "urgent_orders_completed": len(urgent_completed),
            "urgent_on_time_rate_pct": round(100 * urgent_ontime / len(urgent_completed), 2) if urgent_completed else 0,
            "material_starvation_count": sum(1 for x in self.exception_rows if x.issue_type == "MATERIAL_UNAVAILABLE"),
            "planning_start": self.run.planning_start.isoformat(),
            "planning_end": last_end.isoformat() if last_end else None,
        }


def Q_effective_to(day):
    """Return a Q object for calendar rows active on a date."""
    from django.db.models import Q
    return Q(effective_to__isnull=True) | Q(effective_to__gte=day)


@transaction.atomic
def create_and_run_schedule(seed: int = 20261022, planning_start=None):
    now = timezone.now()
    if planning_start is None:
        planning_start = timezone.make_aware(datetime.combine(date(2026, 11, 16), time(8)))
    run = SchedulingRun.objects.create(
        run_id=f"RUN-{now.strftime('%Y%m%d%H%M%S')}-{SchedulingRun.objects.count() + 1:03d}",
        strategy="Priority (1 highest) → earliest feasible finish", seed=seed,
        status="RUNNING", planning_start=planning_start, created_at=now,
    )
    engine = SchedulingEngine(run)
    metrics = engine.run_schedule()
    return run, metrics
