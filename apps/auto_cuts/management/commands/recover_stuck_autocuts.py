from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand

from apps.auto_cuts.services.recovery import (
    DEFAULT_RECOVERY_COOLDOWN,
    DEFAULT_STUCK_AFTER,
    recover_stuck_autocut_analyses,
)


class Command(BaseCommand):
    help = "Scan and recover stuck/inconsistent AutoCut analyses from persisted DB state."

    def add_arguments(self, parser):
        parser.add_argument(
            "--analysis-id",
            type=int,
            default=None,
            help="Evaluate only one analysis id, even if it is newer than the stuck threshold.",
        )
        parser.add_argument(
            "--stuck-minutes",
            type=int,
            default=int(DEFAULT_STUCK_AFTER.total_seconds() // 60),
            help="Minimum age in minutes before an analysis is considered stuck.",
        )
        parser.add_argument(
            "--cooldown-minutes",
            type=int,
            default=int(DEFAULT_RECOVERY_COOLDOWN.total_seconds() // 60),
            help="Minimum minutes between automatic recovery attempts for the same analysis.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Maximum number of analyses to scan in one run.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show the recovery actions without mutating DB or dispatching tasks.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Bypass recovery cooldown for the targeted analyses. Use with care.",
        )

    def handle(self, *args, **options):
        stuck_after = timedelta(minutes=max(0, int(options["stuck_minutes"])))
        cooldown = timedelta(minutes=max(0, int(options["cooldown_minutes"])))

        results = recover_stuck_autocut_analyses(
            stuck_after=stuck_after,
            cooldown=cooldown,
            limit=options.get("limit"),
            analysis_id=options.get("analysis_id"),
            dry_run=bool(options.get("dry_run")),
            force=bool(options.get("force")),
        )

        if not results:
            self.stdout.write("No stuck or inconsistent AutoCut analyses found.")
            return

        summary: dict[str, int] = {}
        for result in results:
            summary[result.action] = summary.get(result.action, 0) + 1
            self.stdout.write(
                f"- analysis_id={result.analysis_id} status_before={result.status_before} "
                f"stage={result.stage} action={result.action} reason={result.reason}"
            )

        summary_text = ", ".join(
            f"{action}={count}" for action, count in sorted(summary.items())
        )
        mode_text = "dry-run" if options.get("dry_run") else "applied"
        self.stdout.write(self.style.SUCCESS(f"Recovery {mode_text}: {summary_text}"))
