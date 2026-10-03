from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from api.schemas import VideoIngestionRequest
from src.local_transcriber import transcribe_video_audio
from src.video_transcript import fetch_video_transcript


@pytest.mark.parametrize("value", [
    "kLZgQWjnUz0",
    "https://www.youtube.com/watch?v=kLZgQWjnUz0",
    "https://youtu.be/kLZgQWjnUz0?t=30",
    "https://www.youtube.com/live/kLZgQWjnUz0",
    "https://www.youtube.com/shorts/kLZgQWjnUz0",
])
def test_video_url_normalizes_to_id(value):
    assert VideoIngestionRequest(video=value).video == "kLZgQWjnUz0"


@pytest.mark.parametrize("value", [
    "https://evil.example/watch?v=kLZgQWjnUz0",
    "http://www.youtube.com/watch?v=kLZgQWjnUz0",
    "https://www.youtube.com/@channel",
    "https://www.youtube.com/watch?v=too-short",
])
def test_video_url_rejects_invalid_targets(value):
    with pytest.raises(ValidationError):
        VideoIngestionRequest(video=value)


def test_caption_path_preserves_all_segments_without_a_paid_api():
    snippets = [
        SimpleNamespace(text="Part one", start=0.1, duration=3.4),
        SimpleNamespace(text="Part two", start=14913.5, duration=5.0),
    ]
    with patch("src.video_transcript.YouTubeTranscriptApi") as api, patch(
        "src.video_transcript._video_title", return_value="Long lesson"
    ), patch("src.video_transcript.transcribe_video_audio") as asr:
        api.return_value.fetch.return_value = snippets
        result = fetch_video_transcript("kLZgQWjnUz0")
    assert len(result["segments"]) == 2
    assert result["segments"][-1]["start"] == 14913
    assert result["transcript_source"] == "youtube_captions"
    asr.assert_not_called()


def test_missing_captions_use_local_speech_recognition():
    with patch("src.video_transcript.YouTubeTranscriptApi") as api, patch(
        "src.video_transcript.transcribe_video_audio", return_value={"transcript_source": "local_speech_recognition"}
    ) as asr:
        api.return_value.fetch.side_effect = RuntimeError("captions unavailable")
        result = fetch_video_transcript("kLZgQWjnUz0")
    assert result["transcript_source"] == "local_speech_recognition"
    asr.assert_called_once_with("kLZgQWjnUz0")


def test_local_recognition_offsets_each_bounded_audio_chunk():
    def fake_download(_video_id, directory):
        return Path(directory) / "audio.webm", "Long lesson", 1900

    fake_segments = [SimpleNamespace(text="spoken words", start=2.5, end=5.0)]
    with patch("src.local_transcriber._download_audio", side_effect=fake_download), patch(
        "src.local_transcriber._audio_chunk"
    ) as split, patch("faster_whisper.WhisperModel") as model:
        model.return_value.transcribe.return_value = (fake_segments, None)
        result = transcribe_video_audio("kLZgQWjnUz0")
    assert split.call_count == 3
    assert [segment["start"] for segment in result["segments"]] == [2, 902, 1802]
    assert result["transcript_source"] == "local_speech_recognition"
