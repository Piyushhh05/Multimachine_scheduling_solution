# Data dictionary

The Django ORM is the authoritative application schema. The CSV/JSON reference export is a synthetic data interchange representation. Primary IDs are immutable strings unless noted. Dates are ISO `YYYY-MM-DD`; timestamps are ISO 8601 with timezone; time-of-day is `HH:MM:SS`; durations are integer minutes; inventory amounts are decimal quantities in the material's unit.

## Master entities

| Entity / PK | Fields (type, requiredness, units) | Validation / uniqueness | Relationships and cardinality |
|---|---|---|---|
| **Product** `product_id` | `sku_code` string (required); `name` string (required); `category` enum-like string (required); `unit` string (required, default EA); `lifecycle_state` ACTIVE/INACTIVE/RETIRED; `created_at` timestamp | `product_id` follows `PROD-YYYY-######`; `sku_code` unique; name non-empty | Product 1:N Routing; Product 1:N ProductionOrder; Product N:M Material via ProductMaterial |
| **Routing** `routing_id` | `product_id` FK (required); `version` positive integer; `effective_from` date; `effective_to` optional date; `lifecycle_state`; `created_at` | unique `(product, version)`; end date absent or not before start; one route version selected by order | Product N:1 Routing; Routing 1:N RoutingStep and 1:N ProductionOrder |
| **RoutingStep** `step_id` | `routing_id` FK; `sequence_no` positive integer; `name`; `base_minutes_per_unit` positive integer (minutes/unit); `required_workers` positive integer (people); optional `tooling_id` FK; `machine_type_required`; `setup_family` | unique `(routing, sequence_no)`; positive base time and worker count | Routing 1:N RoutingStep; RoutingStep N:M Machine via RoutingStepMachine; optional N:1 Tooling; Operation N:1 RoutingStep |
| **Machine** `machine_id` | `name`; `machine_type`; `capacity_units_per_hour` decimal (units/hour rating; baseline operation minutes are divided by this relative rate and rounded up); `location`; `lifecycle_state`; `available_from` date; optional `available_to` date | capacity > 0; stable `MACH-###` ID | Machine 1:N MachineCalendar, MachineDowntime, ChangeoverMatrix and OperationSchedule; Machine N:M RoutingStep and Operation via bridge tables; Machine N:M Tooling via MachineTooling |
| **MachineCalendar** auto PK | `machine_id` FK; `weekday` integer 0–6 (Mon=0); `shift_start`, `shift_end` time; `capacity_minutes` positive integer (minutes/shift); `effective_from` date; optional `effective_to` date | end > start; weekday 0..6; unique `(machine, weekday, shift_start, effective_from)` | Machine 1:N MachineCalendar. Multiple rows per day support multiple shifts |
| **MachineDowntime** `downtime_id` | `machine_id` FK; `start_at`, `end_at` timestamps; `reason`; `issue_type` PLANNED_DOWNTIME/UNPLANNED_DOWNTIME | end > start; interval must be treated as unavailable | Machine 1:N MachineDowntime |
| **Material** `material_id` | `name`; `category`; `unit` (KG/L/M/EA); `lifecycle_state` | stable `MAT-####` ID; unit required | Material 1:N InventoryLot; Material N:M Product via ProductMaterial; Material N:M orders through derived BOM need and MaterialReservation |
| **InventoryLot** `lot_id` | `material_id` FK; `lot_number`; `quantity` decimal (material unit); `available_from` date; optional `expiry_date`; `quality_status` AVAILABLE/QUARANTINE/REJECTED | unique `(material, lot_number)`; quantity > 0; expiry absent or >= available date; only AVAILABLE stock can be allocated | Material 1:N InventoryLot; lot 1:N MaterialReservation |
| **Tooling** `tooling_id` | `name`; `capacity` positive integer (concurrent uses); `lifecycle_state` | positive capacity; stable `TOOL-###` ID | Tooling N:M Machine via MachineTooling; Tooling 1:N RoutingStep |
| **MachineTooling** auto PK | `machine_id` FK; `tooling_id` FK | unique `(machine, tooling)` | Bridge for Machine N:M Tooling |
| **ChangeoverMatrix** `changeover_id` | `machine_id` FK; `from_setup`, `to_setup` strings; `changeover_minutes` non-negative integer (minutes); effective date range | unique `(machine, from_setup, to_setup, effective_from)`; a missing active pair is infeasible; `START` represents initial setup state | Machine 1:N ChangeoverMatrix |
| **LaborShift** auto PK | `weekday` integer 0–6; `shift_start`, `shift_end` time; `workers_available` positive integer (people); effective date range | unique `(weekday, shift_start, effective_from)`; end > start; weekday 0..6 | Shared repeating labor-capacity calendar consumed by scheduled operations |

## Transaction and bridge entities

