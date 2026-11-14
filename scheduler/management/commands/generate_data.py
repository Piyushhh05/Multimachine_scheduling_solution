from pathlib import Path
import json
from django.core.management.base import BaseCommand, CommandError
from scheduler.services.data_generator import generate_dataset, SEED
from scheduler.services.engine import create_and_run_schedule
from scheduler.services.exporter import export_all

class Command(BaseCommand):
    help = "Generate deterministic synthetic factory data; optionally schedule and export it."

    def add_arguments(self, parser):
        parser.add_argument("--seed", type=int, default=SEED)
        parser.add_argument("--scale", choices=["starter", "full"], default="full")
        parser.add_argument("--schedule", action="store_true", help="Run the scheduler after generating data")
        parser.add_argument("--export", action="store_true", help="Export CSV/JSON/SQL to data/exports")

    def handle(self, *args, **options):
        try:
            summary = generate_dataset(seed=options["seed"], scale=options["scale"])
        except Exception as exc:
            raise CommandError(f"Data generation failed: {exc}") from exc
        self.stdout.write(self.style.SUCCESS("Synthetic data generated"))
        self.stdout.write(json.dumps(summary, indent=2))
        if options["schedule"]:
            run, metrics = create_and_run_schedule(seed=options["seed"])
            self.stdout.write(self.style.SUCCESS(f"Scheduling run completed: {run.run_id}"))
            self.stdout.write(json.dumps(metrics, indent=2))
        if options["export"]:
            target = Path("data/exports")
            manifest = export_all(target)
            self.stdout.write(self.style.SUCCESS(f"Exported {manifest['total_rows']} rows to {target}"))
