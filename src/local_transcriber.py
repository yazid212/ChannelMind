"""Offline speech recognition for a public YouTube video's downloaded audio."""

import math
import os
import subprocess
import tempfile
from pathlib import Path

from config import LOCAL_WHISPER_DEVICE, LOCAL_WHISPER_MODEL, LOCAL_WHISPER_MODEL_PATH
from .utils import clean_text


AUDIO_CHUNK_SECONDS = 15 * 60


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
    }
    try:
        with YoutubeDL(options) as downloader:
            info = downloader.extract_info(
                f"https://www.youtube.com/watch?v={video_id}", download=True
            )
    except DownloadError as error:
        raise RuntimeError("Could not download this video's audio from YouTube.") from error
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
    from faster_whisper import WhisperModel

    with tempfile.TemporaryDirectory(prefix="channelmind-audio-") as temporary:
        directory = Path(temporary)
        audio_path, title, duration = _download_audio(video_id, directory)
        model_root = os.path.abspath(LOCAL_WHISPER_MODEL_PATH)
        os.makedirs(model_root, exist_ok=True)
        try:
            model = WhisperModel(
                LOCAL_WHISPER_MODEL,
                device=LOCAL_WHISPER_DEVICE,
                compute_type="int8" if LOCAL_WHISPER_DEVICE == "cpu" else "float16",
                download_root=model_root,
            )
        except Exception as error:
            raise RuntimeError("Could not load the local speech model. Check the model download and device setting.") from error

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
