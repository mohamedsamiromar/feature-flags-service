"""Find and clear variation references that point at another flag's variation."""

from django.core.management.base import BaseCommand

from apps.flags.services import FlagService


class Command(BaseCommand):
    help = (
        "Report flags (off/fallthrough variation) and rules (serve_variation) "
        "that reference a variation belonging to a different flag — possibly "
        "another tenant's. Only rows written before the write paths checked "
        "ownership can hold one. Dry run by default; --apply clears them "
        "(audited, cache-evicted). Prints ids only, never variation values."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply", action="store_true",
            help="Clear the references instead of only reporting them.",
        )

    def handle(self, *args, **options):
        apply = options["apply"]
        findings = FlagService().clear_foreign_variation_refs(apply=apply)

        if not findings:
            self.stdout.write(self.style.SUCCESS("No foreign variation references."))
            return

        for f in findings:
            self.stdout.write(
                f"{f['entity']} {f['id']} (flag {f['flag_id']}): {f['field']} -> "
                f"variation {f['variation_id']} of flag {f['variation_flag_id']}"
            )

        if apply:
            self.stdout.write(self.style.SUCCESS(f"Cleared {len(findings)} reference(s)."))
        else:
            self.stdout.write(self.style.WARNING(
                f"{len(findings)} reference(s) found. Re-run with --apply to clear them."
            ))
