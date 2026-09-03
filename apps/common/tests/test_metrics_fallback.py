"""Fallback de métricas quando prometheus_client não está instalado.

O bloco ``except ImportError`` de ``apps.common.metrics`` existe para o cenário
"imagem não reconstruída depois de mudar requirements.txt". Se o substituto no-op
não cobrir toda a superfície usada pelo código, o remendo derruba a aplicação
justamente no cenário que ele deveria proteger.
"""

from __future__ import annotations

import builtins
import importlib
import importlib.util
import sys
import unittest
from contextlib import contextmanager
from unittest import mock

METRICS_MODULE = "apps.common.metrics"


@contextmanager
def metrics_without_prometheus():
    """Importa apps.common.metrics como se prometheus_client não existisse."""
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "prometheus_client" or name.startswith("prometheus_client."):
            raise ImportError("simulado: prometheus_client não instalado")
        return real_import(name, *args, **kwargs)

    saved_prometheus = {k: v for k, v in sys.modules.items() if k.startswith("prometheus_client")}
    saved_metrics = sys.modules.get(METRICS_MODULE)
    for name in saved_prometheus:
        del sys.modules[name]
    sys.modules.pop(METRICS_MODULE, None)
    try:
        with mock.patch.object(builtins, "__import__", side_effect=fake_import):
            yield importlib.import_module(METRICS_MODULE)
    finally:
        sys.modules.pop(METRICS_MODULE, None)
        sys.modules.update(saved_prometheus)
        if saved_metrics is not None:
            sys.modules[METRICS_MODULE] = saved_metrics


def _prometheus_client_installed() -> bool:
    return importlib.util.find_spec("prometheus_client") is not None


def _metric_objects(module):
    """Todas as métricas exportadas pelo módulo (Counter/Histogram no-op)."""
    found = []
    for name in dir(module):
        if name.startswith("_"):
            continue
        obj = getattr(module, name)
        if hasattr(obj, "labels"):
            found.append((name, obj))
    return found


class MetricsFallbackTests(unittest.TestCase):
    def test_fallback_is_active_without_prometheus_client(self):
        """Guarda do próprio teste: sem isto os demais poderiam testar a lib real."""
        with metrics_without_prometheus() as metrics:
            self.assertEqual(type(metrics.publish_duration_ms).__name__, "_NoOpMetric")

    def test_observe_without_labels_does_not_raise(self):
        """Regressão: publish_duration_ms.observe() é chamado sem labels em social/tasks.py."""
        with metrics_without_prometheus() as metrics:
            metrics.publish_duration_ms.observe(1234.5)
            metrics.publish_reconciliation_duration_ms.observe(10.0)
            metrics.multiple_creator_duration_ms.observe(0.0)

    def test_inc_without_labels_does_not_raise(self):
        with metrics_without_prometheus() as metrics:
            metrics.publish_attempts_total.inc()
            metrics.publish_failures_total.inc(2)

    def test_labels_child_supports_inc_and_observe(self):
        with metrics_without_prometheus() as metrics:
            metrics.task_started_total.labels(task_name="t", queue_name="q").inc()
            metrics.task_duration_ms.labels(task_name="t", queue_name="q").observe(5.0)
            metrics.grok_cost_usd_total.labels(model="m").inc(0.42)

    def test_every_metric_supports_the_full_surface(self):
        """Anti-drift: nenhuma métrica pode ficar sem parte da superfície usada no código."""
        with metrics_without_prometheus() as metrics:
            objects = _metric_objects(metrics)
            self.assertGreater(len(objects), 20, "métricas não foram coletadas")
            for name, metric in objects:
                with self.subTest(metric=name):
                    metric.inc()
                    metric.observe(1.0)
                    child = metric.labels(a="1", b="2")
                    child.inc()
                    child.observe(1.0)

    @unittest.skipUnless(_prometheus_client_installed(), "prometheus_client não instalado")
    def test_real_prometheus_client_is_restored_afterwards(self):
        """O context manager não pode vazar o módulo no-op para os outros testes."""
        with metrics_without_prometheus():
            pass
        metrics = importlib.import_module(METRICS_MODULE)
        self.assertNotEqual(type(metrics.publish_duration_ms).__name__, "_NoOpMetric")
