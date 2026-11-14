import csv
from collections import defaultdict

from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from scheduler.models import (
    AuditEvent, InventoryLot, Machine, MachineCalendar, Material, Operation,
    OperationEligibleMachine, OperationSchedule, Product, ProductionOrder, Routing,
    RoutingStep, ScheduleException, SchedulingRun,
)
from scheduler.services.engine import create_and_run_schedule


def dashboard(request):
    latest_run = SchedulingRun.objects.filter(status="COMPLETED").first()
    metrics = latest_run.metrics if latest_run else {}
    recent_exceptions = latest_run.exceptions.select_related("operation", "operation__order").all()[:8] if latest_run else []
    recent_runs = SchedulingRun.objects.all()[:5]
    context = {
        "latest_run": latest_run, "metrics": metrics,
        "products_count": Product.objects.count(),
        "machines_count": Machine.objects.count(), "orders_count": ProductionOrder.objects.count(),
        "operations_count": Operation.objects.count(), "materials_count": Material.objects.count(),
        "exceptions": recent_exceptions, "recent_runs": recent_runs,
        "active_orders": ProductionOrder.objects.filter(status="RELEASED").count(),
        "scheduled_total": OperationSchedule.objects.count(),
    }
    return render(request, "scheduler/dashboard.html", context)


@require_POST
def run_schedule(request):
    try:
        run, metrics = create_and_run_schedule()
        messages.success(request, f"Scheduling run {run.run_id} completed: {metrics['scheduled_operations']:,} operations scheduled, {metrics['unscheduled_operations']:,} not scheduled.")
    except Exception as exc:
        messages.error(request, f"Scheduling failed: {exc}")
    return redirect("dashboard")


def schedule_board(request):
    run_id = request.GET.get("run")
    run = SchedulingRun.objects.filter(pk=run_id).first() if run_id else SchedulingRun.objects.filter(status="COMPLETED").first()
    machine_filter = request.GET.get("machine", "")
    search = request.GET.get("q", "")
    rows = OperationSchedule.objects.none()
    gantt_groups = []
    if run:
        rows = run.schedules.select_related("operation__order__product", "machine").order_by("start_at", "machine_id")
        if machine_filter:
            rows = rows.filter(machine_id=machine_filter)
        if search:
            rows = rows.filter(operation__operation_id__icontains=search) | rows.filter(operation__order__order_id__icontains=search)
        rows = list(rows)
        if rows:
            window_start = min(x.occupied_start for x in rows)
            window_end = max(x.end_at for x in rows)
            span = max((window_end - window_start).total_seconds(), 1)
            by_machine = defaultdict(list)
            for row in rows:
                left = ((row.occupied_start - window_start).total_seconds() / span) * 100
                width = (max(60, (row.end_at - row.occupied_start).total_seconds()) / span) * 100
                row.left_pct = max(0, min(99.4, left))
                row.width_pct = max(0.6, min(100 - row.left_pct, width))
                row.tooltip = f"{row.operation.operation_id}: {row.start_at:%d %b %H:%M}–{row.end_at:%d %b %H:%M}"
                by_machine[row.machine].append(row)
            gantt_groups = [{"machine": machine, "items": items} for machine, items in sorted(by_machine.items(), key=lambda x: x[0].machine_id)]
    context = {
        "run": run, "runs": SchedulingRun.objects.all()[:15], "machines": Machine.objects.all(),
        "selected_machine": machine_filter, "query": search, "gantt_groups": gantt_groups,
        "schedules": rows[:200], "schedule_count": len(rows),
    }
    return render(request, "scheduler/schedule.html", context)


def exceptions_page(request):
    run_id = request.GET.get("run")
    run = SchedulingRun.objects.filter(pk=run_id).first() if run_id else SchedulingRun.objects.filter(status="COMPLETED").first()
    exceptions = run.exceptions.select_related("operation", "operation__order", "operation__order__product").all() if run else ScheduleException.objects.none()
    issue_filter = request.GET.get("issue", "")
    if issue_filter:
        exceptions = exceptions.filter(issue_type=issue_filter)
    return render(request, "scheduler/exceptions.html", {
        "run": run, "runs": SchedulingRun.objects.all()[:15], "exceptions": exceptions[:300],
        "exception_count": exceptions.count(), "issue_filter": issue_filter,
        "issue_types": ScheduleException.ISSUE_TYPES,
    })


def data_browser(request):
    return render(request, "scheduler/data.html", {
        "products": Product.objects.count(), "routings": Routing.objects.count(),
        "routing_steps": RoutingStep.objects.count(), "machines": Machine.objects.count(),
        "machine_calendars": MachineCalendar.objects.count(), "materials": Material.objects.count(),
        "lots": InventoryLot.objects.count(), "orders": ProductionOrder.objects.count(),
        "operations": Operation.objects.count(), "eligible_links": OperationEligibleMachine.objects.count(),
        "orders_sample": ProductionOrder.objects.select_related("product").order_by("priority", "due_date")[:12],
        "machines_sample": Machine.objects.all()[:12],
        "events": AuditEvent.objects.order_by("-occurred_at")[:12],
    })


@require_GET
def metrics_api(request):
    run = SchedulingRun.objects.filter(status="COMPLETED").first()
    return JsonResponse({"run_id": run.run_id if run else None, "metrics": run.metrics if run else {}, "exceptions": run.exceptions.count() if run else 0})


@require_GET
def orders_api(request):
    orders = ProductionOrder.objects.select_related("product").order_by("priority", "due_date")[:100]
    return JsonResponse({"results": [{"order_id": o.order_id, "product": o.product.sku_code, "quantity": o.quantity, "priority": o.priority, "due_date": o.due_date.isoformat(), "status": o.status} for o in orders]})


@require_GET
def export_schedule(request):
    run_id = request.GET.get("run")
    run = SchedulingRun.objects.filter(pk=run_id).first() if run_id else SchedulingRun.objects.filter(status="COMPLETED").first()
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="schedule-{run.run_id if run else "empty"}.csv"'
    writer = csv.writer(response)
    writer.writerow(["schedule_id", "run_id", "operation_id", "order_id", "product_sku", "machine_id", "start_at", "end_at", "setup_minutes", "processing_minutes", "decision_reason"])
    if run:
        for row in run.schedules.select_related("operation__order__product", "machine").order_by("start_at"):
            writer.writerow([row.schedule_id, run.run_id, row.operation_id, row.operation.order_id, row.operation.order.product.sku_code, row.machine_id, row.start_at.isoformat(), row.end_at.isoformat(), row.setup_minutes, row.processing_minutes, row.decision_reason])
    return response


@require_GET
def export_exceptions(request):
    run_id = request.GET.get("run")
    run = SchedulingRun.objects.filter(pk=run_id).first() if run_id else SchedulingRun.objects.filter(status="COMPLETED").first()
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="exceptions-{run.run_id if run else "empty"}.csv"'
    writer = csv.writer(response)
    writer.writerow(["run_id", "issue_type", "severity", "operation_id", "message", "details"])
    if run:
        for item in run.exceptions.select_related("operation").all():
            writer.writerow([run.run_id, item.issue_type, item.severity, item.operation_id or "", item.message, item.details])
    return response
