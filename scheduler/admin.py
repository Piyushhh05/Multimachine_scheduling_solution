from django.contrib import admin
from scheduler.models import (
    AuditEvent, ChangeoverMatrix, InventoryLot, LaborShift, Machine, MachineCalendar,
    MachineDowntime, MachineTooling, Material, MaterialReservation, Operation,
    OperationEligibleMachine, OperationSchedule, Product, ProductMaterial,
    ProductionOrder, Routing, RoutingStep, RoutingStepMachine, ScheduleException,
    SchedulingRun, Tooling,
)


class ImmutableRecordAdmin(admin.ModelAdmin):
    """Permit history inspection but prevent editing/deleting recorded outcomes."""
    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("product_id", "sku_code", "name", "category", "lifecycle_state")
    search_fields = ("product_id", "sku_code", "name")
    list_filter = ("category", "lifecycle_state")


@admin.register(ProductionOrder)
class ProductionOrderAdmin(admin.ModelAdmin):
    list_display = ("order_id", "product", "quantity", "priority", "due_date", "status")
    list_filter = ("priority", "status")
    search_fields = ("order_id", "product__sku_code")


@admin.register(OperationSchedule)
class OperationScheduleAdmin(ImmutableRecordAdmin):
    list_display = ("schedule_id", "run", "operation", "machine", "start_at", "end_at", "setup_minutes")
    list_filter = ("machine",)
    search_fields = ("schedule_id", "operation__operation_id", "run__run_id")


@admin.register(SchedulingRun)
class SchedulingRunAdmin(ImmutableRecordAdmin):
    list_display = ("run_id", "strategy", "status", "seed", "created_at", "completed_at")
    list_filter = ("status",)
    search_fields = ("run_id", "strategy")


@admin.register(MaterialReservation)
class MaterialReservationAdmin(ImmutableRecordAdmin):
    list_display = ("reservation_id", "run", "order", "material", "lot", "quantity", "reserved_at")
    search_fields = ("reservation_id", "order__order_id", "lot__lot_id")


@admin.register(ScheduleException)
class ScheduleExceptionAdmin(ImmutableRecordAdmin):
    list_display = ("id", "run", "issue_type", "severity", "operation", "detected_at")
    list_filter = ("issue_type", "severity")
    search_fields = ("message", "operation__operation_id", "run__run_id")


@admin.register(AuditEvent)
class AuditEventAdmin(ImmutableRecordAdmin):
    list_display = ("event_id", "entity_type", "entity_id", "event_type", "actor", "occurred_at")
    list_filter = ("entity_type", "event_type", "actor")
    search_fields = ("event_id", "entity_id", "event_type")


admin.site.register([
    Routing, RoutingStep, RoutingStepMachine, Machine, MachineCalendar, MachineDowntime,
    Material, InventoryLot, ProductMaterial, Tooling, MachineTooling, LaborShift,
    ChangeoverMatrix, Operation, OperationEligibleMachine,
])
