import json
from django.core.management.base import BaseCommand, CommandError
from scheduler.services.data_generator import generate_dataset, SEED
from scheduler.services.exporter import logical_dataset_digest

class Command(BaseCommand):
    help = "Regenerate the same synthetic dataset twice and compare stable row counts and SHA-256 digests."
    def add_arguments(self, parser):
        parser.add_argument("--seed", type=int, default=SEED)
        parser.add_argument("--scale", choices=["starter", "full"], default="starter")
    def handle(self, *args, **options):
        try:
            first = generate_dataset(options["seed"], options["scale"])
            digest_one = logical_dataset_digest()
            second = generate_dataset(options["seed"], options["scale"])
            digest_two = logical_dataset_digest()
        except Exception as exc:
            raise CommandError(f"Reproducibility check failed: {exc}") from exc
        result = {
            "seed": options["seed"], "scale": options["scale"],
            "first_counts": first["counts"], "second_counts": second["counts"],
            "first_sha256": digest_one, "second_sha256": digest_two,
            "row_counts_identical": first["counts"] == second["counts"],
            "checksums_identical": digest_one == digest_two,
            "reproducible": first["counts"] == second["counts"] and digest_one == digest_two,
        }
        self.stdout.write(json.dumps(result, indent=2))
        if not result["reproducible"]:
            raise CommandError("Generated rows or checksums changed between identical seeds.")
        self.stdout.write(self.style.SUCCESS("Reproducibility verified."))
