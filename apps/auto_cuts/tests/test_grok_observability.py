from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.auto_cuts.services.grok import (
    GROK_OPERATION_ANALYZE_CHUNKS,
    _calculate_grok_cost_usd,
    _extract_grok_usage,
    _observe_grok_request_metrics,
)
from apps.common.metrics import (
    grok_cost_usd_total,
    grok_request_duration_ms,
    grok_requests_total,
    grok_tokens_total,
)


class _DummyMetricChild:
    def __init__(self) -> None:
        self.incs: list[float] = []
        self.observations: list[float] = []

    def inc(self, amount: float = 1) -> None:
        self.incs.append(amount)

    def observe(self, value: float) -> None:
        self.observations.append(value)


class _DummyMetric:
    def __init__(self) -> None:
        self.children: dict[tuple[tuple[str, str], ...], _DummyMetricChild] = {}

    def labels(self, **labels):
        key = tuple(sorted((str(k), str(v)) for k, v in labels.items()))
        child = self.children.get(key)
        if child is None:
            child = _DummyMetricChild()
            self.children[key] = child
        return child


class GrokCostObservabilityTests(SimpleTestCase):
    def _labels_key(self, **labels):
        return tuple(sorted((str(k), str(v)) for k, v in labels.items()))

    def test_calculate_grok_cost_usd_uses_cached_input_discount(self):
        self.assertAlmostEqual(
            _calculate_grok_cost_usd(
                model="grok-4-1-fast-reasoning",
                usage={
                    "input_tokens": 1500,
                    "output_tokens": 500,
                    "cached_input_tokens": 500,
                },
            ),
            0.000475,
        )

    def test_extract_grok_usage_reads_chat_completion_usage(self):
        response = SimpleNamespace(
            usage=SimpleNamespace(
                prompt_tokens=1200,
                completion_tokens=300,
                total_tokens=1500,
                prompt_tokens_details=SimpleNamespace(cached_tokens=200),
                completion_tokens_details=SimpleNamespace(reasoning_tokens=50),
            )
        )

        self.assertEqual(
            _extract_grok_usage(response),
            {
                "input_tokens": 1200,
                "output_tokens": 300,
                "cached_input_tokens": 200,
                "reasoning_tokens": 50,
            },
        )

    def test_observe_grok_request_metrics_records_tokens_cost_and_duration(self):
        requests = _DummyMetric()
        tokens = _DummyMetric()
        cost = _DummyMetric()
        duration = _DummyMetric()

        with patch.multiple(
            "apps.auto_cuts.services.grok",
            grok_requests_total=requests,
            grok_tokens_total=tokens,
            grok_cost_usd_total=cost,
            grok_request_duration_ms=duration,
        ):
            _observe_grok_request_metrics(
                model="grok-4-1-fast",
                operation=GROK_OPERATION_ANALYZE_CHUNKS,
                duration_ms=1234.5,
                usage={
                    "input_tokens": 1500,
                    "output_tokens": 500,
                    "cached_input_tokens": 500,
                },
            )

        model_name = "grok-4-1-fast"
        self.assertEqual(
            requests.children[
                self._labels_key(model=model_name, operation=GROK_OPERATION_ANALYZE_CHUNKS)
            ].incs,
            [1],
        )
        self.assertEqual(
            tokens.children[self._labels_key(model=model_name, type="input")].incs,
            [1500],
        )
        self.assertEqual(
            tokens.children[self._labels_key(model=model_name, type="output")].incs,
            [500],
        )
        self.assertEqual(
            duration.children[self._labels_key(model=model_name)].observations,
            [1234.5],
        )
        # grok-4-1-fast: uncached_input=1000 tokens × $0.0002/1k = $0.0002
        #                cached_input=500 tokens × $0.00005/1k  = $0.000025
        #                output=500 tokens × $0.0004/1k          = $0.0002
        self.assertAlmostEqual(
            cost.children[self._labels_key(model=model_name)].incs[0],
            0.000425,
        )

    def test_grok_metrics_use_low_cardinality_labels(self):
        self.assertEqual(tuple(grok_requests_total._labelnames), ("model", "operation"))
        self.assertEqual(tuple(grok_tokens_total._labelnames), ("model", "type"))
        self.assertEqual(tuple(grok_cost_usd_total._labelnames), ("model",))
        self.assertEqual(tuple(grok_request_duration_ms._labelnames), ("model",))


class GrokCostDashboardTests(SimpleTestCase):
    def test_cost_dashboard_only_uses_real_metrics(self):
        dashboard_path = (
            Path(__file__).resolve().parents[3]
            / "monitoring"
            / "grafana"
            / "dashboards"
            / "dashboard_06_cost.json"
        )
        dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))

        expressions = [
            target["expr"]
            for panel in dashboard["panels"]
            for target in panel.get("targets", [])
            if target.get("expr")
        ]
        metric_pattern = re.compile(r"([a-zA-Z_:][a-zA-Z0-9_:]*)(?=\{|\[|$)")
        metric_names = set()
        for expression in expressions:
            metric_names.update(metric_pattern.findall(expression))

        self.assertEqual(
            metric_names,
            {
                "grok_cost_usd_total",
                "grok_request_duration_ms_count",
                "grok_request_duration_ms_sum",
                "grok_requests_total",
                "grok_tokens_total",
            },
        )
