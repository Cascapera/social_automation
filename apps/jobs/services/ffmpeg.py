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
    crf = getattr(settings, "FFMPEG_LIBX264_CRF", 20)
    preset = getattr(settings, "FFMPEG_LIBX264_PRESET", "veryfast")
    return ["-c:v", "libx264", "-preset", str(preset), "-crf", str(crf), "-pix_fmt", "yuv420p"]


def video_encode_args_overlay_long_cpu() -> list[str]:
    """CPU: qualidade acima do pipeline geral (overlay longo = reencode crítico). Ajustável via .env."""
    crf = getattr(settings, "FFMPEG_LIBX264_OVERLAY_LONG_CRF", 16)
    preset = getattr(settings, "FFMPEG_LIBX264_OVERLAY_LONG_PRESET", "slow")
    return ["-c:v", "libx264", "-preset", str(preset), "-crf", str(crf), "-pix_fmt", "yuv420p"]


def video_encode_args_burn_cpu() -> list[str]:
    """Queima de legendas (filtro subtitles): alinha CRF/preset ao resto do pipeline."""
    crf = getattr(settings, "FFMPEG_LIBX264_BURN_CRF", 20)
    preset = getattr(settings, "FFMPEG_LIBX264_BURN_PRESET", "veryfast")
    return ["-c:v", "libx264", "-preset", str(preset), "-crf", str(crf), "-pix_fmt", "yuv420p"]


def audio_encode_args(input_file: Path) -> list[str]:
    if input_has_audio(input_file):
        return ["-c:a", "aac", "-b:a", "160k"]
    return ["-an"]

def common_mp4_flags() -> list[str]:
    return ["-movflags", "+faststart"]


def overlay_logo(
    input_path: Path,
    output_path: Path,
    logo_path: Path,
    x: int,
    y: int,
    logo_height: int = 160,
    opacity: float = 0.8,
    use_gpu: bool = False,
) -> None:
    """Sobrepoe logo no vídeo em posição x,y (px do topo-esquerda). Opacidade 0-1 (0.8 = 80%)."""
    aa = max(0.0, min(1.0, float(opacity)))
    cmd = [
        settings.FFMPEG_BIN, "-y",
        "-i", str(input_path),
        "-i", str(logo_path),
        "-filter_complex",
        f"[1:v]scale=-1:{logo_height},format=rgba,colorchannelmixer=aa={aa}[logo];"
        f"[0:v][logo]overlay={x}:{y}:format=auto",
        *video_encode_args(use_gpu),
        *audio_encode_args(input_path),
        *common_mp4_flags(),
        str(output_path),
    ]
    res = run_cmd(cmd)
    if not res.ok:
        raise RuntimeError(f"overlay logo failed: {res.stderr}")


def overlay_animation(
    input_path: Path,
    output_path: Path,
    animation_path: Path,
    position: str = "bottom_right",
    margin: int = 24,
    height: int = 120,
    use_gpu: bool = False,
) -> None:
    """
    Sobrepõe animação (PNG/GIF com fundo transparente) em um canto do vídeo.
    position: top_left, top_right, bottom_left, bottom_right
    margin: margem em px
    height: altura da animação (largura proporcional)
    """
    # Loop infinito para GIF/vídeo curto (animação se repete durante o vídeo)
    ext = str(animation_path).lower().split(".")[-1] if "." in str(animation_path) else ""
    needs_loop = ext in ("gif", "webm", "mov", "mp4")
    anim_input = ["-stream_loop", "-1", "-i", str(animation_path)] if needs_loop else ["-i", str(animation_path)]

    # Posição: FFmpeg overlay usa expressões
    # top_left: margin:margin
    # top_right: W-w-margin:margin
    # bottom_left: margin:H-h-margin
    # bottom_right: W-w-margin:H-h-margin
    pos_map = {
        "top_left": f"{margin}:{margin}",
        "top_right": f"W-w-{margin}:{margin}",
        "bottom_left": f"{margin}:H-h-{margin}",
        "bottom_right": f"W-w-{margin}:H-h-{margin}",
    }
    overlay_pos = pos_map.get(position, pos_map["bottom_right"])

    cmd = [
        settings.FFMPEG_BIN, "-y",
        "-i", str(input_path),
        *anim_input,
        "-filter_complex",
        f"[1:v]scale=-1:{height},format=rgba[anim];"
        f"[0:v][anim]overlay={overlay_pos}:format=auto",
        *video_encode_args(use_gpu),
        *audio_encode_args(input_path),
        *common_mp4_flags(),
        str(output_path),
    ]
    res = run_cmd(cmd)
    if not res.ok:
        raise RuntimeError(f"overlay animation failed: {res.stderr}")


