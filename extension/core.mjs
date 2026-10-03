export function serverOrigin(value) {
  const url = new URL(value);
  const local = ["localhost", "127.0.0.1"].includes(url.hostname);
  if (url.protocol !== "https:" && !(local && url.protocol === "http:")) {
    throw new Error("Use an HTTPS address, or HTTP on localhost for development.");
  }
  if (url.username || url.password || url.search || url.hash || url.pathname !== "/") {
    throw new Error("Enter a server origin only, for example https://api.example.com.");
  }
  return url.origin;
}

export function channelHint(value) {
  try {
    const url = new URL(value);
    if (!["youtube.com", "www.youtube.com", "m.youtube.com"].includes(url.hostname)) return "";
    const parts = url.pathname.split("/").filter(Boolean);
    if (parts[0]?.startsWith("@")) return decodeURIComponent(parts[0]);
    if (parts[0] === "channel" && /^UC[A-Za-z0-9_-]{22}$/.test(parts[1] || "")) return parts[1];
  } catch { /* A non-YouTube tab is fine. */ }
  return "";
}

export function playlistHint(value) {
  try {
    const url = new URL(value);
    if (!["youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"].includes(url.hostname)) return "";
    if (!["/playlist", "/watch"].includes(url.pathname)) return "";
    const ids = url.searchParams.getAll("list");
    if (ids.length !== 1 || !/^[A-Za-z0-9_-]{10,128}$/.test(ids[0])) return "";
    return `https://www.youtube.com/playlist?list=${ids[0]}`;
  } catch { return ""; }
}

export function hostPermission(value) {
  const url = new URL(serverOrigin(value));
  return `${url.protocol}//${url.hostname}/*`;
}

export function authCode(returnedUrl, redirectUri, expectedState) {
  const returned = new URL(returnedUrl);
  const expected = new URL(redirectUri);
  if (returned.origin !== expected.origin || returned.pathname !== expected.pathname ||
      returned.searchParams.get("state") !== expectedState) {
    throw new Error("Google returned an unexpected authorization response. Please reconnect.");
  }
  if (returned.searchParams.has("error")) throw new Error("Google authorization was cancelled or declined.");
  const code = returned.searchParams.get("code");
  if (!code) throw new Error("Google did not return an authorization code.");
  return code;
}

export function progressLabel(run) {
  if (!run.catalog_complete) return `Discovering videos · ${run.total} found`;
  return `${run.processed} / ${run.total} processed · ${run.exported} transcripts · ${run.skipped} unavailable · ${run.total - run.processed} pending`;
}
