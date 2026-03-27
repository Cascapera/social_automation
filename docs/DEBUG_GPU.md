# Debug: GPU crash/hang (Whisper)

When transcription hangs with GPU but works on CPU, follow these steps to diagnose.

## Environment variables (.env)

| Variable | Values | Description |
|----------|--------|-------------|
| `WHISPER_FORCE_CPU` | `1` (default) | When set, Whisper uses CPU only so the GPU stays free for NVENC. Set to `0` to allow CUDA again (still overridable by `python manage.py test_whisper_gpu ... --device cuda`). |
| `WHISPER_DEVICE` | `cpu` | Force CPU (avoids crash on low-VRAM machines) |
| `WHISPER_DEVICE` | `cuda` | Force GPU; on failure, falls back to CPU |
| `WHISPER_DEVICE` | (empty) | Try CUDA first, fall back to CPU on error |
| `WHISPER_DEBUG_GPU` | `1` | **Diagnostic mode:** no fallback; re-raises with full traceback |
| `WHISPER_MODEL` | `tiny`, `base`, `small`, `medium`, `large-v3` | Whisper model. Short videos: default `large-v3`. Long (chunked): default `small`. |

To **run with GPU and see errors** in Celery:
1. Remove or comment `WHISPER_DEVICE=cpu` in `.env` (or set `WHISPER_DEVICE=cuda`)
2. Add `WHISPER_DEBUG_GPU=1` to skip fallback and see the full error
3. Restart the Celery worker and watch logs

## 1. Isolated test (outside Celery)

Run transcription **without** Celery to see if the issue is worker-specific:

```powershell
# With GPU (will hang if the problem is in transcription)
python manage.py test_whisper_gpu storage/media/cortes_processo/1/chunk_001.wav --device cuda

# With CPU (should work)
python manage.py test_whisper_gpu storage/media/cortes_processo/1/chunk_001.wav --device cpu
```

Use a real audio file (e.g. a 10–18 min chunk). For a recent job, chunks live under `storage/media/cortes_processo/<analysis_id>/`.

**Interpretation:**
- Hangs in isolated test with GPU → issue in faster-whisper/CUDA
- Works in isolated test with GPU → likely Celery + CUDA interaction

## 2. Logs to locate the hang

Logs show where it stopped:

- `Whisper: loading model X on CUDA...` → hang loading model
- `Whisper: model loaded. Starting transcription...` → hang during transcription

With the current logging, errors are logged with full traceback before fallback.

## 3. Synchronous CUDA (clearer stack trace)

For a clearer stack trace on error:

```powershell
$env:CUDA_LAUNCH_BLOCKING = "1"
python manage.py test_whisper_gpu path/to/file.wav --device cuda
```

## 4. py-spy (stack trace while process is stuck)

If the process hangs with no error:

1. In another terminal, find the Python process PID.
2. Install: `pip install py-spy`
3. Run: `py-spy dump --pid <PID>`

This shows which function the process is stuck in.

## 5. Check GPU usage

While the process runs (or hangs):

```powershell
nvidia-smi
```

Check whether other processes are using the GPU.

## 6. Alternatives

- Use CPU: `WHISPER_DEVICE=cpu` in `.env`
- Use a smaller model: `--model small` or `medium` in the test
- Update NVIDIA drivers and CUDA libraries
