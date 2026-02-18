import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from django.conf import settings

@dataclass
class CmdResult:
    ok: bool
    stdout: str
    stderr: str
    returncode: int

def run_cmd(cmd: List[str], cwd: Optional[Path] = None) -> CmdResult:
    p = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        shell=False,
    )
    return CmdResult(ok=p.returncode == 0, stdout=p.stdout, stderr=p.stderr, returncode=p.returncode)

def has_nvenc() -> bool:
    res = run_cmd([settings.FFMPEG_BIN, "-hide_banner", "-encoders"])
    return res.ok and ("h264_nvenc" in res.stdout)

def input_has_audio(input_file: Path) -> bool:
    cmd = [
        settings.FFPROBE_BIN, "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=index",
        "-of", "csv=p=0",
        str(input_file),
    ]
    res = run_cmd(cmd)
    return res.ok and res.stdout.strip() != ""

def video_encode_args(use_gpu: bool) -> list[str]:
    if use_gpu:
        return ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "19", "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]

def audio_encode_args(input_file: Path) -> list[str]:
    if input_has_audio(input_file):
        return ["-c:a", "aac", "-b:a", "160k"]
    return ["-an"]

def common_mp4_flags() -> list[str]:
    return ["-movflags", "+faststart"]

def cut_clip(input_file: Path, start_tc: str, end_tc: str, output_file: Path, use_gpu: bool) -> None:
    cmd = [
        settings.FFMPEG_BIN, "-y",
        "-ss", start_tc, "-to", end_tc,
        "-i", str(input_file),
        *video_encode_args(use_gpu),
        *audio_encode_args(input_file),
        *common_mp4_flags(),
        str(output_file),
    ]
    res = run_cmd(cmd)
    if not res.ok:
        raise RuntimeError(f"cut failed: {res.stderr}")

def make_vertical_blur(input_file: Path, output_file: Path, use_gpu: bool) -> None:
    fps = "30"
    w, h = 1080, 1920
    vf2 = (
        f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},gblur=sigma=20,fps={fps},format=yuv420p,"
        f"setpts=N/({fps}*TB)[bg];"
        f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,fps={fps},format=yuv420p,"
        f"setpts=N/({fps}*TB)[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2"
    )
    cmd = [
        settings.FFMPEG_BIN, "-y",
        "-i", str(input_file),
        "-filter_complex", vf2,
        *video_encode_args(use_gpu),
        *audio_encode_args(input_file),
        *common_mp4_flags(),
        str(output_file),
    ]
    res = run_cmd(cmd)
    if not res.ok:
        raise RuntimeError(f"vertical failed: {res.stderr}")

def normalize_part_for_concat(
    input_file: Path, output_file: Path, use_gpu: bool, *, make_vertical: bool = True
) -> None:
    """Codifica uma parte para formato padrão (30fps, yuv420p, aac).
    make_vertical=True: 1080x1920 (9:16). make_vertical=False: 1920x1080 (16:9)."""
    w, h = (1080, 1920) if make_vertical else (1920, 1080)
    fps = "30"
    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,fps={fps},format=yuv420p"
    )
    has_audio = input_has_audio(input_file)
    if has_audio:
        cmd = [
            settings.FFMPEG_BIN, "-y",
            "-i", str(input_file),
            "-vf", vf,
            *video_encode_args(use_gpu),
            "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
            *common_mp4_flags(),
            str(output_file),
        ]
    else:
        dur = ffprobe_duration(input_file)
        cmd = [
            settings.FFMPEG_BIN, "-y",
            "-i", str(input_file),
            "-f", "lavfi", "-i", f"anullsrc=channel_layout=stereo:sample_rate=48000",
            "-filter_complex",
            f"[0:v]{vf}[v];[1:a]atrim=0:{dur},asetpts=PTS-STARTPTS[a]",
            "-map", "[v]", "-map", "[a]",
            *video_encode_args(use_gpu),
            "-c:a", "aac", "-b:a", "160k",
            *common_mp4_flags(),
            str(output_file),
        ]
    res = run_cmd(cmd)
    if not res.ok:
        raise RuntimeError(f"normalize failed: {res.stderr}")


def concat_videos(files: List[Path], output_file: Path, workdir: Path, use_gpu: bool) -> None:
    list_file = workdir / "concat_list.txt"
    with open(list_file, "w", encoding="utf-8", newline="\n") as f:
        for p in files:
            path_str = p.resolve().as_posix().replace("'", "'\\''")
            f.write(f"file '{path_str}'\n")

    cmd = [
        settings.FFMPEG_BIN, "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        *video_encode_args(use_gpu),
        "-c:a", "aac", "-b:a", "160k",
        *common_mp4_flags(),
        str(output_file),
    ]
    res = run_cmd(cmd, cwd=workdir)
    if not res.ok:
        raise RuntimeError(f"concat failed: {res.stderr}")