def overlay_long_right(
    input_path: Path,
    overlay_path: Path,
    output_path: Path,
    use_gpu: bool = False,
) -> None:
    """
    Sobrepõe PNG/JPG ou vídeo MP4 alinhado à borda direita e superior do vídeo base.
    Se a altura do overlay for maior que a do vídeo, reduz para caber na altura.
    Se for menor, mantém o tamanho e posiciona no canto superior direito.
    - PNG/JPG: imagem estática repetida do início ao fim do vídeo base (-loop 1).
    - MP4 (ou outro vídeo): repete em loop (-stream_loop -1) até o fim do vídeo base;
      a saída é limitada à duração exata do vídeo principal (-t).
    Áudio: só o do vídeo base.
    """
    main_dur = ffprobe_duration(input_path)
    info_v = ffprobe_video_info(input_path)
    vw = max(1, int(info_v.get("width") or 1920))
    vh = max(1, int(info_v.get("height") or 1080))
    info_o = ffprobe_video_info(overlay_path)
    ow = max(1, int(info_o.get("width") or 1))
    oh = max(1, int(info_o.get("height") or 1))

    if oh > vh:
        sh = vh
        sw = max(1, int(round(ow * (vh / oh))))
    else:
        sh = oh
        sw = ow
    if sw > vw:
        sw = vw
        sh = max(1, int(round(oh * (vw / ow))))

    ext = str(overlay_path.suffix or "").lower().lstrip(".")
    is_video = ext in ("mp4", "mov", "webm", "mkv", "avi")
    if is_video:
        overlay_inputs = ["-stream_loop", "-1", "-i", str(overlay_path)]
    else:
        overlay_inputs = ["-loop", "1", "-i", str(overlay_path)]

    # shortest=0: não encerrar quando o clipe overlay (sem loop) acaba antes do principal.
    # Duração da saída = vídeo base via -t (PNG/JPG estático com -loop 1; MP4 com stream_loop até cortar).
    vf = (
        f"[1:v]scale={sw}:{sh}:flags=lanczos,format=rgba[ov];"
        f"[0:v][ov]overlay=W-w:0:shortest=0:format=auto[outv]"
    )
    cmd = [
        settings.FFMPEG_BIN,
        "-y",
        "-i",
        str(input_path),
        *overlay_inputs,
        "-filter_complex",
        vf,
    ]
    if input_has_audio(input_path):
        cmd.extend(["-map", "[outv]", "-map", "0:a"])
    else:
        cmd.extend(["-map", "[outv]"])
    enc = video_encode_args(use_gpu) if use_gpu else video_encode_args_overlay_long_cpu()
    cmd.extend(
        [
            *enc,
            *audio_encode_args(input_path),
            "-t",
            str(main_dur),
            *common_mp4_flags(),
            str(output_path),
        ]
    )
    res = run_cmd(cmd)
    if not res.ok:
        raise RuntimeError(f"overlay_long_right failed: {res.stderr}")


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


