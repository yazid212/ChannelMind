export type SourceScope = "video" | "playlist" | "channel";

export function detectSource(input: string): SourceScope {
  const value = input.trim();
  if (/^@[A-Za-z0-9._-]{1,100}$/.test(value) || /^UC[A-Za-z0-9_-]{22}$/.test(value)) return "channel";
  if (/^[A-Za-z0-9_-]{11}$/.test(value)) return "video";
  let url: URL;
  try { url = new URL(value); } catch { throw new Error("Paste a public YouTube video, playlist, or channel URL."); }
  if (url.protocol !== "https:" || url.username || url.password || url.port) {
    throw new Error("Use an HTTPS YouTube URL without login details or a custom port.");
  }
  const host = url.hostname.toLowerCase();
  if (["youtu.be", "www.youtu.be"].includes(host) && /^\/[A-Za-z0-9_-]{11}\/?$/.test(url.pathname)) return "video";
  if (!["youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"].includes(host)) {
    throw new Error("Use a public YouTube URL.");
  }
  if (["/playlist", "/watch"].includes(url.pathname) && url.searchParams.has("list")) return "playlist";
  if (host === "music.youtube.com") throw new Error("Use a standard YouTube video or channel URL.");
  if (url.pathname === "/watch" || /^\/(shorts|live|embed)\/[A-Za-z0-9_-]{11}\/?$/.test(url.pathname)) return "video";
  if (/^\/@[^/]+(?:\/(?:videos|shorts|streams|playlists))?\/?$/.test(url.pathname)
      || /^\/(channel|c|user)\/[^/]+(?:\/(?:videos|shorts|streams|playlists))?\/?$/.test(url.pathname)) return "channel";
  throw new Error("Paste a public YouTube video, playlist, or channel URL.");
}
