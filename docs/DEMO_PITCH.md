# PlantPilot APS | 8-minute demo / presentation outline

## Slide 1 — The production planning problem
Manual planning has to reconcile route sequence, machine eligibility, shifts, downtime, changeovers, labor, tooling and lot availability. A locally sensible assignment can break another constraint.

## Slide 2 — Who feels the pain
Planner: conflicts and late orders. Operator: unclear next job/setup. Maintenance/quality: downtime and material release. OT/IT: inconsistent identifiers and limited traceability.

## Slide 3 — What we built
A focused planning cockpit: deterministic synthetic data, priority-aware dispatch, hard-constraint checks, schedule history, exception explanations and operational KPIs.

## Slide 4 — Architecture
Three tiers: Bootstrap/HTML/vanilla JS and Django templates; Django views, scheduler, validator and generator; SQLite accessed through Django ORM. No extra frontend or database platform.

## Slide 5 — End-to-end demonstration
Inspect order queue → run scheduler → show machine Gantt → open one schedule record's setup and decision reason → export dispatch list.

## Slide 6 — Reliability and negative cases
Show independent exception validation and the held-out examples: machine overlap, route precedence, material before availability, outside-shift assignment and missing setup. Explain that labels are kept separate from valid baseline input.

## Slide 7 — Business outcomes
Display actual run metrics from the dashboard: makespan, on-time rate, lateness, utilization, setup hours, bottleneck and exceptions. Do not claim improvement without comparing to the plant's pre-existing baseline.

## Slide 8 — Deployment path
Pilot with sanitized ERP routings/BOMs/orders, maintenance windows and inventory lots. Validate constraints with planners, integrate order/maintenance feeds, introduce RBAC and secure deployment, then compare schedule KPIs against a measured baseline.

## Suggested spoken demo script

“PlantPilot addresses the hidden coordination problem in manufacturing scheduling. The planner is not simply choosing an empty machine. Every operation belongs to a route, needs compatible equipment, labor and tooling, waits for its materials, and may require a changeover. Our workflow connects those records and makes the scheduling decision explainable.

“I’ll start with the synthetic plant data. The IDs are deterministic and the operation rows point back to valid order and routing records. I’ll run a new schedule. The engine prioritizes urgent orders, tests eligible machines and rejects candidates that conflict with calendars, downtime, material, shared labor, tooling or setup requirements.

“The Gantt view shows when each machine is occupied; setup minutes are recorded separately from processing. The dashboard reports makespan, due-date performance, setup burden and utilization. When no feasible assignment is available, the exception list tells the planner which rule blocked it rather than hiding the problem.

“We also run a second validator against the persisted schedule and keep each run as a separate version with append-only decision events. That means a planner can inspect what was decided and why. The result is a transparent decision-support MVP. Before production deployment, we would connect it to the plant's ERP and maintenance systems, apply user roles and test it against real baseline KPIs.”