def normalize_video_to_canvas(
    input_path: Path,
    output_path: Path,
    width: int = 1920,
    height: int = 1080,
    use_gpu: bool = False,
    *,
    target_fps: Optional[int] = None,
    audio_hz: Optional[int] = None,
) -> None:
    """
    Coloca o vídeo em um canvas 16:9 fixo (padrão 1920×1080): escala mantendo proporção
    e completa com barras pretas (letterbox/pillarbox). Áudio preservado quando existir.

    target_fps: quando definido (ex.: 30), força FPS e SAR 1:1 — necessário antes de
    concat_with_xfade com clipes mistos (25/30/60 fps), pois o xfade exige timebase
    compatível entre entradas.

    audio_hz: quando definido com áudio na entrada, reamostra para esta taxa (ex.: 48000)
    para acrossfade entre clipes 44.1/48 kHz.
    """
    w, h = int(width), int(height)
    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black"
    )
    if target_fps is not None:
        fps = max(1, int(target_fps))
        vf = f"{vf},setsar=1,fps={fps}"
    cmd = [
        settings.FFMPEG_BIN,
        "-y",
        "-i",
        str(input_path),
        "-vf",
        vf,
    ]
    if audio_hz is not None and input_has_audio(input_path):
        hz = max(8000, int(audio_hz))
        cmd.extend(["-af", f"aresample={hz}"])
    cmd.extend(
        [
            *video_encode_args(use_gpu),
            *audio_encode_args(input_path),
            *common_mp4_flags(),
            str(output_path),
        ]
    )
    res = run_cmd(cmd)
    if not res.ok:
        raise RuntimeError(f"normalize_video_to_canvas failed: {res.stderr}")


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


def ffprobe_sample_aspect_ratio_float(sar_raw: str | None) -> float | None:
    """Converte ffprobe sample_aspect_ratio (ex. '1:1', '4:3', 'N/A') para float ou None se desconhecido."""
    if not sar_raw or str(sar_raw).strip() in ("N/A", "0:1", "nan"):
        return None
    s = str(sar_raw).strip().replace("/", ":")
    if s in ("1:1", "1"):
        return 1.0
    parts = [p for p in s.split(":") if p]
    if len(parts) >= 2:
        try:
            a, b = float(parts[0]), float(parts[1])
            if b:
                return a / b
        except ValueError:
            pass
    return None


def ffprobe_video_info(input_file: Path) -> dict:
    """Retorna duração (segundos), width, height (dimensões de exibição).
    Considera rotação via tags.rotate (ffprobe <5) ou side_data.rotation (ffprobe 5+).
    Inclui sample_aspect_ratio (string ffprobe) para detectar anamorfismo."""
    cmd = [
        settings.FFPROBE_BIN, "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,sample_aspect_ratio",
        "-show_entries", "stream_tags=rotate",
        "-show_entries", "stream_side_data=rotation",
        "-show_entries", "format=duration",
        "-of", "json",
        str(input_file),
    ]
    res = run_cmd(cmd)
    if not res.ok:
        raise RuntimeError(f"ffprobe failed: {res.stderr}")

    import json
    data = json.loads(res.stdout)
    stream = data.get("streams", [{}])[0]
    fmt = data.get("format", {})
    width = int(stream.get("width", 0))
    height = int(stream.get("height", 0))

    # Vídeos com rotation 90/270: exibição é height x width
    # stream_tags.rotate (ffprobe <5) ou stream_side_data (ffprobe 5+)
    rotated = False
    tags = stream.get("tags") or {}
    rotate = str(tags.get("rotate", "")).strip()
    if rotate in ("90", "270"):
        rotated = True
    else:
        side_data = stream.get("side_data") or stream.get("side_data_list") or []
        if isinstance(side_data, list):
            for sd in side_data:
                if not isinstance(sd, dict):
                    continue
                rot = sd.get("rotation")
                if rot is not None:
                    try:
                        if abs(int(rot)) in (90, 270):
                            rotated = True
                            break
                    except (ValueError, TypeError):
                        pass

    if rotated:
        width, height = height, width

    dur_val = fmt.get("duration", 0)
    duration = float(dur_val) if dur_val else 0.0
    sar_str = stream.get("sample_aspect_ratio")
    if isinstance(sar_str, str):
        sar_str = sar_str.strip()
    else:
        sar_str = None
    return {
        "duration": duration,
        "width": width,
        "height": height,
        "sample_aspect_ratio": sar_str,
    }


def seconds_to_tc(sec: float) -> str:
    """Converte segundos para HH:MM:SS."""
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def tc_to_seconds(tc: str) -> float:
    """Converte HH:MM:SS ou MM:SS ou SS para segundos."""
    if not tc or not isinstance(tc, str):
        return 0.0
    parts = [int(x) for x in tc.strip().split(":") if x.isdigit()]
    if not parts:
        return 0.0
    if len(parts) == 1:
        return float(parts[0])
    if len(parts) == 2:
        return float(parts[0] * 60 + parts[1])
    return float(parts[0] * 3600 + parts[1] * 60 + parts[2])

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