"""Prometheus scrape endpoint.

Set ``PROMETHEUS_MULTIPROC_DIR`` to a writable directory for the current
process. When multiple containers share the same multiprocess root, use a
service-specific subdirectory per container and expose the shared parent in
``PROMETHEUS_MULTIPROC_ROOT_DIR`` so the Django scrape can aggregate all
worker/web metrics without PID filename collisions.
"""

import glob
import os

from django.http import HttpResponse


def _collect_multiproc_paths(current_dir: str, root_dir: str | None) -> list[str]:
    paths: list[str] = []

    def _append(path: str | None) -> None:
        if path and os.path.isdir(path) and path not in paths:
            paths.append(path)

    _append(current_dir)
    if root_dir and os.path.isdir(root_dir):
        if glob.glob(os.path.join(root_dir, "*.db")):
            _append(root_dir)
        for entry in sorted(os.listdir(root_dir)):
            child = os.path.join(root_dir, entry)
            if os.path.isdir(child):
                _append(child)
    return paths


class _MultiProcessDirsCollector:
    def __init__(self, paths: list[str], multiprocess_module) -> None:
        self._paths = paths
        self._multiprocess = multiprocess_module

    def collect(self):
        files: list[str] = []
        for path in self._paths:
            files.extend(glob.glob(os.path.join(path, "*.db")))
        return self._multiprocess.MultiProcessCollector.merge(files, accumulate=True)


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

    multiproc_dir = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
    if multiproc_dir:
        registry = CollectorRegistry()
        multiproc_root = os.environ.get("PROMETHEUS_MULTIPROC_ROOT_DIR")
        paths = _collect_multiproc_paths(multiproc_dir, multiproc_root)
        if len(paths) <= 1:
            multiprocess.MultiProcessCollector(
                registry,
                path=paths[0] if paths else multiproc_dir,
            )
        else:
            registry.register(_MultiProcessDirsCollector(paths, multiprocess))
        data = generate_latest(registry)
    else:
        data = generate_latest()
    return HttpResponse(data, content_type=CONTENT_TYPE_LATEST)
