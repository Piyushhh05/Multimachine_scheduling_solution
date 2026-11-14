# Problem analysis | PlantPilot APS

## 5W analysis

| Question | Finding |
|---|---|
| **Who** | Production planner sequences orders; plant operator follows the dispatch list; maintenance/quality engineer supplies downtime and quality-release status; OT/IT administrator maintains machine, calendar and integration data. |
| **What** | Jobs must be routed through ordered operations and assigned to eligible machines without violating machine, labor, tooling, shift, material or setup constraints. |
| **Why** | Manual planning across separate spreadsheets and source systems makes shared constraints hard to see. The result can be machine clashes, work released before material is available, skipped setups, blocked routes and late orders. |
| **Where** | At order release, dispatch planning, machine assignment, shift handover, material staging and maintenance rescheduling. |
| **When** | At daily/shift planning and whenever priorities, inventory dates, machine downtime or order dates change. |

## Symptom vs root cause

**Symptoms:** overlapping work, late orders, idle machines, unexpected changeovers, jobs waiting for material, and unexplained schedule edits.

**Underlying bottleneck:** there is no single, constraint-aware view connecting order routing, eligible work centres, calendars, sequence-dependent setup, labor capacity, shared tooling and lot-level inventory dates. A planner can optimize one spreadsheet while invalidating another constraint.

## Stakeholder needs

| Actor | Need from the MVP | Visible outcome |
|---|---|---|
| Production planner | A feasible priority-aware sequence and clear reasons for blocked operations | Run scheduler, Gantt, KPIs, exception list |
| Plant operator | Reliable machine/operation dispatch and setup visibility | Machine, processing window, setup minutes and order reference |
| Maintenance / quality engineer | Downtime and material/quality constraints must be respected | Machine downtime records, lot quality status and exceptions |
| OT/IT administrator | Referential integrity, repeatable seed data and traceable changes | Relational schema, deterministic generator, exports, audit events |

## MoSCoW scope

**Must have:** master/transaction data generation; deterministic IDs and valid relationships; machine/routing eligibility; shift and downtime calendars; due dates and priority; material availability and FIFO reservations; sequence-dependent setup; labor and tooling capacity; schedule run history; independent validator; makespan/lateness/utilization; exception and CSV exports.

**Should have:** Gantt-style timeline, audit log browser, JSON read-only endpoints, Django Admin, held-out anomaly scenarios and reproducibility check.

**Could have:** alternative scheduling policies, schedule comparison, order release workflow, shift-level resource heat map, role-specific screens.

**Won't have now:** ML-based prediction, global mathematical optimization, SAP/ERP/CMMS/SCADA connectors, real-time machine telemetry, multi-plant optimization, named-employee skills rostering and commercial deployment security.

## Success measures

| KPI | Calculation | Why it matters |
|---|---|---|
| Feasible schedule | Unscheduled operations = 0 and validator hard-violations = 0 | Core acceptance condition |
| Makespan | Last completion − first occupied start | Overall plan horizon |
| On-time rate | Completed orders by due date / completed orders | Customer delivery performance |
| Total tardiness | Sum of positive completion-date minus due-date days | Magnitude of lateness |
| Machine utilization | Processing minutes / available calendar minutes | Capacity visibility |
| Setup hours | Total sequence-dependent setup minutes / 60 | Setup burden visibility |
| Material starvation | Orders blocked for insufficient usable lots | Supply readiness |
| Exception coverage | Count by typed constraint | Explains infeasibility rather than hiding it |

No real-world improvement percentage is claimed. Metrics are measured on synthetic records and should be compared against a plant baseline before any ROI claim.

## Why this MVP

A deterministic priority-first, earliest-feasible-finish dispatcher is small enough to implement and explain in a hackathon. Hard constraints are enforced before an assignment is accepted, and a separate validator recomputes the outcome. This gives the planner a usable decision-support workflow without pretending a heuristic is globally optimal or inventing AI predictions without training data.
