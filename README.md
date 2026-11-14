# PlantPilot APS | Production Scheduling MVP

PlantPilot is a small, explainable production-planning application for discrete manufacturing. It takes released orders and assigns their operations to eligible machines while respecting routing precedence, machine shift calendars, maintenance downtime, material availability, labor capacity, shared tooling and sequence-dependent changeovers. It keeps each scheduling run as a separate historical version and records decisions and exceptions.

**Technology is intentionally fixed to the requested stack:** Python, Django, SQLite, Django ORM, Django templates, HTML5, CSS3, vanilla JavaScript and Bootstrap 5. There is no React, Node, Flask, FastAPI, PostgreSQL, MongoDB, external optimizer or ML dependency.

> Decision method: a hybrid of explicit business-rule constraints and a deterministic priority-first, earliest-feasible-finish heuristic, followed by an independent validator. This is explainable scheduling decision support, not a trained ML model or a proof of global optimality. No opaque AI prediction is used to override hard factory rules.

## 1. What is included

- A three-tier Django application with dashboard, Gantt-style schedule board, exception register, plant-data browser, CSV exports and Django Admin.
- Relational models for Product, Routing, RoutingStep, Machine, MachineCalendar, MachineDowntime, Material, InventoryLot, Tooling, MachineTooling, ProductMaterial (BOM), LaborShift, ChangeoverMatrix, ProductionOrder, Operation, OperationEligibleMachine, SchedulingRun, OperationSchedule, MaterialReservation, ScheduleException and AuditEvent.
- Deterministic starter/full data generator. Full scale is 100 products, 40 machines, 500 orders, 2,000 order operations and 200 materials.
- A standard-library reference export in `data/reference/` containing the generated CSV/JSON input tables, source manifest and held-out anomaly labels/scenarios. The Django management command can create the same domain dataset in SQLite and export a live database snapshot.
- A scheduler and independent validator, including typed infeasibility explanations.
- Django tests for the end-to-end scheduling flow and intentional failures.
- Data dictionary, architecture and workflow diagrams, validation plan, test catalog and a demo/presentation outline.

## 2. Run it locally from scratch

Use Python 3.10+ (Python 3.11/3.12 recommended) and a network connection for the first Django package installation. Bootstrap 5 is loaded from its public CDN in the browser.

### Windows PowerShell

```powershell
cd production_scheduler
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python manage.py makemigrations scheduler
python manage.py migrate
python manage.py generate_data --seed 20261022 --scale full --schedule --export
python manage.py validate_data
python manage.py runserver
```

Open `http://127.0.0.1:8000/`.

### macOS / Linux

```bash
cd production_scheduler
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python manage.py makemigrations scheduler
python manage.py migrate
python manage.py generate_data --seed 20261022 --scale full --schedule --export
python manage.py validate_data
python manage.py runserver
```

The command sequence generates the full baseline, runs one schedule, validates it and exports CSV/JSON/SQL under `data/exports/`. The bundled `data/reference/` files are inspectable source data; the Django app uses SQLite ORM records created by `generate_data`. The standalone `reference_seed.sqlite3` and `seed.sql` are portable reference exports for inspection and integrity checks, not direct inserts into Django's ORM-managed `db.sqlite3`.

### Quick starter dataset

```bash
python manage.py generate_data --seed 20261022 --scale starter --schedule --export
```

Use `--scale full` for the requested challenge volume. Re-running `generate_data` clears the generated manufacturing dataset and its prior schedule runs, then recreates it from the selected seed/scale. It does not delete Django users or unrelated apps.

### Reproducibility check

```bash
python manage.py verify_reproducibility --seed 20261022 --scale starter
```

This intentionally regenerates the selected dataset twice and compares row counts and a stable logical SHA-256 digest. It leaves the second generated dataset in the database. Run `generate_data --scale full --schedule --export` afterwards when you want to restore the full demo dataset.

### Optional admin login

```bash
python manage.py createsuperuser
```