| Entity / PK | Fields (type, requiredness, units) | Validation / uniqueness | Relationships and cardinality |
|---|---|---|---|
| **ProductMaterial** (BOM) auto PK | `product_id` FK; `material_id` FK; `quantity_per_unit` decimal (material units per finished unit) | unique `(product, material)`; quantity > 0 | Bridge for Product N:M Material |
| **RoutingStepMachine** auto PK | `routing_step_id` FK; `machine_id` FK | unique `(routing_step, machine)` | Bridge for RoutingStep N:M Machine |
| **ProductionOrder** `order_id` | `product_id`, `routing_id` FKs; `quantity` positive integer (EA); `order_date`, `due_date`; `priority` integer 1–5 (1 highest); `status` DRAFT/RELEASED/ON_HOLD/CANCELLED/COMPLETED; `created_at` | due date >= order date; routing must belong to product; unique `PO-YYYY-######` | Product 1:N Order; Routing 1:N Order; Order 1:N Operation and MaterialReservation |
| **Operation** `operation_id` | `order_id`, `routing_step_id` FKs; `sequence_no`; `duration_minutes` positive integer; `required_workers` positive integer; `status` PENDING/SCHEDULED/BLOCKED | unique `(order, sequence_no)`; `OP-<order-id>-##`; FK step must be in order's route (service/data validation) | Order 1:N Operation; Operation N:1 RoutingStep; Operation N:M Machine via OperationEligibleMachine |
| **OperationEligibleMachine** auto PK | `operation_id` FK; `machine_id` FK | unique `(operation, machine)` | Bridge for Operation N:M Machine |
| **SchedulingRun** `run_id` | `strategy`; `seed` integer; `status` RUNNING/COMPLETED/FAILED; `planning_start`; `created_at`; optional `completed_at`; `metrics` JSON | run ID unique; completed time set when terminal; prior runs retained | SchedulingRun 1:N OperationSchedule, MaterialReservation and ScheduleException |
| **OperationSchedule** `schedule_id` | `run_id`, `operation_id`, `machine_id` FKs; `start_at`, `end_at` timestamps; `setup_minutes` and `processing_minutes`; `decision_reason`; `created_at` | unique `(run, operation)`; end > start; IDs follow `SCH-YYYY-######`; setup occupies `[start_at - setup, start_at)` | Run 1:N schedules; Operation 1:N schedule versions across runs; Machine 1:N schedules |
| **MaterialReservation** `reservation_id` | `run_id`, `order_id`, `material_id`, `lot_id` FKs; `quantity` decimal; `reserved_at` timestamp | unique `(run, order, material, lot)`; quantity > 0; total allocation cannot exceed available stock in the run | Run/Order/Material/Lot each 1:N reservations |
| **ScheduleException** auto PK | `run_id` FK; optional `operation_id` FK; `issue_type` enum; `severity` ERROR/WARNING; `message`; `details` JSON; `detected_at` timestamp | issue type must be one of the declared constraint codes; never silently discard an unscheduled operation | Run 1:N exceptions; Operation optional 1:N exceptions |
| **AuditEvent** `event_id` | `entity_type`, `entity_id`, `event_type`, `actor`, `payload` JSON, `occurred_at` timestamp | unique immutable event ID; append only; payload contains decision evidence | Polymorphic text reference to historical entity type/ID; no destructive foreign key |

## ID conventions

| Object | Pattern | Example |
|---|---|---|
| Product | `PROD-YYYY-######` | `PROD-2026-000001` |
| Routing | `ROUTE-<sku>-##` | `ROUTE-SKU-0001-01` |
| Routing step | `RSTEP-<sku>-##` | `RSTEP-SKU-0001-01` |
| Machine | `MACH-###` | `MACH-001` |
| Material | `MAT-####` | `MAT-0001` |
| Production order | `PO-YYYY-######` | `PO-2026-000001` |
| Operation | `OP-<order-id>-##` | `OP-PO-2026-000001-01` |
| Schedule | `SCH-YYYY-######` | `SCH-2026-000001` |

Counters are zero-padded and IDs do not change after creation. Foreign keys use exact parent IDs. The full generator creates parent/master records first, then bridges, orders, operations and schedule records.

## Lifecycle and effective-date rules

- Product, Machine, Material, Tooling and Routing lifecycle states are ACTIVE, INACTIVE or RETIRED.
- Orders progress conceptually from DRAFT to RELEASED, ON_HOLD, COMPLETED or CANCELLED. The demo only schedules RELEASED orders; state changes should be represented by AuditEvent records.
- Operations are PENDING, SCHEDULED or BLOCKED. Their schedule records are versioned by SchedulingRun and retained historically.
- Machine calendars, routings and changeover matrix rows have effective-from/to boundaries. The scheduler uses rows active on the planned date.
- Inventory lots are usable only when AVAILABLE, available by the proposed start date and not expired.
- Deliberately invalid scenarios exist only in `ground_truth` and `anomaly_scenarios`; the baseline input files remain referentially valid.
