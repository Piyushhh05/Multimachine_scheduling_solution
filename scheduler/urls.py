from django.urls import path
from scheduler import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("run/", views.run_schedule, name="run_schedule"),
    path("schedule/", views.schedule_board, name="schedule_board"),
    path("exceptions/", views.exceptions_page, name="exceptions_page"),
    path("data/", views.data_browser, name="data_browser"),
    path("api/metrics/", views.metrics_api, name="metrics_api"),
    path("api/orders/", views.orders_api, name="orders_api"),
    path("exports/schedule.csv", views.export_schedule, name="export_schedule"),
    path("exports/exceptions.csv", views.export_exceptions, name="export_exceptions"),
]