Open `http://127.0.0.1:8000/admin/` for detailed CRUD access to the registered master and transaction models. The demo dashboard itself is designed for a trusted local hackathon environment; production deployment must add role-based authentication, secrets management, HTTPS, CSRF/origin hardening and a production-grade operational database. Settings can be supplied through `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, and `DJANGO_ALLOWED_HOSTS` environment variables; see `.env.example` (the file is a template and is not auto-loaded).

## 3. User workflow

1. Generate master data and transaction records using `generate_data`.
2. Open the dashboard and review released orders, machines, products and materials.
3. Select **Run scheduler**. A new immutable `SchedulingRun` is created.
4. The engine dispatches priority 1 first, then earliest due date, and tests each eligible machine for the earliest feasible finish.
5. The engine records schedule assignments and FIFO material reservations. Unscheduled steps receive explicit exceptions.
6. An independent validator recomputes machine overlap, route precedence, calendar, downtime, changeover, material, labor and tooling constraints.
7. Review the timeline and exception register. Export schedule and exceptions to CSV.
8. Use the audit event list to trace when source data or schedule decisions were created.

## 4. Scheduling decisions and assumptions

- Priority 1 is most urgent; 5 is least urgent.
- Dispatch order is `(priority ascending, due_date ascending, order_id ascending)`.
- Candidate machine selection is the earliest feasible finish, with machine ID as a deterministic tie-breaker.
- The routing duration is a baseline at a 1.0 machine rate. The selected machine's `capacity_units_per_hour` scales processing minutes (baseline ÷ rate, rounded up); the independent validator reconciles the recorded duration against that machine rating.
- Machine calendars are recurring weekdays, 08:00–16:00, and operations including setup must fit in one shift. Setup is reserved immediately before `start_at`; `start_at` and `end_at` represent processing time.
- Machine downtime blocks both setup and processing.
- A machine handles one job at a time. For traceability, this MVP appends new work to the machine's existing queue rather than inserting it into historical gaps.
- Material availability is checked against good inventory lots; requirements are calculated from product BOM × order quantity. Lots are allocated FIFO by availability date and lot ID.
- Labor is modeled as a shared weekday shift capacity (36 workers in the full synthetic data). Operations reserve their required worker count during processing.
- Tooling has a capacity; each synthetic shared fixture has capacity 1.
- Changeover setup is determined from the previous scheduled product family to the current product family on that machine. Matrix rows are selected by effective date; missing active entries are treated as infeasible.
- The baseline data is valid. The five negative cases are stored separately in `ground_truth` and `anomaly_scenarios`; they are not mixed into baseline schedule inputs or silently corrupting valid transaction rows.
- Operation lifecycle states are updated to `SCHEDULED` or `BLOCKED`; every transition is appended to `AuditEvent`. Previous run assignments and events remain unchanged.
- All generated dates use a fixed planning baseline of 16 November 2026. The Django project timezone is `Asia/Kolkata`.

## 5. Useful commands

```bash
# Validate schema-level and cross-record data expectations
python manage.py validate_data

# Re-run the scheduler without regenerating master data
# (each run is appended; previous runs remain visible)
python manage.py shell -c "from scheduler.services.engine import create_and_run_schedule; print(create_and_run_schedule()[1])"

# Export all ORM tables as CSV/JSON and a SQLite-compatible SQL seed script
python manage.py export_data --output data/exports

# Execute the Django test suite
python manage.py test scheduler -v 2
```

## 6. Application URLs

| URL | Purpose |
|---|---|
| `/` | KPI dashboard and scheduling action |
| `/schedule/` | Run selector, machine Gantt and assignment ledger |
| `/exceptions/` | Filterable issue register with evidence payloads |
| `/data/` | Master/transaction counts, order queue and audit events |
| `/api/metrics/` | Latest run metrics as JSON |
| `/api/orders/` | Read-only order sample as JSON |
| `/exports/schedule.csv` | Schedule download |
| `/exports/exceptions.csv` | Exception download |
| `/admin/` | Django Admin |

## 7. Metrics

- **Makespan:** latest processing end minus earliest occupied start (including initial setup).
- **Lateness / tardiness:** order completion date minus due date; tardiness floors negative lateness to zero.
- **Projected on-time rate:** fully scheduled orders whose planned completion date is on or before the due date divided by fully scheduled orders. This is a plan projection, not actual shipment/OTIF performance; the MVP does not model goods dispatch.
- **Machine utilization:** scheduled processing minutes divided by calendar capacity over the measured planning horizon.
- **Setup hours:** sum of setup minutes for all assignments.
- **Bottleneck machine:** machine with the largest scheduled processing-minute load.
- **Urgent on-time rate:** on-time completion rate among priority 1–2 orders.
- **Feasibility:** true only when no operation is left unscheduled and the independent validator reports zero hard-constraint violations.

Metrics are recomputed from schedule and source records. They are not randomly assigned.

## 8. Validation and limitations

See `docs/VALIDATION.md`, `docs/TEST_PLAN.md`, and `docs/DATA_DICTIONARY.md`.

Known MVP limits:

- The heuristic is deterministic and practical for a hackathon demo, but is not a globally optimal job-shop solver.
- An operation must fit into one shift; splitting work across shifts is not implemented.
- The synthetic labor pool is one shared weekday shift, not named employees or skills-based rostering.
- Inventory reservations are per scheduling run and are not an ERP goods-issue transaction. Publishing/committing a plan and releasing reservations need a production workflow.
- Maintenance is represented by planned/unplanned downtime rows; there is no CMMS/PLC/SCADA integration.
- Authentication/role permissions and production hardening are not implemented on the demo pages. Django Admin provides local administration only.
- No external customer, supplier, employee, personal or confidential plant data is used. All bundled records are synthetic.

## 9. Project layout

```text
production_scheduler/
├── config/                 # Django project settings and entry points
├── scheduler/              # Models, views, admin, services, templates and static files
│   ├── management/commands/ # generate_data, export_data, validate_data, verify_reproducibility
│   ├── services/            # deterministic generator, scheduler, validator and exporter
│   ├── templates/scheduler/ # Bootstrap pages
│   └── static/scheduler/    # CSS and vanilla JS
├── data/reference/          # Full synthetic CSV/JSON reference export + ground truth
├── docs/                    # Architecture, data dictionary, validation, testing, demo outline
├── tools/                   # Standard-library reference data generator
├── requirements.txt
├── .env.example
└── manage.py
```
