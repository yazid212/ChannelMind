"""Google OAuth and caption access using only the official YouTube API."""

import base64
import hashlib
import secrets
import time
from urllib.parse import urlencode

import requests

from .export_store import get_export_store
from .settings import get_settings

SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API_URL = "https://www.googleapis.com/youtube/v3/"


class YouTubeAccessError(Exception):
    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason


def start_authorization(identity, extension_id):
    settings = get_settings()
    if not settings.google_client_id or not settings.google_client_secret:
        raise YouTubeAccessError("setup", "Configure Google OAuth credentials on the API first.")
    if extension_id not in settings.extension_ids:
        raise YouTubeAccessError("setup", "Add this extension ID to CHANNELMIND_EXTENSION_IDS on the API.")
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    redirect = f"https://{extension_id}.chromiumapp.org/oauth2"
    get_export_store().put("oauth:" + state, {
        "workspace_id": identity.workspace_id,
        "expires_at": identity.expires_at,
        "redirect_uri": redirect,
        "verifier": verifier,
    }, min(time.time() + 600, identity.expires_at))
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return {
        "state": state,
        "authorization_url": "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
            "client_id": settings.google_client_id,
            "redirect_uri": redirect,
            "response_type": "code",
            "scope": SCOPE,
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }),
    }


def token_request(data):
    try:
        response = requests.post(TOKEN_URL, data=data, timeout=(10, 30), allow_redirects=False)
        if response.status_code != 200:
            raise YouTubeAccessError("authorization", "Google authorization expired or was declined. Connect again.")
        return response.json()
    except requests.RequestException:
        raise YouTubeAccessError("temporary", "Google is temporarily unreachable. Please retry.") from None


def finish_authorization(identity, state, code):
    store = get_export_store()
    pending = store.get("oauth:" + state)
    if not pending or pending["workspace_id"] != identity.workspace_id:
        raise YouTubeAccessError("authorization", "Authorization session expired or does not match this workspace.")
    pending = store.get("oauth:" + state, consume=True)
    if not pending:
        raise YouTubeAccessError("authorization", "Authorization was already completed.")
    settings = get_settings()
    tokens = token_request({
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": pending["redirect_uri"],
        "code_verifier": pending["verifier"],
    })
    if SCOPE not in tokens.get("scope", "").split():
        raise YouTubeAccessError("authorization", "Caption access was not granted. Connect again and allow the requested access.")
    tokens["access_expires_at"] = time.time() + int(tokens.get("expires_in", 3600))
    tokens["expires_at"] = identity.expires_at
    store.put("grant:" + identity.workspace_id, tokens, identity.expires_at)


class AuthorizedYouTube:
    def __init__(self, workspace_id):
        self.workspace_id = workspace_id
        self.store = get_export_store()
        self.settings = get_settings()
        self.session = requests.Session()

    def access_token(self):
        key = "grant:" + self.workspace_id
        grant = self.store.get(key)
        if not grant:
            raise YouTubeAccessError("authorization", "Connect your YouTube account to continue.")
        if grant["access_expires_at"] < time.time() + 60:
            if not grant.get("refresh_token"):
                raise YouTubeAccessError("authorization", "Reconnect YouTube to continue this export.")
            renewed = token_request({
                "client_id": self.settings.google_client_id,
                "client_secret": self.settings.google_client_secret,
                "refresh_token": grant["refresh_token"],
                "grant_type": "refresh_token",
            })
            grant.update(renewed)
            grant["access_expires_at"] = time.time() + int(renewed.get("expires_in", 3600))
            self.store.put(key, grant, grant["expires_at"])
        return grant["access_token"]

    def get(self, resource, params, *, text=False):
        # Resource names are code-owned. No user-provided URLs are fetched.
        for attempt in range(3):
            try:
                response = self.session.get(
                    API_URL + resource, params=params,
                    headers={"Authorization": "Bearer " + self.access_token()},
                    timeout=(10, 30), allow_redirects=False,
                )
            except requests.RequestException:
                if attempt == 2:
                    raise YouTubeAccessError("temporary", "YouTube is temporarily unreachable; resume to retry.") from None
                time.sleep(2 ** attempt)
                continue
            if response.status_code == 200:
                return response.text if text else response.json()
            try:
                reason = response.json().get("error", {}).get("errors", [{}])[0].get("reason", "")
            except (ValueError, IndexError, AttributeError):
                reason = ""
            if reason in {"quotaExceeded", "dailyLimitExceeded"}:
                raise YouTubeAccessError("quota", "YouTube API quota is exhausted. Saved progress is available; resume after quota resets.")
            if response.status_code == 401:
                raise YouTubeAccessError("authorization", "YouTube authorization needs renewing. Reconnect, then resume.")
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise YouTubeAccessError("temporary", "YouTube is busy; progress is saved. Resume shortly.")
            if response.status_code == 403:
                raise YouTubeAccessError("forbidden", "This account cannot download captions for this video.")
            if response.status_code == 404:
                raise YouTubeAccessError("unavailable", "The video or caption track is unavailable.")
            raise YouTubeAccessError("unavailable", "YouTube could not provide this caption track.")

    def channels(self):
        items, page = [], None
        while True:
            params = {"part": "snippet,contentDetails", "mine": "true", "maxResults": 50}
            if page:
                params["pageToken"] = page
            response = self.get("channels", params)
            for item in response.get("items", []):
                items.append({
                    "id": item["id"], "title": item["snippet"]["title"],
                    "uploads": item["contentDetails"]["relatedPlaylists"]["uploads"],
                    "handle": item["snippet"].get("customUrl", ""),
                })
            page = response.get("nextPageToken")
            if not page:
                return items

    def video_page(self, playlist_id, page=None):
        params = {"part": "snippet,contentDetails", "playlistId": playlist_id, "maxResults": 50}
        if page:
            params["pageToken"] = page
        return self.get("playlistItems", params)

    def caption(self, video_id, language=""):
        tracks = self.get("captions", {"part": "snippet", "videoId": video_id}).get("items", [])
        tracks = [t for t in tracks if t.get("snippet", {}).get("status") == "serving"]
        tracks.sort(key=lambda t: (
            bool(language) and t["snippet"].get("language") != language,
            t["snippet"].get("trackKind") == "ASR",
            t["snippet"].get("language") != "en",
        ))
        for track in tracks:
            try:
                content = self.get("captions/" + track["id"], {"tfmt": "vtt"}, text=True)
                return content, track["snippet"].get("language", ""), track["snippet"].get("trackKind", "standard")
            except YouTubeAccessError as error:
                if error.reason not in {"forbidden", "unavailable"}:
                    raise
        reason = "forbidden" if tracks else "no_captions"
        raise YouTubeAccessError(reason, "No downloadable caption track is available for this video.")

    def disconnect(self):
        grant = self.store.get("grant:" + self.workspace_id, consume=True)
        if grant:
            try:
                self.session.post("https://oauth2.googleapis.com/revoke", data={
                    "token": grant.get("refresh_token") or grant["access_token"],
                }, timeout=(10, 15), allow_redirects=False)
            except requests.RequestException:
                pass  # Local deletion still succeeds; no credential is retained.
