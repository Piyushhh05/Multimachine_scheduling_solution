from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class Lifecycle(models.TextChoices):
    ACTIVE = "ACTIVE", "Active"
    INACTIVE = "INACTIVE", "Inactive"
    RETIRED = "RETIRED", "Retired"


class Product(models.Model):
    product_id = models.CharField(max_length=32, primary_key=True)
    sku_code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=120)
    category = models.CharField(max_length=32)
    unit = models.CharField(max_length=12, default="EA")
    lifecycle_state = models.CharField(max_length=12, choices=Lifecycle.choices, default=Lifecycle.ACTIVE)
    created_at = models.DateTimeField()

    class Meta:
        ordering = ["product_id"]
        indexes = [models.Index(fields=["category", "lifecycle_state"])]

    def __str__(self):
        return f"{self.sku_code} · {self.name}"


class Routing(models.Model):
    routing_id = models.CharField(max_length=48, primary_key=True)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="routings")
    version = models.PositiveSmallIntegerField(default=1)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    lifecycle_state = models.CharField(max_length=12, choices=Lifecycle.choices, default=Lifecycle.ACTIVE)
    created_at = models.DateTimeField()

    class Meta:
        ordering = ["product_id", "version"]
        constraints = [
            models.UniqueConstraint(fields=["product", "version"], name="uq_product_routing_version"),
            models.CheckConstraint(condition=Q(effective_to__isnull=True) | Q(effective_to__gte=models.F("effective_from")), name="routing_effective_dates_valid"),
        ]

    def clean(self):
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValidationError("Routing effective_to must not precede effective_from.")


class Machine(models.Model):
    machine_id = models.CharField(max_length=16, primary_key=True)
    name = models.CharField(max_length=80)
    machine_type = models.CharField(max_length=24)
    capacity_units_per_hour = models.DecimalField(max_digits=8, decimal_places=2, default=1)
    location = models.CharField(max_length=60, default="Plant 1")
    lifecycle_state = models.CharField(max_length=12, choices=Lifecycle.choices, default=Lifecycle.ACTIVE)
    available_from = models.DateField()
    available_to = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["machine_id"]
        constraints = [
            models.CheckConstraint(condition=Q(capacity_units_per_hour__gt=0), name="machine_capacity_positive"),
            models.CheckConstraint(condition=Q(available_to__isnull=True) | Q(available_to__gte=models.F("available_from")), name="machine_availability_dates_valid"),
        ]

    def __str__(self):
        return f"{self.machine_id} · {self.name}"


class MachineCalendar(models.Model):
    machine = models.ForeignKey(Machine, on_delete=models.CASCADE, related_name="calendars")
    weekday = models.PositiveSmallIntegerField(help_text="Monday=0, Sunday=6")
    shift_start = models.TimeField()
    shift_end = models.TimeField()
    capacity_minutes = models.PositiveSmallIntegerField(default=480)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["machine_id", "weekday", "shift_start"]
        constraints = [
            models.UniqueConstraint(fields=["machine", "weekday", "shift_start", "effective_from"], name="uq_machine_calendar_shift"),
            models.CheckConstraint(condition=Q(weekday__gte=0) & Q(weekday__lte=6), name="machine_calendar_weekday_range"),
            models.CheckConstraint(condition=Q(shift_end__gt=models.F("shift_start")), name="machine_calendar_shift_valid"),
            models.CheckConstraint(condition=Q(capacity_minutes__gt=0), name="machine_calendar_capacity_positive"),
            models.CheckConstraint(condition=Q(effective_to__isnull=True) | Q(effective_to__gte=models.F("effective_from")), name="machine_calendar_dates_valid"),
        ]

    def clean(self):
        if self.shift_end <= self.shift_start:
            raise ValidationError("Machine shift_end must be after shift_start.")
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValidationError("Machine calendar effective_to must not precede effective_from.")


class MachineDowntime(models.Model):
    downtime_id = models.CharField(max_length=32, primary_key=True)
    machine = models.ForeignKey(Machine, on_delete=models.CASCADE, related_name="downtimes")
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    reason = models.CharField(max_length=120)
    issue_type = models.CharField(max_length=24, default="PLANNED_DOWNTIME")

    class Meta:
        ordering = ["start_at"]
        constraints = [models.CheckConstraint(condition=Q(end_at__gt=models.F("start_at")), name="downtime_end_after_start")]

    def clean(self):
        if self.end_at <= self.start_at:
            raise ValidationError("Downtime end must be after its start.")


