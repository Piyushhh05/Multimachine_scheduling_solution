# Validation and verification report

## Constraint policy

| Constraint | Hard / soft | Enforcement |
|---|---|---|
| Operation's machine is eligible | Hard | OperationEligibleMachine bridge + scheduler candidate filter + validator |
| Operation route precedence | Hard | Operation sequence; next step release time is previous step end; validator rechecks |
| Machine shift and shift capacity | Hard | MachineCalendar weekday/effective dates, shift start/end and capacity minutes |
| Planned/unplanned machine downtime | Hard | MachineDowntime interval overlap check |
| Material availability / quantity / quality | Hard | BOM-derived order demand, AVAILABLE InventoryLot, FIFO in-run reservations |
| Sequence-dependent changeover | Hard | ChangeoverMatrix from/to setup state on selected machine |
| Labor shift capacity | Hard | LaborShift worker pool and concurrent demand sweep |
| Shared tooling capacity | Hard | Tooling capacity and interval-overlap check |
| Order priority and due-date sequence | Soft dispatch objectives | Priority-first, earliest-due ordering; not a feasibility override |
| Earliest finish / shorter makespan | Soft tie-break objective | Select the feasible candidate with earliest completion |

## Data checks provided

`python manage.py validate_data` checks baseline volumes, duplicate product SKUs, order/product/routing consistency, routing-step machine coverage, operation machine coverage, date ordering, positive inventory, required changeover rows and missing order operations. Database constraints protect primary keys, unique business keys, positive durations/quantities and key date rules.

The scheduler also checks that product and routing lifecycle/effective dates are valid at planning start, that machine availability/effective dates contain the assignment, and that each operation's processing duration reconciles to the selected machine's rated throughput. Changeover rows are selected by their effective-date range.

The standard-library reference data generator is independently runnable without Django:

```bash
python tools/generate_reference_data.py --seed 20261022 --scale full --output data/reference
```

It exports parent and transaction tables, bridge tables, source manifest, and separate `ground_truth` / `anomaly_scenarios` files. The full-scale reference counts are summarized in `reports/reference_data_validation.json`.

## Negative scenarios held out from baseline input

| Scenario | Injected fault description | Expected detection |
|---|---|---|
| ANOM-001 | Two jobs are hypothetically assigned overlapping times on one machine | MACHINE_OVERLAP |
| ANOM-002 | A downstream route step is moved before its predecessor | PRECEDENCE |
| ANOM-003 | A delayed material lot is consumed one day before availability | MATERIAL_UNAVAILABLE |
| ANOM-004 | A job is moved to 04:00, outside the 08:00–16:00 work shift | CALENDAR_VIOLATION |
| ANOM-005 | Required sequence-dependent setup is set to zero | CHANGEOVER_MISSING |

These rows are held out from scheduler inputs. The Django tests create controlled bad schedule records in isolated test databases and assert that the independent validator reports the corresponding issue. The data generator does not silently mutate valid baseline data.

## Checks actually executed in the packaging environment

The build environment did not contain Django, and package installation could not reach the package index. Therefore **Django migrations, Django ORM generation, browser interaction and `python manage.py test` were not executed in this environment**. They must be run after installing `requirements.txt` using the commands in the README. No unexecuted Django test is reported as passed.

The following standard-library checks are performed during packaging:

- Python source files parse/compile with `compileall`.
- Full synthetic reference dataset generated twice from the same seed.
- CSV/JSON row counts and SHA-256 checksums compared across the two generations.
- Dataset joins checked: every order references a product and route; every operation references an order and route step; eligible-machine bridge rows reference known operations and machines; product/material BOM rows reference known parents.
- Deterministic ID counts checked for 100 products, 40 machines, 500 orders, 2,000 operations and 200 materials.

The exact outcome is stored in `reports/VALIDATION_SUMMARY.json` and `reports/reproducibility.json`.

## Important interpretation

A heuristic can leave an order partially scheduled when a later routing step has no feasible machine slot. The earlier steps remain visible and the blocked step is assigned an exception. This is preferable to inventing a completion time. The schedule is labeled feasible only when every released operation is scheduled and the independent validator finds zero hard-constraint violations.
