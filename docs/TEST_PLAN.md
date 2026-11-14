# QA test plan

| ID | Type | Setup / input | Expected result | Automated coverage |
|---|---|---|---|---|
| T01 | Normal | Valid one-order, two-operation route; available material; eligible machine; active calendar | Both steps scheduled in sequence; one lot reservation; zero exceptions; state-transition events appended | `test_end_to_end_schedule_respects_precedence_and_material` |
| T02 | Machine conflict | Two operations overlap on one machine | Validator reports MACHINE_OVERLAP | `test_validator_detects_machine_overlap` |
| T03 | Route dependency | Downstream operation begins before predecessor end | Validator reports PRECEDENCE | `test_validator_detects_machine_overlap` |
| T04 | Shift boundary | Operation starts at 06:00 against 08:00–16:00 calendar | Validator reports CALENDAR_VIOLATION | `test_validator_detects_calendar_violation` |
| T05 | Material date | Reserved lot becomes available two days after operation start | Validator reports MATERIAL_UNAVAILABLE | `test_validator_detects_material_consumed_before_availability` |
| T06 | Changeover | Previous product family changes; setup recorded as zero although matrix requires 30 min | Validator reports CHANGEOVER_MISSING | `test_validator_detects_missing_changeover` |
| T07 | Machine rate | Machine rating is 0.5x versus routing baseline | Processing duration doubles and validator reconciles it | `test_machine_rating_scales_processing_duration` |
| T08 | Eligibility | First operation has no eligible-machine bridge row | Operation remains blocked; NO_ELIGIBLE_MACHINE and state event are recorded | `test_missing_eligible_machine_blocks_operation_and_records_state_event` |
| T09 | Empty data | Run scheduler when no released orders exist | Completed run with zero scheduled operations and zero makespan; no fabricated rows | Manual / extend Django tests |
| T10 | Routing integrity | Operation's RoutingStep belongs to a different routing than its order | `validate_data` reports the cross-parent join mismatch | Management-command validation |
| T11 | Insufficient stock | BOM demand is higher than all good lots combined | Order does not enter dispatch; MATERIAL_UNAVAILABLE exception explains shortage | Manual / extend Django tests |
| T12 | Downtime | Candidate interval intersects MachineDowntime | Candidate pushed after downtime or operation remains unscheduled | Manual / extend Django tests |
| T13 | Labor capacity | Concurrent operation demand exceeds workers_available | Candidate moved to an available interval | Manual / extend Django tests |
| T14 | Shared tooling | Same capacity-1 tool is required by overlapping machine operations | Candidate moved or exception raised | Manual / extend Django tests |
| T15 | Invalid date | Due date precedes order date | Database check rejects insertion | Model/database constraint |
| T16 | Duplicate ID | Two products share product_id or SKU | Primary/unique key rejects insertion | Database constraint |
| T17 | Repeatable data | Generate twice with seed 20261022 and same scale | Identical row counts and digest | `verify_reproducibility` management command and stdlib reference script |

## Manual browser smoke test after startup

1. Visit `/` and confirm dashboard loads.
2. Click **Run scheduler** and confirm a new run ID appears.
3. Visit `/schedule/`, change run/machine filters and inspect Gantt and ledger rows.
4. Visit `/exceptions/`, filter issue types and inspect evidence payloads.
5. Visit `/data/`, check master/transaction counts and audit entries.
6. Download schedule and exception CSVs.
7. Visit `/api/metrics/` and `/api/orders/` and inspect JSON responses.
8. Use Django Admin to inspect masters, orders, schedule versions and audit events.