def concat_with_xfade(
    parts: List[Path],
    output_file: Path,
    transition: str,
    duration_sec: float,
    use_gpu: bool,
) -> None:
    """Concatena partes com transição xfade: intro-(trans)-cut-(trans)-outro."""
    if len(parts) < 2:
        raise ValueError("xfade precisa de pelo menos 2 partes")
    if transition == "none":
        raise ValueError("transition não pode ser 'none' para concat_with_xfade")

    T = duration_sec
    durations = [ffprobe_duration(p) for p in parts]
    n = len(parts)

    inputs = []
    for p in parts:
        inputs += ["-i", str(p)]

    # Cadeia de xfade para vídeo: [0][1]xfade->v01; [v01][2]xfade->vout
    # offset = duração acumulada do output anterior - T
    v_filters = []
    cum_dur = durations[0]
    for i in range(1, n):
        offset = cum_dur - T
        if offset < 0:
            offset = 0
        in1 = f"[v{i-1:02d}]" if i > 1 else "[0:v]"
        in2 = f"[{i}:v]"
        out = "[vout]" if i == n - 1 else f"[v{i:02d}]"
        v_filters.append(f"{in1}{in2}xfade=transition={transition}:duration={T}:offset={offset}{out}")
        cum_dur = cum_dur + durations[i] - T

    # Cadeia de acrossfade para áudio
    a_filters = []
    for i in range(1, n):
        in1 = "[0:a]" if i == 1 else f"[a{i-1:02d}]"
        in2 = f"[{i}:a]"
        out = "[aout]" if i == n - 1 else f"[a{i:02d}]"
        a_filters.append(f"{in1}{in2}acrossfade=d={T}:c1=tri:c2=tri{out}")

    filter_complex = ";".join(v_filters) + ";" + ";".join(a_filters)
    cmd = [
        settings.FFMPEG_BIN, "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", "[aout]",
        *video_encode_args(use_gpu),
        "-c:a", "aac", "-b:a", "160k",
        *common_mp4_flags(),
        str(output_file),
    ]
    res = run_cmd(cmd)
    if not res.ok:
        raise RuntimeError(f"concat(xfade) failed: {res.stderr}")


def concat_videos_copy(files: List[Path], output_file: Path, workdir: Path) -> None:
    """Concatena arquivos já normalizados com -c copy (sem re-encode)."""
    list_file = workdir / "concat_list.txt"
    with open(list_file, "w", encoding="utf-8", newline="\n") as f:
        for p in files:
            path_str = p.resolve().as_posix().replace("'", "'\\''")
            f.write(f"file '{path_str}'\n")

    cmd = [
        settings.FFMPEG_BIN, "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        *common_mp4_flags(),
        str(output_file),
    ]
    res = run_cmd(cmd, cwd=workdir)
    if not res.ok:
        raise RuntimeError(f"concat(copy) failed: {res.stderr}")

def ffprobe_duration(input_file: Path) -> float:
    cmd = [
        settings.FFPROBE_BIN, "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(input_file),
    ]
    res = run_cmd(cmd)
    if not res.ok:
        raise RuntimeError(f"ffprobe duration failed: {res.stderr}")
    return float(res.stdout.strip())

def concat_videos_filter(parts: List[Path], output_file: Path, use_gpu: bool) -> None:
    fps = "30"  # escolha fixa para shorts; pode virar preset depois
    w, h = 1080, 1920  # formato vertical para Reels/Shorts/TikTok

    inputs = []
    filter_lines = []
    vlabels = []
    alabels = []

    for i, p in enumerate(parts):
        inputs += ["-i", str(p)]

        # Vídeo: normaliza resolução (evita freeze por mismatch), CFR + PTS novo
        filter_lines.append(
            f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,fps={fps},format=yuv420p,"
            f"setpts=N/({fps}*TB)[v{i}]"
        )
        vlabels.append(f"[v{i}]")

        # Áudio: se existir, reamostra e gera PTS novo; se não, cria silêncio com duração do clipe
        if input_has_audio(p):
            filter_lines.append(f"[{i}:a]aresample=48000,asetpts=N/SR/TB[a{i}]")
        else:
            dur = ffprobe_duration(p)
            filter_lines.append(
                f"anullsrc=channel_layout=stereo:sample_rate=48000,atrim=0:{dur},asetpts=N/SR/TB[a{i}]"
            )
        alabels.append(f"[a{i}]")

    n = len(parts)
    concat_line = f"{''.join(vlabels)}{''.join(alabels)}concat=n={n}:v=1:a=1[v][a]"
    filter_complex = ";".join(filter_lines + [concat_line])

    cmd = [
        settings.FFMPEG_BIN, "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-map", "[a]",
        *video_encode_args(use_gpu),
        "-c:a", "aac", "-b:a", "160k",
        *common_mp4_flags(),
        str(output_file),
    ]

    res = run_cmd(cmd)
    if not res.ok:
        raise RuntimeError(f"concat(filter) failed: {res.stderr}")