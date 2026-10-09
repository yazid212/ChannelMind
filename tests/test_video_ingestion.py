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


def test_collection_title_avoids_a_second_youtube_lookup():
    with patch("src.video_transcript.YouTubeTranscriptApi") as api, patch(
        "src.video_transcript._video_title"
    ) as lookup:
        api.return_value.fetch.return_value = [SimpleNamespace(text="Words", start=0, duration=1)]
        result = fetch_video_transcript("kLZgQWjnUz0", "Catalog lesson title")
    assert result["title"] == "Catalog lesson title"
    lookup.assert_not_called()


def test_missing_captions_use_local_speech_recognition():
    with patch("src.video_transcript.YouTubeTranscriptApi") as api, patch(
        "src.video_transcript.transcribe_video_audio", return_value={"transcript_source": "local_speech_recognition"}
    ) as asr:
        api.return_value.fetch.side_effect = RuntimeError("captions unavailable")
        result = fetch_video_transcript("kLZgQWjnUz0")
    assert result["transcript_source"] == "local_speech_recognition"
    asr.assert_called_once_with("kLZgQWjnUz0")


def test_caption_path_uses_configured_proxy():
    with patch("src.video_transcript.WEBSHARE_PROXY_USERNAME", "proxy-user"), patch(
        "src.video_transcript.WEBSHARE_PROXY_PASSWORD", "proxy-password"
    ), patch("src.video_transcript.WebshareProxyConfig") as proxy_class, patch(
        "src.video_transcript.YouTubeTranscriptApi"
    ) as api, patch("src.video_transcript.transcribe_video_audio") as asr:
        api.return_value.fetch.return_value = [SimpleNamespace(text="Words", start=0, duration=1)]
        fetch_video_transcript("kLZgQWjnUz0")
    assert api.call_args.kwargs["proxy_config"] is proxy_class.return_value
    asr.assert_not_called()


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


def test_audio_download_reextracts_after_transient_youtube_403(tmp_path):
    from yt_dlp.utils import DownloadError
    from src.local_transcriber import _download_audio

    def extract(_url, download):
        if downloader.extract_info.call_count == 1:
            raise DownloadError("HTTP Error 403: Forbidden")
        (tmp_path / "audio.webm").write_bytes(b"audio")
        return {"duration": 10, "title": "Retry lesson"}

    with patch("src.local_transcriber.time.sleep") as sleep, patch(
        "yt_dlp.YoutubeDL"
    ) as youtube:
        downloader = youtube.return_value.__enter__.return_value
        downloader.extract_info.side_effect = extract
        path, title, duration = _download_audio("kLZgQWjnUz0", tmp_path)

    assert path == tmp_path / "audio.webm"
    assert title == "Retry lesson"
    assert duration == 10
    assert downloader.extract_info.call_count == 2
    sleep.assert_called_once_with(3)


def test_audio_download_retries_bot_challenge_then_reports_access_block(tmp_path):
    from yt_dlp.utils import DownloadError

    from src.local_transcriber import VideoAccessBlockedError, _download_audio

    with patch("src.local_transcriber.time.sleep") as sleep, patch(
        "yt_dlp.YoutubeDL"
    ) as youtube:
        downloader = youtube.return_value.__enter__.return_value
        downloader.extract_info.side_effect = DownloadError(
            "Sign in to confirm you're not a bot"
        )
        with pytest.raises(VideoAccessBlockedError, match="bot verification"):
            _download_audio("kLZgQWjnUz0", tmp_path)

    assert downloader.extract_info.call_count == 3
    assert [call.args[0]["extractor_args"]["youtube"]["player_client"]
            for call in youtube.call_args_list[1:]] == [["android"], ["mweb"]]
    sleep.assert_not_called()


def test_audio_download_uses_android_client_after_bot_challenge(tmp_path):
    from yt_dlp.utils import DownloadError

    from src.local_transcriber import _download_audio

    def extract(_url, download):
        if downloader.extract_info.call_count == 1:
            raise DownloadError("Sign in to confirm you're not a bot")
        (tmp_path / "audio.webm").write_bytes(b"audio")
        return {"duration": 10, "title": "Recovered lesson"}

    with patch("yt_dlp.YoutubeDL") as youtube:
        downloader = youtube.return_value.__enter__.return_value
        downloader.extract_info.side_effect = extract
        path, title, duration = _download_audio("kLZgQWjnUz0", tmp_path)

    assert path == tmp_path / "audio.webm"
    assert title == "Recovered lesson"
    assert duration == 10
    assert youtube.call_args_list[1].args[0]["extractor_args"] == {
        "youtube": {"player_client": ["android"]}
    }


def test_unavailable_video_is_not_retried_as_a_transcription_failure(tmp_path):
    from yt_dlp.utils import DownloadError
    from src.local_transcriber import VideoUnavailableError, _download_audio

    with patch("src.local_transcriber.time.sleep") as sleep, patch(
        "yt_dlp.YoutubeDL"
    ) as youtube:
        downloader = youtube.return_value.__enter__.return_value
        downloader.extract_info.side_effect = DownloadError("Video unavailable")
        with pytest.raises(VideoUnavailableError):
            _download_audio("kLZgQWjnUz0", tmp_path)
    assert downloader.extract_info.call_count == 1
    sleep.assert_not_called()


def test_age_restricted_video_is_not_retried_as_a_transcription_failure(tmp_path):
    from yt_dlp.utils import DownloadError
    from src.local_transcriber import VideoRestrictedError, _download_audio

    with patch("src.local_transcriber.time.sleep") as sleep, patch(
        "yt_dlp.YoutubeDL"
    ) as youtube:
        downloader = youtube.return_value.__enter__.return_value
        downloader.extract_info.side_effect = DownloadError("Sign in to confirm your age")
        with pytest.raises(VideoRestrictedError):
            _download_audio("kLZgQWjnUz0", tmp_path)
    assert downloader.extract_info.call_count == 1
    sleep.assert_not_called()
