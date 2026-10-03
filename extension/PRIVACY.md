# ChannelMind extension data handling

The extension reads the URL of the tab where you click its icon, only to recognize a YouTube channel. It does not read your browsing history, cookies, page contents, email address or Google password. No YouTube content script is installed.

You choose a ChannelMind API server. The extension stores its address, a signed workspace token and the most recent export ID locally in Chrome. The optional API host permission is requested when you start a public export, connect Google, or save the server address. `identity` opens Google consent only for the optional owned-channel mode; `alarms` checks export completion and `downloads` saves the resulting bundle.

For a public-channel export, the API receives the channel URL or handle, fetches public upload metadata and accessible transcripts, and retains the bundle until its expiration. You do not sign in to the channel owner’s account. Public transcript requests can be blocked or rate limited by YouTube.

Google authorization codes are exchanged by the API. Google access/refresh tokens stay encrypted on the API's export store; they are not returned to Chrome or the website. The API uses them to read your channel catalog and caption tracks. Local development stores this data in process memory. Production uses Redis with expiration and encrypted records. Expiration follows the workspace lifetime, which defaults to 24 hours; export retention can be shorter.

The server can process non-public videos that YouTube exposes to the authorized account. Their text is included in your private export. Export download and status routes require the workspace token. Downloaded files remain on your computer until you delete them.

If you choose **Use in ChannelMind**, transcript text is embedded in a separate search workspace. You can also upload a downloaded ZIP or JSON into a new website workspace; that import sends the transcript text to the configured API for indexing. When you ask questions, selected passages are sent to the app's configured language-model provider to generate an answer. Downloading an export by itself does not call that provider.

**Disconnect account** deletes the stored grant and attempts to revoke it with Google. You can also revoke access in your Google account's third-party connections settings. Disconnecting does not delete exports already downloaded to your computer. Server export records expire automatically. Expired Qdrant indexes are removed by the deployment's scheduled cleanup; local Chroma data needs operator cleanup. No analytics are included in this extension.

This describes the repository implementation. Before publishing, the operator must supply their contact details, actual hosting/provider information and retention settings in a public privacy policy.