class Material(models.Model):
    material_id = models.CharField(max_length=24, primary_key=True)
    name = models.CharField(max_length=120)
    category = models.CharField(max_length=32)
    unit = models.CharField(max_length=12, default="KG")
    lifecycle_state = models.CharField(max_length=12, choices=Lifecycle.choices, default=Lifecycle.ACTIVE)

    class Meta:
        ordering = ["material_id"]

    def __str__(self):
        return f"{self.material_id} · {self.name}"


class InventoryLot(models.Model):
    lot_id = models.CharField(max_length=32, primary_key=True)
    material = models.ForeignKey(Material, on_delete=models.PROTECT, related_name="lots")
    lot_number = models.CharField(max_length=40)
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    available_from = models.DateField()
    expiry_date = models.DateField(null=True, blank=True)
    quality_status = models.CharField(max_length=16, choices=[("AVAILABLE", "Available"), ("QUARANTINE", "Quarantine"), ("REJECTED", "Rejected")], default="AVAILABLE")

    class Meta:
        ordering = ["material_id", "available_from", "lot_id"]
        constraints = [
            models.UniqueConstraint(fields=["material", "lot_number"], name="uq_material_lot_number"),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="inventory_lot_quantity_positive"),
            models.CheckConstraint(condition=Q(expiry_date__isnull=True) | Q(expiry_date__gte=models.F("available_from")), name="inventory_lot_dates_valid"),
        ]


class Tooling(models.Model):
    tooling_id = models.CharField(max_length=16, primary_key=True)
    name = models.CharField(max_length=80)
    capacity = models.PositiveSmallIntegerField(default=1)
    lifecycle_state = models.CharField(max_length=12, choices=Lifecycle.choices, default=Lifecycle.ACTIVE)

    class Meta:
        ordering = ["tooling_id"]
        constraints = [models.CheckConstraint(condition=Q(capacity__gt=0), name="tooling_capacity_positive")]


class MachineTooling(models.Model):
    machine = models.ForeignKey(Machine, on_delete=models.CASCADE, related_name="tooling_links")
    tooling = models.ForeignKey(Tooling, on_delete=models.CASCADE, related_name="machine_links")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["machine", "tooling"], name="uq_machine_tooling")]


class RoutingStep(models.Model):
    step_id = models.CharField(max_length=48, primary_key=True)
    routing = models.ForeignKey(Routing, on_delete=models.CASCADE, related_name="steps")
    sequence_no = models.PositiveSmallIntegerField()
    name = models.CharField(max_length=80)
    base_minutes_per_unit = models.PositiveSmallIntegerField()
    required_workers = models.PositiveSmallIntegerField(default=1)
    tooling = models.ForeignKey(Tooling, on_delete=models.PROTECT, null=True, blank=True, related_name="routing_steps")
    machine_type_required = models.CharField(max_length=24)
    setup_family = models.CharField(max_length=32)

    class Meta:
        ordering = ["routing_id", "sequence_no"]
        constraints = [
            models.UniqueConstraint(fields=["routing", "sequence_no"], name="uq_routing_step_sequence"),
            models.CheckConstraint(condition=Q(base_minutes_per_unit__gt=0), name="routing_step_duration_positive"),
            models.CheckConstraint(condition=Q(required_workers__gte=1), name="routing_step_workers_positive"),
        ]


class RoutingStepMachine(models.Model):
    routing_step = models.ForeignKey(RoutingStep, on_delete=models.CASCADE, related_name="machine_links")
    machine = models.ForeignKey(Machine, on_delete=models.PROTECT, related_name="routing_step_links")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["routing_step", "machine"], name="uq_step_eligible_machine")]


class ProductMaterial(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="material_requirements")
    material = models.ForeignKey(Material, on_delete=models.PROTECT, related_name="product_requirements")
    quantity_per_unit = models.DecimalField(max_digits=12, decimal_places=4)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["product", "material"], name="uq_product_material_bom"),
            models.CheckConstraint(condition=Q(quantity_per_unit__gt=0), name="bom_quantity_positive"),
        ]


