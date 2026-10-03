import sys
import os

# Add project root to path so we can import src and config
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import streamlit as st
from src.extractor import YouTubeExtractor
from src.rag import YouTubeRAG
from src.utils import get_cached_channels, log_exception_safely

st.set_page_config(
    page_title="YouTube RAG Intelligence Agent",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for modern UI
st.markdown("""
<style>
    /* Main container styling */
    .main {
        background-color: #0e1117;
    }

    /* Header gradient banner */
    .hero-container {
        background: linear-gradient(135deg, #1e1e2f 0%, #0d1117 100%);
        padding: 1.5rem 2rem;
        border-radius: 12px;
        border: 1px solid #2e3440;
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);
        margin-bottom: 1.5rem;
    }

    .hero-title {
        background: linear-gradient(90deg, #ff4b4b, #ff7676, #ff8c00);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        font-weight: 800;
        font-size: 2.2rem;
        margin-bottom: 0.3rem;
    }

    .hero-subtitle {
        color: #8892b0;
        font-size: 1.05rem;
    }

    /* Metric cards */
    div[data-testid="stMetric"] {
        background-color: #161b22;
        border: 1px solid #30363d;
        padding: 0.8rem 1rem;
        border-radius: 8px;
    }

    /* Sidebar custom header */
    .sidebar-header {
        font-size: 1.1rem;
        font-weight: 700;
        color: #e6edf3;
        margin-top: 1rem;
        margin-bottom: 0.5rem;
        border-bottom: 1px solid #30363d;
        padding-bottom: 0.3rem;
    }

    /* Badge styling */
    .cache-badge {
        background-color: #238636;
        color: white;
        padding: 2px 8px;
        border-radius: 12px;
        font-size: 0.8rem;
        font-weight: 600;
    }
    .live-badge {
        background-color: #1f6feb;
        color: white;
        padding: 2px 8px;
        border-radius: 12px;
        font-size: 0.8rem;
        font-weight: 600;
    }
</style>
""", unsafe_allow_html=True)

# Session state initialization
if "rag" not in st.session_state:
    st.session_state.rag = None
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "processed" not in st.session_state:
    st.session_state.processed = False
if "channel_info" not in st.session_state:
    st.session_state.channel_info = {}
if "channel_url_input" not in st.session_state:
    st.session_state.channel_url_input = ""


def queue_cached_channel(channel_id: str) -> None:
    """Update the visible channel field and process that cache on the next run."""
    st.session_state.channel_url_input = channel_id
    st.session_state.pending_cached_channel = channel_id


def main():
    # Hero Header Banner
    st.markdown("""
        <div class="hero-container">
            <div class="hero-title">🎬 YouTube RAG Intelligence Agent</div>
            <div class="hero-subtitle">Conversational AI over any YouTube channel transcript with precise video timestamp citations</div>
        </div>
    """, unsafe_allow_html=True)

    # Sidebar
    with st.sidebar:
        st.markdown('<div class="sidebar-header">📺 Channel Ingestion</div>', unsafe_allow_html=True)

        channel_url = st.text_input(
            "YouTube Channel URL / Handle",
            placeholder="https://www.youtube.com/@Fireship",
            help="Paste any YouTube channel link, @handle, or channel ID",
            key="channel_url_input",
        )

        max_videos = st.slider("Max Videos to Process", 5, 50, 10, help="Controls how many recent videos to pull transcripts from")

        use_cache = st.checkbox("Use disk cache if available", value=True, help="Loads saved transcripts instantly if previously fetched")

        c1, c2 = st.columns(2)
        with c1:
            process_btn = st.button("🚀 Process", use_container_width=True, type="primary")
        with c2:
            clear_btn = st.button("🗑️ Reset", use_container_width=True)

        # Quick Cache Selector
        cached_list = get_cached_channels()
        if cached_list:
            st.markdown('<div class="sidebar-header">💾 Cached Channels</div>', unsafe_allow_html=True)
            for c_info in cached_list:
                col_a, col_b = st.columns([3, 1])
                with col_a:
                    st.caption(f"**{c_info['channel_id']}** ({c_info['video_count']} vids)")
                with col_b:
                    st.button(
                        "Load",
                        key=f"cache_{c_info['channel_id']}",
                        use_container_width=True,
                        on_click=queue_cached_channel,
                        args=(c_info["channel_id"],),
                    )

        st.markdown('<div class="sidebar-header">⚙️ LLM Config</div>', unsafe_allow_html=True)
        provider = st.selectbox("Provider", ["openrouter", "groq", "grok", "nvidia"], index=0)

        if st.session_state.processed:
            st.success("✅ Knowledge Base Ready")

    pending_cached_channel = st.session_state.pop("pending_cached_channel", None)
    if pending_cached_channel:
        channel_url = pending_cached_channel
        process_btn = True

    # Reset Action
    if clear_btn:
        if st.session_state.rag:
            try:
                st.session_state.rag.clear_knowledge_base()
            except Exception as error:
                log_exception_safely("Knowledge-base reset", error)
        st.session_state.rag = None
        st.session_state.processed = False
        st.session_state.chat_history = []
        st.session_state.channel_info = {}
        st.rerun()

    # Process Action
    if process_btn and channel_url:
        progress_bar = st.progress(0, text="Initializing channel extraction...")
        status_box = st.empty()

        try:
            extractor = YouTubeExtractor()
            transcripts = []
            is_cached = False

            for step in extractor.process_channel_generator(channel_url, max_videos, use_cache=use_cache):
                idx, total, title, current_transcripts, cached = step
                transcripts = current_transcripts
                is_cached = cached
                pct = int((idx / total) * 100) if total > 0 else 100
                progress_bar.progress(pct, text=f"Processing video {idx}/{total}: {title[:40]}...")

            if not transcripts:
                status_box.error("❌ No transcripts found. Videos may have auto-captions disabled.")
                return

            status_box.info("🧠 Building vector embeddings and RAG index...")
            st.session_state.rag = YouTubeRAG(provider=provider)
            st.session_state.rag.ingest_transcripts(transcripts)

            st.session_state.processed = True
            st.session_state.channel_info = {
                "url": channel_url,
                "video_count": len(transcripts),
                "is_cached": is_cached,
                "provider": provider
            }
            progress_bar.empty()
            status_box.empty()
            st.rerun()

        except Exception as error:
            progress_bar.empty()
            log_exception_safely("Channel processing", error)
            status_box.error(
                "Unable to process that channel. Verify the channel and server "
                "configuration, then try again."
            )

    # Main Area: Metrics & Chat
    if not st.session_state.processed:
        st.info("👈 Enter a YouTube channel URL in the sidebar and click **Process** to build your knowledge base.")

        # Example prompt cards
        st.markdown("### 💡 What you can ask:")
        col1, col2, col3 = st.columns(3)
        with col1:
            st.markdown("📌 **Summaries**\n*\"What are the top 3 topics discussed across these videos?\"*")
        with col2:
            st.markdown("🔍 **Specific Tools/Tech**\n*\"How does the author explain Docker or FastAPI?\"*")
        with col3:
            st.markdown("⏱️ **Timestamps**\n*\"At what point in the video is vector search covered?\"*")
        return

    # Metrics Summary Row
    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.metric("Videos Ingested", st.session_state.channel_info.get("video_count", 0))
    with m2:
        badge = '<span class="cache-badge">Disk Cache</span>' if st.session_state.channel_info.get("is_cached") else '<span class="live-badge">Live Extract</span>'
        st.markdown(f"**Source Mode**<br>{badge}", unsafe_allow_html=True)
    with m3:
        st.metric("LLM Provider", st.session_state.channel_info.get("provider", "openrouter").upper())
    with m4:
        st.metric("Conversation Turns", len(st.session_state.chat_history) // 2)

    st.divider()

    # Chat History Render
    for msg in st.session_state.chat_history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("usage"):
                usage = msg["usage"]
                st.caption(
                    f"Tokens: {usage.get('input_tokens', 0)} in · "
                    f"{usage.get('output_tokens', 0)} out · "
                    f"{usage.get('total_tokens', 0)} total"
                )
            if msg.get("sources"):
                with st.expander("📚 Verified Timestamp Sources"):
                    for src in msg["sources"]:
                        st.markdown(f"- 🎬 **[{src['title']}]({src['url']})** at `{src['timestamp']}`")

    # Chat Input Box
    question = st.chat_input("Ask a question about the channel's videos...")

    if question and st.session_state.rag:
        # Display user message
        st.session_state.chat_history.append({"role": "user", "content": question})
        with st.chat_message("user"):
            st.markdown(question)

        # Assistant response generation
        with st.chat_message("assistant"):
            with st.spinner("Searching video context & generating answer..."):
                try:
                    result = st.session_state.rag.ask(
                        question,
                        # The current question is already passed separately.
                        chat_history=st.session_state.chat_history[:-1]
                    )

                    st.markdown(result["answer"])

                    if result.get("usage"):
                        usage = result["usage"]
                        st.caption(
                            f"Tokens: {usage.get('input_tokens', 0)} in · "
                            f"{usage.get('output_tokens', 0)} out · "
                            f"{usage.get('total_tokens', 0)} total"
                        )

                    if result["sources"]:
                        with st.expander("📚 Verified Timestamp Sources"):
                            for src in result["sources"]:
                                st.markdown(f"- 🎬 **[{src['title']}]({src['url']})** at `{src['timestamp']}`")

                    st.session_state.chat_history.append({
                        "role": "assistant",
                        "content": result["answer"],
                        "sources": result["sources"],
                        "usage": result.get("usage", {})
                    })
                    st.rerun()

                except Exception as error:
                    log_exception_safely("Answer generation", error)
                    st.error("Unable to generate an answer right now. Please try again.")


if __name__ == "__main__":
    main()
