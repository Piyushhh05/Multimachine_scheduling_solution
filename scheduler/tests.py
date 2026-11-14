from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from scheduler.models import (
    AuditEvent, ChangeoverMatrix, InventoryLot, LaborShift, Machine, MachineCalendar, Material,
    MaterialReservation, Operation, OperationEligibleMachine, OperationSchedule,
    Product, ProductMaterial, ProductionOrder, Routing, RoutingStep,
    RoutingStepMachine, ScheduleException, SchedulingRun,
)
from scheduler.services.engine import create_and_run_schedule
from scheduler.services.validator import validate_run


class SchedulerWorkflowTests(TestCase):
    def setUp(self):
        self.plan_day = date(2026, 11, 16)  # Monday
        self.plan_start = timezone.make_aware(datetime.combine(self.plan_day, time(8)))
        self.product = Product.objects.create(
            product_id="PROD-2026-000001", sku_code="SKU-0001", name="Test actuator",
            category="MACHINED", unit="EA", created_at=self.plan_start,
        )
        self.route = Routing.objects.create(
            routing_id="ROUTE-SKU-0001-01", product=self.product, version=1,
            effective_from=date(2026, 1, 1), lifecycle_state="ACTIVE", created_at=self.plan_start,
        )
        self.machine = Machine.objects.create(
            machine_id="MACH-001", name="Test mill", machine_type="CELL-A",
            capacity_units_per_hour=Decimal("1.00"), available_from=date(2026, 1, 1),
        )
        MachineCalendar.objects.create(
            machine=self.machine, weekday=0, shift_start=time(8), shift_end=time(16),
            capacity_minutes=480, effective_from=date(2026, 1, 1), effective_to=date(2027, 12, 31),
        )
        LaborShift.objects.create(
            weekday=0, shift_start=time(8), shift_end=time(16), workers_available=10,
            effective_from=date(2026, 1, 1), effective_to=date(2027, 12, 31),
        )
        ChangeoverMatrix.objects.create(
            changeover_id="CO-MACH-001-00-00", machine=self.machine, from_setup="START",
            to_setup="MACHINED", changeover_minutes=0, effective_from=date(2026, 1, 1),
        )
        ChangeoverMatrix.objects.create(
            changeover_id="CO-MACH-001-00-01", machine=self.machine, from_setup="MACHINED",
            to_setup="MACHINED", changeover_minutes=0, effective_from=date(2026, 1, 1),
        )
        self.material = Material.objects.create(material_id="MAT-0001", name="Steel", category="RAW", unit="KG")
        self.lot = InventoryLot.objects.create(
            lot_id="LOT-000001", material=self.material, lot_number="STEEL-01", quantity=Decimal("100"),
            available_from=self.plan_day, quality_status="AVAILABLE",
        )
        ProductMaterial.objects.create(product=self.product, material=self.material, quantity_per_unit=Decimal("1.0000"))
        self.order = ProductionOrder.objects.create(
            order_id="PO-2026-000001", product=self.product, routing=self.route, quantity=5,
            order_date=self.plan_day, due_date=self.plan_day + timedelta(days=10), priority=1,
            status="RELEASED", created_at=self.plan_start,
        )
        self.step1 = RoutingStep.objects.create(
            step_id="RSTEP-SKU-0001-01", routing=self.route, sequence_no=1, name="Cutting",
            base_minutes_per_unit=10, required_workers=1, machine_type_required="CELL-A", setup_family="MACHINED",
        )
        self.step2 = RoutingStep.objects.create(
            step_id="RSTEP-SKU-0001-02", routing=self.route, sequence_no=2, name="Inspection",
            base_minutes_per_unit=5, required_workers=1, machine_type_required="CELL-A", setup_family="MACHINED",
        )
        self.op1 = Operation.objects.create(
            operation_id="OP-PO-2026-000001-01", order=self.order, routing_step=self.step1,
            sequence_no=1, duration_minutes=60, required_workers=1,
        )
        self.op2 = Operation.objects.create(
            operation_id="OP-PO-2026-000001-02", order=self.order, routing_step=self.step2,
            sequence_no=2, duration_minutes=30, required_workers=1,
        )
        OperationEligibleMachine.objects.create(operation=self.op1, machine=self.machine)
        OperationEligibleMachine.objects.create(operation=self.op2, machine=self.machine)
        RoutingStepMachine.objects.create(routing_step=self.step1, machine=self.machine)
        RoutingStepMachine.objects.create(routing_step=self.step2, machine=self.machine)

    def test_end_to_end_schedule_respects_precedence_and_material(self):
        run, metrics = create_and_run_schedule(seed=20261022, planning_start=self.plan_start)
        self.assertEqual(run.status, "COMPLETED")
        self.assertEqual(metrics["scheduled_operations"], 2)
        first = run.schedules.get(operation=self.op1)
        second = run.schedules.get(operation=self.op2)
        self.assertGreaterEqual(second.start_at, first.end_at)
        self.assertEqual(run.exceptions.count(), 0)
        self.assertEqual(run.material_reservations.count(), 1)
        self.assertTrue(metrics["feasible"])
        self.op1.refresh_from_db()
        self.op2.refresh_from_db()
        self.assertEqual(self.op1.status, "SCHEDULED")
        self.assertEqual(self.op2.status, "SCHEDULED")
        self.assertEqual(AuditEvent.objects.filter(entity_type="OPERATION", event_type="OPERATION_STATE_CHANGED").count(), 2)

    def test_missing_eligible_machine_blocks_operation_and_records_state_event(self):
        OperationEligibleMachine.objects.filter(operation=self.op1).delete()
        run, metrics = create_and_run_schedule(seed=20261022, planning_start=self.plan_start)
        self.assertEqual(metrics["scheduled_operations"], 0)
        self.assertFalse(metrics["feasible"])
        self.assertTrue(run.exceptions.filter(issue_type="NO_ELIGIBLE_MACHINE", operation=self.op1).exists())
        self.op1.refresh_from_db()
        self.assertEqual(self.op1.status, "BLOCKED")
        self.assertTrue(AuditEvent.objects.filter(entity_id=self.op1.operation_id, event_type="OPERATION_STATE_CHANGED").exists())

    def test_machine_rating_scales_processing_duration(self):
        self.machine.capacity_units_per_hour = Decimal("0.5")
        self.machine.save(update_fields=["capacity_units_per_hour"])
        run, metrics = create_and_run_schedule(seed=20261022, planning_start=self.plan_start)
        self.assertEqual(metrics["scheduled_operations"], 2)
        first = run.schedules.get(operation=self.op1)
        # 60 baseline minutes divided by a 0.5x-rated machine = 120 minutes.
        self.assertEqual(first.processing_minutes, 120)
        self.assertEqual(int((first.end_at - first.start_at).total_seconds() // 60), 120)
        self.assertNotIn("MACHINE_CAPACITY", {x.issue_type for x in run.exceptions.all()})

    def test_validator_detects_machine_overlap(self):
        run = SchedulingRun.objects.create(
            run_id="RUN-TEST-OVERLAP", strategy="test", seed=1, status="RUNNING",
            planning_start=self.plan_start, created_at=self.plan_start,
        )
        start = self.plan_start + timedelta(hours=1)
        OperationSchedule.objects.create(
            schedule_id="SCH-2026-900001", run=run, operation=self.op1, machine=self.machine,
            start_at=start, end_at=start + timedelta(hours=2), setup_minutes=0,
            processing_minutes=120, created_at=self.plan_start,
        )
        OperationSchedule.objects.create(
            schedule_id="SCH-2026-900002", run=run, operation=self.op2, machine=self.machine,
            start_at=start + timedelta(hours=1), end_at=start + timedelta(hours=3), setup_minutes=0,
            processing_minutes=120, created_at=self.plan_start,
        )
        issue_types = {x["issue_type"] for x in validate_run(run)}
        self.assertIn("MACHINE_OVERLAP", issue_types)
        self.assertIn("PRECEDENCE", issue_types)

    def test_validator_detects_calendar_violation(self):
        run = SchedulingRun.objects.create(
            run_id="RUN-TEST-CALENDAR", strategy="test", seed=1, status="RUNNING",
            planning_start=self.plan_start, created_at=self.plan_start,
        )
        start = timezone.make_aware(datetime.combine(self.plan_day, time(6)))
        OperationSchedule.objects.create(
            schedule_id="SCH-2026-900003", run=run, operation=self.op1, machine=self.machine,
            start_at=start, end_at=start + timedelta(minutes=60), setup_minutes=0,
            processing_minutes=60, created_at=self.plan_start,
        )
        issue_types = {x["issue_type"] for x in validate_run(run)}
        self.assertIn("CALENDAR_VIOLATION", issue_types)

    def test_validator_detects_material_consumed_before_availability(self):
        run = SchedulingRun.objects.create(
            run_id="RUN-TEST-MATERIAL", strategy="test", seed=1, status="RUNNING",
            planning_start=self.plan_start, created_at=self.plan_start,
        )
        self.lot.available_from = self.plan_day + timedelta(days=2)
        self.lot.save(update_fields=["available_from"])
        start = self.plan_start
        OperationSchedule.objects.create(
            schedule_id="SCH-2026-900004", run=run, operation=self.op1, machine=self.machine,
            start_at=start, end_at=start + timedelta(minutes=60), setup_minutes=0,
            processing_minutes=60, created_at=self.plan_start,
        )
        MaterialReservation.objects.create(
            reservation_id="RES-TEST-0001", run=run, order=self.order, material=self.material,
            lot=self.lot, quantity=Decimal("5"), reserved_at=start,
        )
        issue_types = {x["issue_type"] for x in validate_run(run)}
        self.assertIn("MATERIAL_UNAVAILABLE", issue_types)

    def test_validator_detects_missing_changeover(self):
        run = SchedulingRun.objects.create(
            run_id="RUN-TEST-SETUP", strategy="test", seed=1, status="RUNNING",
            planning_start=self.plan_start, created_at=self.plan_start,
        )
        ChangeoverMatrix.objects.create(
            changeover_id="CO-MACH-001-START-FIN", machine=self.machine, from_setup="START",
            to_setup="FINISHED", changeover_minutes=0, effective_from=date(2026, 1, 1),
        )
        ChangeoverMatrix.objects.create(
            changeover_id="CO-MACH-001-01-02", machine=self.machine, from_setup="FINISHED",
            to_setup="MACHINED", changeover_minutes=30, effective_from=date(2026, 1, 1),
        )
        # Earlier job is a different setup family; second task incorrectly has no setup time.
        other_product = Product.objects.create(
            product_id="PROD-2026-000002", sku_code="SKU-0002", name="Test panel",
            category="FINISHED", unit="EA", created_at=self.plan_start,
        )
        other_route = Routing.objects.create(
            routing_id="ROUTE-SKU-0002-01", product=other_product, version=1,
            effective_from=date(2026, 1, 1), lifecycle_state="ACTIVE", created_at=self.plan_start,
        )
        other_order = ProductionOrder.objects.create(
            order_id="PO-2026-000002", product=other_product, routing=other_route, quantity=1,
            order_date=self.plan_day, due_date=self.plan_day + timedelta(days=10), priority=2,
            status="RELEASED", created_at=self.plan_start,
        )
        other_step = RoutingStep.objects.create(
            step_id="RSTEP-SKU-0002-01", routing=other_route, sequence_no=1, name="Assembly",
            base_minutes_per_unit=10, required_workers=1, machine_type_required="CELL-A", setup_family="FINISHED",
        )
        other_op = Operation.objects.create(
            operation_id="OP-PO-2026-000002-01", order=other_order, routing_step=other_step,
            sequence_no=1, duration_minutes=30, required_workers=1,
        )
        OperationEligibleMachine.objects.create(operation=other_op, machine=self.machine)
        start1 = self.plan_start
        OperationSchedule.objects.create(
            schedule_id="SCH-2026-900005", run=run, operation=other_op, machine=self.machine,
            start_at=start1, end_at=start1 + timedelta(minutes=30), setup_minutes=0,
            processing_minutes=30, created_at=self.plan_start,
        )
        start2 = start1 + timedelta(minutes=30)
        OperationSchedule.objects.create(
            schedule_id="SCH-2026-900006", run=run, operation=self.op1, machine=self.machine,
            start_at=start2, end_at=start2 + timedelta(minutes=60), setup_minutes=0,
            processing_minutes=60, created_at=self.plan_start,
        )
        issue_types = {x["issue_type"] for x in validate_run(run)}
        self.assertIn("CHANGEOVER_MISSING", issue_types)
