from dataclasses import dataclass
from pathlib import Path
from django.conf import settings

@dataclass(frozen=True)
class JobPaths:
    base: Path
    workspace: Path
    exports: Path
    logs: Path

def get_job_paths(job_id: int) -> JobPaths:
    base = Path(settings.MEDIA_ROOT) / "jobs" / str(job_id)
    workspace = base / "workspace"
    exports = base / "exports"
    logs = base / "logs"
    workspace.mkdir(parents=True, exist_ok=True)
    exports.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    return JobPaths(base=base, workspace=workspace, exports=exports, logs=logs)
