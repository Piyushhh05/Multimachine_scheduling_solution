import json
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, F
from scheduler.models import (
    ChangeoverMatrix, InventoryLot, Machine, MachineCalendar, MachineDowntime, Material,
    Operation, OperationEligibleMachine, Product, ProductMaterial, ProductionOrder,
    Routing, RoutingStep, RoutingStepMachine,
)

class Command(BaseCommand):
    help = "Run relational and domain integrity checks against the generated baseline."
    def handle(self, *args, **options):
        checks = {
            "product_count": Product.objects.count(),
            "routing_count": Routing.objects.count(),
            "machine_count": Machine.objects.count(),
            "material_count": Material.objects.count(),
            "order_count": ProductionOrder.objects.count(),
            "operation_count": Operation.objects.count(),
            "machine_calendar_rows": MachineCalendar.objects.count(),
            "downtime_rows": MachineDowntime.objects.count(),
            "bom_rows": ProductMaterial.objects.count(),
            "routing_machine_links": RoutingStepMachine.objects.count(),
            "operation_machine_links": OperationEligibleMachine.objects.count(),
        }
        checks["duplicate_product_skus"] = Product.objects.values("sku_code").annotate(n=Count("sku_code")).filter(n__gt=1).count()
        checks["duplicate_order_operation_sequences"] = Operation.objects.values("order_id", "sequence_no").annotate(n=Count("operation_id")).filter(n__gt=1).count()
        checks["orders_with_invalid_routing_product"] = sum(1 for o in ProductionOrder.objects.select_related("product", "routing") if o.product_id != o.routing.product_id)
        checks["steps_without_eligible_machine"] = RoutingStep.objects.annotate(n=Count("machine_links")).filter(n=0).count()
        checks["operations_without_eligible_machine"] = Operation.objects.annotate(n=Count("eligible_machine_links")).filter(n=0).count()
        operation_rows = list(Operation.objects.select_related("order__routing", "routing_step__routing"))
        checks["operations_with_wrong_routing_step"] = sum(1 for o in operation_rows if o.order.routing_id != o.routing_step.routing_id)
        checks["operations_with_sequence_mismatch"] = sum(1 for o in operation_rows if o.sequence_no != o.routing_step.sequence_no)
        checks["operations_with_wrong_duration"] = sum(1 for o in operation_rows if o.duration_minutes != max(15, o.routing_step.base_minutes_per_unit * o.order.quantity))
        checks["invalid_due_dates"] = ProductionOrder.objects.filter(due_date__lt=F("order_date")).count()
        checks["invalid_inventory_quantity"] = InventoryLot.objects.filter(quantity__lte=0).count()
        checks["invalid_machine_capacity"] = Machine.objects.filter(capacity_units_per_hour__lte=0).count()
        checks["invalid_calendar_shift"] = MachineCalendar.objects.filter(shift_end__lte=F("shift_start")).count()
        checks["invalid_calendar_effective_dates"] = MachineCalendar.objects.filter(effective_to__lt=F("effective_from")).count()
        checks["invalid_downtime_intervals"] = MachineDowntime.objects.filter(end_at__lte=F("start_at")).count()
        checks["invalid_changeover_effective_dates"] = ChangeoverMatrix.objects.filter(effective_to__lt=F("effective_from")).count()
        expected_changeovers = Machine.objects.count() * 6 * 5
        checks["missing_changeover_pairs"] = max(0, expected_changeovers - ChangeoverMatrix.objects.count())
        checks["orders_missing_operations"] = ProductionOrder.objects.annotate(n=Count("operations")).filter(n=0).count()
        checks["orders_with_wrong_operation_count"] = sum(1 for o in ProductionOrder.objects.annotate(n=Count("operations")) if o.n != o.routing.steps.count())
        failures = [k for k, v in checks.items() if k not in {
            "product_count", "routing_count", "machine_count", "material_count", "order_count", "operation_count",
            "machine_calendar_rows", "downtime_rows", "bom_rows", "routing_machine_links", "operation_machine_links"
        } and v != 0]
        result = {"checks": checks, "failures": failures, "passed": not failures}
        self.stdout.write(json.dumps(result, indent=2))
        if failures:
            raise CommandError("Data validation checks failed: " + ", ".join(failures))
        self.stdout.write(self.style.SUCCESS("Data validation passed."))
