"""Regenerate sdk_conformance_vectors.json from the engine itself."""

import json
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.evaluation.conformance import build_fixture, generate

DEFAULT_OUTPUT = Path(settings.BASE_DIR) / "sdk_conformance_vectors.json"


class Command(BaseCommand):
    help = (
        "Generate SDK conformance vectors by running the evaluation engine over "
        "a fixed fixture. The vectors are the SDK contract for local evaluation "
        "(SDK_CONFIG_SPEC.md §6); regenerate whenever engine behaviour changes "
        "and review the diff — it is the record that the contract moved."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--output", default=str(DEFAULT_OUTPUT),
            help=f"Where to write the vectors (default: {DEFAULT_OUTPUT}).",
        )
        parser.add_argument(
            "--check", action="store_true",
            help="Exit non-zero if the file on disk differs, without writing it.",
        )

    def handle(self, *args, **options):
        vectors = build()
        payload = serialize(vectors)
        output = Path(options["output"])

        if options["check"]:
            current = output.read_text() if output.exists() else ""
            if current != payload:
                self.stderr.write(
                    "Conformance vectors are stale. Engine behaviour changed, or "
                    "the fixture did. Run without --check and review the diff."
                )
                raise SystemExit(1)
            self.stdout.write(self.style.SUCCESS("Conformance vectors are current."))
            return

        output.write_text(payload)
        self.stdout.write(
            self.style.SUCCESS(
                f"Wrote {len(vectors['cases'])} cases and "
                f"{len(vectors['config']['flags'])} flags to {output}."
            )
        )


def build() -> dict:
    """Build the fixture, generate vectors, and leave the database untouched.

    The fixture is a whole organization's worth of rows. It exists only to be
    evaluated, so it is rolled back rather than committed — which also means the
    command is safe to run against any database, including production.

    The flag cache is cleared on the way out: the fixture's payloads were
    written under keys naming a project that no longer exists after the
    rollback, and leaving them behind would let a later request read a flag
    config whose rows are gone.
    """
    try:
        with transaction.atomic():
            environment = build_fixture()
            vectors = generate(environment)
            transaction.set_rollback(True)
    finally:
        cache.clear()
    return vectors


def serialize(vectors: dict) -> str:
    """Stable, diffable JSON.

    `sort_keys` and a fixed indent are what make the CI check a review of the
    *contract* rather than a review of dict ordering.
    """
    return json.dumps(vectors, indent=2, sort_keys=True) + "\n"
