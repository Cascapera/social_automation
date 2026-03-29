"""Prometheus scrape endpoint.

Set PROMETHEUS_MULTIPROC_DIR to a shared directory (web + Celery workers) so
counters/histograms from worker processes aggregate on scrape.
"""

import os

from django.http import HttpResponse


def prometheus_metrics(request):
    try:
        from prometheus_client import (
            CONTENT_TYPE_LATEST,
            CollectorRegistry,
            generate_latest,
            multiprocess,
        )
    except ImportError:
        return HttpResponse(
            "# prometheus_client not installed; rebuild the Docker image (pip install -r requirements.txt)\n",
            content_type="text/plain",
            status=503,
        )

    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        data = generate_latest(registry)
    else:
        data = generate_latest()
    return HttpResponse(data, content_type=CONTENT_TYPE_LATEST)
