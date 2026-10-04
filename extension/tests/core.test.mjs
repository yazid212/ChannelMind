import test from "node:test";
import assert from "node:assert/strict";
import { authCode, channelHint, hostPermission, playlistHint, progressLabel, serverOrigin } from "../core.mjs";

test("server settings allow HTTPS and local development without URL credentials", () => {
  assert.equal(serverOrigin("https://api.example.com"), "https://api.example.com");
  assert.equal(serverOrigin("http://127.0.0.1:8000"), "http://127.0.0.1:8000");
  assert.equal(hostPermission("http://127.0.0.1:8000"), "http://127.0.0.1/*");
  for (const url of ["http://public.example", "https://user:secret@api.example.com", "file:///etc/passwd", "https://api.example.com/path"]) {
    assert.throws(() => serverOrigin(url));
  }
});

test("channel detection accepts channel pages and excludes unrelated sites or individual videos", () => {
  assert.equal(channelHint("https://www.youtube.com/@creator/videos"), "@creator");
  assert.equal(channelHint("https://youtube.com/watch?v=aaaaaaaaaaa"), "");
  assert.equal(channelHint("https://youtube.com.evil.example/@creator"), "");
});

test("playlist detection accepts playlist and watch pages with a valid list ID", () => {
  const id = "PL" + "p".repeat(32);
  assert.equal(playlistHint(`https://www.youtube.com/playlist?list=${id}`), `https://www.youtube.com/playlist?list=${id}`);
  assert.equal(playlistHint(`https://youtube.com/watch?v=aaaaaaaaaaa&list=${id}`), `https://www.youtube.com/playlist?list=${id}`);
  assert.equal(playlistHint(`https://youtube.com.evil.example/playlist?list=${id}`), "");
  assert.equal(playlistHint("https://youtube.com/playlist?list=short"), "");
});

test("OAuth completion checks both the extension redirect and state", () => {
  const redirect = "https://" + "a".repeat(32) + ".chromiumapp.org/oauth2";
  assert.equal(authCode(redirect + "?code=abc&state=expected", redirect, "expected"), "abc");
  assert.throws(() => authCode(redirect + "?code=abc&state=other", redirect, "expected"));
  assert.throws(() => authCode("https://evil.example/oauth2?code=abc&state=expected", redirect, "expected"));
  assert.throws(() => authCode(redirect + "?error=access_denied&state=expected", redirect, "expected"));
});

test("coverage label distinguishes transcribed, pending, failed and indexed", () => {
  const label = progressLabel({ catalog_complete: true, coverage: {
    discovered: 200, transcribed: 43, unavailable: 100, failed: 7, pending: 50, indexed: 10,
  } });
  assert.match(label, /200 found/);
  assert.match(label, /43 transcribed/);
  assert.match(label, /7 failed/);
  assert.match(label, /50 pending/);
  assert.match(label, /10 indexed/);
});