class LaborShift(models.Model):
    weekday = models.PositiveSmallIntegerField()
    shift_start = models.TimeField()
    shift_end = models.TimeField()
    workers_available = models.PositiveSmallIntegerField(default=32)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["weekday", "shift_start"]
        constraints = [
            models.UniqueConstraint(fields=["weekday", "shift_start", "effective_from"], name="uq_labor_shift"),
            models.CheckConstraint(condition=Q(weekday__gte=0) & Q(weekday__lte=6), name="labor_weekday_range"),
            models.CheckConstraint(condition=Q(workers_available__gt=0), name="labor_workers_positive"),
            models.CheckConstraint(condition=Q(shift_end__gt=models.F("shift_start")), name="labor_shift_time_valid"),
            models.CheckConstraint(condition=Q(effective_to__isnull=True) | Q(effective_to__gte=models.F("effective_from")), name="labor_shift_dates_valid"),
        ]

    def clean(self):
        if self.shift_end <= self.shift_start:
            raise ValidationError("Labor shift end must be after start.")


class ChangeoverMatrix(models.Model):
    changeover_id = models.CharField(max_length=40, primary_key=True)
    machine = models.ForeignKey(Machine, on_delete=models.CASCADE, related_name="changeovers")
    from_setup = models.CharField(max_length=32)
    to_setup = models.CharField(max_length=32)
    changeover_minutes = models.PositiveSmallIntegerField(default=0)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["machine", "from_setup", "to_setup", "effective_from"], name="uq_machine_changeover_pair"),
            models.CheckConstraint(condition=Q(effective_to__isnull=True) | Q(effective_to__gte=models.F("effective_from")), name="changeover_effective_dates_valid"),
        ]


class ProductionOrder(models.Model):
    PRIORITY_CHOICES = [(1, "Critical"), (2, "High"), (3, "Normal"), (4, "Low"), (5, "Deferred")]
    STATUS_CHOICES = [("DRAFT", "Draft"), ("RELEASED", "Released"), ("ON_HOLD", "On hold"), ("CANCELLED", "Cancelled"), ("COMPLETED", "Completed")]
    order_id = models.CharField(max_length=32, primary_key=True)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="orders")
    routing = models.ForeignKey(Routing, on_delete=models.PROTECT, related_name="orders")
    quantity = models.PositiveIntegerField()
    order_date = models.DateField()
    due_date = models.DateField()
    priority = models.PositiveSmallIntegerField(choices=PRIORITY_CHOICES, default=3)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="RELEASED")
    created_at = models.DateTimeField()

    class Meta:
        ordering = ["priority", "due_date", "order_id"]
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0), name="production_order_quantity_positive"),
            models.CheckConstraint(condition=Q(due_date__gte=models.F("order_date")), name="production_order_due_after_order"),
            models.CheckConstraint(condition=Q(priority__gte=1) & Q(priority__lte=5), name="production_order_priority_range"),
        ]
        indexes = [models.Index(fields=["status", "priority", "due_date"])]

    def clean(self):
        if self.routing_id and self.product_id and self.routing.product_id != self.product_id:
            raise ValidationError("Selected routing must belong to the selected product.")


class Operation(models.Model):
    STATUS_CHOICES = [("PENDING", "Pending"), ("SCHEDULED", "Scheduled"), ("BLOCKED", "Blocked")]
    operation_id = models.CharField(max_length=64, primary_key=True)
    order = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name="operations")
    routing_step = models.ForeignKey(RoutingStep, on_delete=models.PROTECT, related_name="operations")
    sequence_no = models.PositiveSmallIntegerField()
    duration_minutes = models.PositiveIntegerField()
    required_workers = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="PENDING")
    eligible_machines = models.ManyToManyField(Machine, through="OperationEligibleMachine", related_name="eligible_operations")

    class Meta:
        ordering = ["order_id", "sequence_no"]
        constraints = [
            models.UniqueConstraint(fields=["order", "sequence_no"], name="uq_order_operation_sequence"),
            models.CheckConstraint(condition=Q(duration_minutes__gt=0), name="operation_duration_positive"),
            models.CheckConstraint(condition=Q(required_workers__gte=1), name="operation_workers_positive"),
        ]
        indexes = [models.Index(fields=["status", "sequence_no"])]


class OperationEligibleMachine(models.Model):
    operation = models.ForeignKey(Operation, on_delete=models.CASCADE, related_name="eligible_machine_links")
    machine = models.ForeignKey(Machine, on_delete=models.PROTECT, related_name="operation_eligibility_links")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["operation", "machine"], name="uq_operation_eligible_machine")]


class SchedulingRun(models.Model):
    STATUS_CHOICES = [("RUNNING", "Running"), ("COMPLETED", "Completed"), ("FAILED", "Failed")]
    run_id = models.CharField(max_length=40, primary_key=True)
    strategy = models.CharField(max_length=80, default="Priority → earliest feasible finish")
    seed = models.PositiveBigIntegerField(default=20261022)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="RUNNING")
    planning_start = models.DateTimeField()
    created_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True, blank=True)
    metrics = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]


