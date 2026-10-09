"""Offline speech recognition for a public YouTube video's downloaded audio."""

import math
import os
import subprocess
import sys
import tempfile
import threading
import time
from importlib.util import find_spec
from pathlib import Path

from config import (
    LOCAL_AUDIO_TEMP_PATH,
    LOCAL_WHISPER_DEVICE,
    LOCAL_WHISPER_CPU_THREADS,
    LOCAL_WHISPER_MODEL,
    LOCAL_WHISPER_MODEL_PATH,
)
from .utils import clean_text


AUDIO_CHUNK_SECONDS = 15 * 60
_thread_model = threading.local()
_cuda_lock = threading.Lock()
_cuda_configured = False
_cuda_dll_handles = []


class VideoUnavailableError(RuntimeError):
    """YouTube will not provide playable audio for this video."""


class VideoRestrictedError(RuntimeError):
    """YouTube requires authentication before it provides this video's audio."""


class VideoAccessBlockedError(RuntimeError):
    """YouTube challenged this connection before providing public audio."""


def _configure_cuda_dlls() -> None:
    """Expose project-local NVIDIA wheel DLLs to CTranslate2 on Windows."""
    global _cuda_configured
    if sys.platform != "win32" or LOCAL_WHISPER_DEVICE != "cuda":
        return
    with _cuda_lock:
        if _cuda_configured:
            return
        for package in ("nvidia.cublas", "nvidia.cudnn"):
            spec = find_spec(package)
            if spec is None or not spec.submodule_search_locations:
                continue
            root = Path(next(iter(spec.submodule_search_locations)))
            for child in ("bin", "lib"):
                folder = root / child
                if folder.is_dir():
                    _cuda_dll_handles.append(os.add_dll_directory(str(folder)))
                    os.environ["PATH"] = str(folder) + os.pathsep + os.environ.get("PATH", "")
        _cuda_configured = True


def _whisper_model():
    """Load one model per transcription thread and reuse it across videos."""
    model = getattr(_thread_model, "model", None)
    if model is not None:
        return model
    _configure_cuda_dlls()
    from faster_whisper import WhisperModel

    model_root = os.path.abspath(LOCAL_WHISPER_MODEL_PATH)
    os.makedirs(model_root, exist_ok=True)
    try:
        model = WhisperModel(
            LOCAL_WHISPER_MODEL,
            device=LOCAL_WHISPER_DEVICE,
            compute_type="int8" if LOCAL_WHISPER_DEVICE == "cpu" else "float16",
            download_root=model_root,
            cpu_threads=LOCAL_WHISPER_CPU_THREADS,
        )
    except Exception as error:
        raise RuntimeError(
            "Could not load the local speech model. Check the model download and device setting."
        ) from error
    _thread_model.model = model
    return model


def _download_audio(video_id: str, directory: Path) -> tuple[Path, str, float]:
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadError

    options = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "outtmpl": str(directory / "audio.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "js_runtimes": {"node": {}},
        "proxy": "",
        "socket_timeout": 20,
        "retries": 3,
        "fragment_retries": 3,
        "noprogress": True,
    }
    bot_challenged = False
    for attempt in range(3):
        try:
            attempt_options = dict(options)
            if bot_challenged:
                client = ("android", "mweb")[attempt - 1]
                attempt_options["extractor_args"] = {"youtube": {"player_client": [client]}}
            with YoutubeDL(attempt_options) as downloader:
                info = downloader.extract_info(
                    f"https://www.youtube.com/watch?v={video_id}", download=True
                )
            break
        except DownloadError as error:
            message = str(error).lower()
            if "sign in to confirm your age" in message:
                raise VideoRestrictedError("YouTube requires age verification for this video.") from error
            if any(marker in message for marker in ("video unavailable", "private video")):
                raise VideoUnavailableError("This video is unavailable on YouTube.") from error
            bot_challenge = "sign in to confirm" in message and "not a bot" in message
            bot_challenged = bot_challenged or bot_challenge
            temporary_failure = any(
                code in str(error) for code in ("HTTP Error 403", "HTTP Error 429", "HTTP Error 5")
            ) or bot_challenged
            if attempt == 2 or not temporary_failure:
                if bot_challenged:
                    raise VideoAccessBlockedError(
                        "YouTube blocked this connection with a bot verification challenge."
                    ) from error
                raise RuntimeError("Could not download this video's audio from YouTube.") from error
            for partial in directory.glob("audio.*.part"):
                partial.unlink(missing_ok=True)
            if bot_challenged:
                continue
            time.sleep(3 * (attempt + 1))
    audio_files = [path for path in directory.glob("audio.*") if path.is_file() and not path.name.endswith(".part")]
    if not info or not audio_files:
        raise RuntimeError("No downloadable audio was found for this video.")
    audio_path = audio_files[0]
    duration = float(info.get("duration") or 0)
    if duration <= 0:
        import av
        with av.open(str(audio_path)) as container:
            duration = float(container.duration or 0) / 1_000_000
    if duration <= 0:
        raise RuntimeError("Could not determine the video's audio duration.")
    title = clean_text(str(info.get("title") or f"YouTube video {video_id}"))
    return audio_path, title, duration


def _audio_chunk(audio_path: Path, output_path: Path, start: int, length: int) -> None:
    from imageio_ffmpeg import get_ffmpeg_exe

    command = [
        get_ffmpeg_exe(), "-nostdin", "-loglevel", "error", "-y",
        "-ss", str(start), "-i", str(audio_path), "-t", str(length),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(output_path),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=600)
    except (subprocess.SubprocessError, OSError) as error:
        raise RuntimeError("Could not prepare the video's audio for transcription.") from error


def transcribe_video_audio(video_id: str) -> dict:
    """Download audio, transcribe 15-minute slices locally, and preserve timestamps."""
    temporary_root = Path(LOCAL_AUDIO_TEMP_PATH).resolve()
    temporary_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="channelmind-audio-", dir=temporary_root
    ) as temporary:
        directory = Path(temporary)
        audio_path, title, duration = _download_audio(video_id, directory)
        model = _whisper_model()

        segments = []
        for part in range(math.ceil(duration / AUDIO_CHUNK_SECONDS)):
            offset = part * AUDIO_CHUNK_SECONDS
            length = min(AUDIO_CHUNK_SECONDS, math.ceil(duration - offset))
            chunk_path = directory / f"chunk-{part:06d}.wav"
            _audio_chunk(audio_path, chunk_path, offset, length)
            try:
                recognized, _ = model.transcribe(
                    str(chunk_path), beam_size=5, vad_filter=True,
                    condition_on_previous_text=False,
                )
                for item in recognized:
                    text = clean_text(item.text)
                    if text:
                        segments.append({
                            "text": text,
                            "start": int(offset + item.start),
                            "duration": max(0, item.end - item.start),
                        })
            finally:
                chunk_path.unlink(missing_ok=True)
        if not segments:
            raise RuntimeError("No speech was detected in this video's audio.")
        return {
            "video_id": video_id,
            "title": title,
            "transcript": " ".join(item["text"] for item in segments),
            "segments": segments,
            "transcript_source": "local_speech_recognition",
        }
