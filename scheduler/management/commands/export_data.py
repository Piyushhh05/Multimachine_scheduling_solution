import json
from pathlib import Path
from django.core.management.base import BaseCommand
from scheduler.services.exporter import export_all

class Command(BaseCommand):
    help = "Export all scheduler tables to CSV, JSON and SQLite seed SQL."
    def add_arguments(self, parser):
        parser.add_argument("--output", default="data/exports")
    def handle(self, *args, **options):
        summary = export_all(Path(options["output"]))
        self.stdout.write(self.style.SUCCESS(f"Export complete: {summary['total_rows']} table rows"))
        self.stdout.write(json.dumps(summary["labels"], indent=2))
