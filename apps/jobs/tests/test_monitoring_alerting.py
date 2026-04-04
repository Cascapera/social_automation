from __future__ import annotations

import json
import re
from pathlib import Path

from django.test import SimpleTestCase

_METRIC_PATTERN = re.compile(r"([a-zA-Z_:][a-zA-Z0-9_:]*)(?=\{|\[|$)")


def _extract_metric_names(expressions: list[str]) -> set[str]:
    metric_names: set[str] = set()
    for expression in expressions:
        metric_names.update(_METRIC_PATTERN.findall(expression))
    return metric_names


class MonitoringAlertRulesTests(SimpleTestCase):
    def test_alert_rules_define_expected_alerts(self):
        alert_rules_path = (
            Path(__file__).resolve().parents[3]
            / "monitoring"
            / "alert_rules.yml"
        )
        content = alert_rules_path.read_text(encoding="utf-8")

        self.assertEqual(
            set(re.findall(r"^\s*-\s*alert:\s*([A-Za-z0-9_]+)\s*$", content, flags=re.MULTILINE)),
            {
                "ProcessingFailuresHigh",
                "PublishFailuresHigh",
                "PublishLatencyHigh",
                "QueueBacklogHigh",
                "QueueWaitHigh",
                "ReconciliationFailuresHigh",
                "RenderFailuresHigh",
                "RenderLatencyHigh",
                "TranscriptionFailuresHigh",
                "TranscriptionLatencyHigh",
            },
        )

    def test_alert_rules_only_use_real_metrics(self):
        alert_rules_path = (
            Path(__file__).resolve().parents[3]
            / "monitoring"
            / "alert_rules.yml"
        )
        content = alert_rules_path.read_text(encoding="utf-8")

        expressions = []
        for raw_expression in re.findall(r"^\s*expr:\s*(.+)$", content, flags=re.MULTILINE):
            expr = raw_expression.strip()
            if expr[:1] in {"'", '"'} and expr[-1:] == expr[:1]:
                expr = expr[1:-1]
            expressions.append(expr)

        self.assertEqual(
            _extract_metric_names(expressions),
            {
                "publish_attempts_total",
                "publish_duration_ms_bucket",
                "publish_failures_total",
                "publish_reconciliation_failures_total",
                "queue_backlog",
                "queue_wait_ms_bucket",
                "render_duration_ms_bucket",
                "render_failures_total",
                "render_jobs_total",
                "task_failed_total",
                "task_started_total",
                "transcription_duration_ms_bucket",
                "transcription_failures_total",
                "transcription_jobs_total",
            },
        )


class MonitoringSloDashboardTests(SimpleTestCase):
    def test_slo_dashboard_only_uses_real_metrics(self):
        dashboard_path = (
            Path(__file__).resolve().parents[3]
            / "monitoring"
            / "grafana"
            / "dashboards"
            / "dashboard_05_slo.json"
        )
        dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))

        expressions = [
            target["expr"]
            for panel in dashboard["panels"]
            for target in panel.get("targets", [])
            if target.get("expr")
        ]

        self.assertEqual(
            _extract_metric_names(expressions),
            {
                "publish_attempts_total",
                "publish_duration_ms_bucket",
                "publish_failures_total",
                "publish_reconciliation_failures_total",
                "queue_backlog",
                "queue_wait_ms_bucket",
                "render_duration_ms_bucket",
                "render_failures_total",
                "render_jobs_total",
                "task_failed_total",
                "task_started_total",
                "transcription_duration_ms_bucket",
                "transcription_failures_total",
                "transcription_jobs_total",
            },
        )