class OperationSchedule(models.Model):
    schedule_id = models.CharField(max_length=32, primary_key=True)
    run = models.ForeignKey(SchedulingRun, on_delete=models.CASCADE, related_name="schedules")
    operation = models.ForeignKey(Operation, on_delete=models.PROTECT, related_name="schedule_records")
    machine = models.ForeignKey(Machine, on_delete=models.PROTECT, related_name="schedule_records")
    start_at = models.DateTimeField(help_text="Processing start; setup occupies the interval immediately before this time")
    end_at = models.DateTimeField()
    setup_minutes = models.PositiveSmallIntegerField(default=0)
    processing_minutes = models.PositiveIntegerField()
    decision_reason = models.CharField(max_length=240, blank=True)
    created_at = models.DateTimeField()

    class Meta:
        ordering = ["start_at", "machine_id"]
        constraints = [
            models.UniqueConstraint(fields=["run", "operation"], name="uq_run_operation_schedule"),
            models.CheckConstraint(condition=Q(end_at__gt=models.F("start_at")), name="schedule_end_after_start"),
            models.CheckConstraint(condition=Q(processing_minutes__gt=0), name="schedule_processing_positive"),
        ]
        indexes = [models.Index(fields=["run", "machine", "start_at"])]

    @property
    def occupied_start(self):
        from datetime import timedelta
        return self.start_at - timedelta(minutes=self.setup_minutes)


class MaterialReservation(models.Model):
    reservation_id = models.CharField(max_length=40, primary_key=True)
    run = models.ForeignKey(SchedulingRun, on_delete=models.CASCADE, related_name="material_reservations")
    order = models.ForeignKey(ProductionOrder, on_delete=models.PROTECT, related_name="material_reservations")
    material = models.ForeignKey(Material, on_delete=models.PROTECT, related_name="reservations")
    lot = models.ForeignKey(InventoryLot, on_delete=models.PROTECT, related_name="reservations")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    reserved_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["run", "order", "material", "lot"], name="uq_run_order_material_lot"),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="reservation_quantity_positive"),
        ]


class ScheduleException(models.Model):
    ISSUE_TYPES = [
        ("MACHINE_OVERLAP", "Machine overlap"),
        ("PRECEDENCE", "Precedence violation"),
        ("MATERIAL_UNAVAILABLE", "Material unavailable"),
        ("CALENDAR_VIOLATION", "Outside machine shift"),
        ("DOWNTIME_VIOLATION", "Machine downtime"),
        ("CHANGEOVER_MISSING", "Changeover missing"),
        ("NO_ELIGIBLE_MACHINE", "No eligible machine"),
        ("NO_CALENDAR_SLOT", "No calendar slot"),
        ("LABOR_CAPACITY", "Labor capacity"),
        ("TOOLING_CAPACITY", "Tooling capacity"),
        ("MACHINE_CAPACITY", "Machine rated capacity"),
        ("MACHINE_INACTIVE", "Machine is inactive"),
        ("PRODUCT_INACTIVE", "Product is inactive"),
        ("ROUTING_NOT_EFFECTIVE", "Routing is not effective"),
    ]
    run = models.ForeignKey(SchedulingRun, on_delete=models.CASCADE, related_name="exceptions")
    operation = models.ForeignKey(Operation, on_delete=models.PROTECT, null=True, blank=True, related_name="exceptions")
    issue_type = models.CharField(max_length=32, choices=ISSUE_TYPES)
    severity = models.CharField(max_length=12, choices=[("ERROR", "Error"), ("WARNING", "Warning")], default="ERROR")
    message = models.CharField(max_length=300)
    details = models.JSONField(default=dict, blank=True)
    detected_at = models.DateTimeField()

    class Meta:
        ordering = ["issue_type", "operation_id"]
        indexes = [models.Index(fields=["run", "issue_type"])]


class AuditEvent(models.Model):
    event_id = models.CharField(max_length=48, primary_key=True)
    entity_type = models.CharField(max_length=40)
    entity_id = models.CharField(max_length=64)
    event_type = models.CharField(max_length=60)
    actor = models.CharField(max_length=80, default="system")
    payload = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField()

    class Meta:
        ordering = ["occurred_at", "event_id"]
        indexes = [models.Index(fields=["entity_type", "entity_id"])]
