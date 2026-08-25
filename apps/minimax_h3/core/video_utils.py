"""
Utilidades de video para el encadenado de tramos (chains.py): extraccion del
ultimo frame de un clip (para continuar con I2V) y concatenacion de los
tramos aceptados en un clip final.

ffmpeg portatil via imageio-ffmpeg (binario estatico empaquetado en el venv,
CON libx264/aac) -- nunca un ffmpeg de sistema: el hub no debe requerir pasos
manuales fuera del launcher estandar (misma filosofia que B2 en reglas
globales -- todo vive en el venv del proyecto).
"""
import base64
import subprocess
from pathlib import Path

import imageio_ffmpeg

_FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


class VideoUtilError(Exception):
    pass


def _run(args: list[str]):
    proc = subprocess.run([_FFMPEG, *args], capture_output=True)
    if proc.returncode != 0:
        raise VideoUtilError(proc.stderr.decode("utf-8", "replace")[-2000:])


def last_frame_b64(video: Path) -> str:
    """Ultimo frame exacto del video, como PNG en base64 (sin prefijo data:).
    Invierte el video entero y toma el primer frame del resultado -- mas
    confiable que `-sseof` con los contenedores/keyframes que suelta H3."""
    out = video.with_name(video.stem + "_lastframe.png")
    try:
        _run(["-y", "-i", str(video), "-vf", "reverse",
              "-frames:v", "1", "-q:v", "2", str(out)])
        return base64.b64encode(out.read_bytes()).decode("ascii")
    finally:
        out.unlink(missing_ok=True)


def concat_videos(videos: list[Path], out_path: Path):
    """Concatena varios clips (mismo codec/resolucion -- todos salen del mismo
    pipeline H3) en uno solo, con audio. Re-encoda en vez de `-c copy` porque
    los timestamps/keyframes entre tramos generados por separado no siempre
    empalman limpio con solo copiar streams."""
    if not videos:
        raise VideoUtilError("no hay clips para concatenar")
    list_file = out_path.with_suffix(".txt")
    list_file.write_text(
        "\n".join(f"file '{v.resolve().as_posix()}'" for v in videos), encoding="utf-8")
    try:
        _run(["-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
              "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p", str(out_path)])
    finally:
        list_file.unlink(missing_ok=True)
