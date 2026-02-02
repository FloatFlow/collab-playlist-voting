import streamlit as st
import streamlit.components.v1 as components
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import json
import os
import math
import re

# --- Configuration ---
TRACKS_FILE = 'tracks.json'
VOTES_FILE = 'votes.json'
HISTORY_FILE = 'history.json'
PAGE_SIZE = 10  # Number of tracks per page

# --- Data Persistence ---
def get_db():
    """Initialize Firestore client if secrets are available."""
    if "firestore" in st.secrets:
        try:
            from google.cloud import firestore
            from google.oauth2 import service_account
            
            # Load credentials from secrets
            # strict=False allows control characters (newlines) in the JSON string
            key_dict = json.loads(st.secrets["firestore"]["textkey"], strict=False)
            creds = service_account.Credentials.from_service_account_info(key_dict)
            return firestore.Client(credentials=creds)
        except Exception as e:
            st.error(f"Firestore Connection Error: {e}")
    return None

def load_json(filepath, default_value):
    """Load data from Firestore (if configured) or local JSON."""
    data = None
    
    # 1. Try Firestore
    db = get_db()
    if db:
        try:
            doc_id = os.path.splitext(filepath)[0]  # e.g., 'tracks.json' -> 'tracks'
            doc = db.collection("playlist_voting").document(doc_id).get()
            if doc.exists:
                data = doc.to_dict().get("data")
        except Exception as e:
            st.warning(f"Firestore read error: {e}. Falling back to local file.")

    # 2. Fallback to Local File (if Firestore is missing the doc or returned None)
    # This ensures the static 'tracks.json' is used as the seed when the DB is empty.
    if data is None:
        if os.path.exists(filepath):
            try:
                with open(filepath, 'r') as f:
                    data = json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
                
    return data if data is not None else default_value

def save_json(filepath, data):
    """Save data to Firestore (if configured) or local JSON."""
    # 1. Try Firestore
    db = get_db()
    if db:
        try:
            doc_id = os.path.splitext(filepath)[0]
            db.collection("playlist_voting").document(doc_id).set({"data": data})
            return
        except Exception as e:
            st.error(f"Firestore save error: {e}")
            # Fall through to local save as backup

    # 2. Fallback to Local File
    with open(filepath, 'w') as f:
        json.dump(data, f, indent=2)

# --- Spotify API & Logic ---
def get_spotify_client():
    # Try to get secrets safely
    try:
        # Check if secrets are available (Streamlit Cloud or local .streamlit/secrets.toml)
        if 'SPOTIPY_CLIENT_ID' in st.secrets:
            client_id = st.secrets['SPOTIPY_CLIENT_ID']
            client_secret = st.secrets['SPOTIPY_CLIENT_SECRET']
            redirect_uri = st.secrets.get('SPOTIPY_REDIRECT_URI', 'http://localhost:8501/')
            
            auth_manager = SpotifyOAuth(
                client_id=client_id,
                client_secret=client_secret,
                redirect_uri=redirect_uri,
                scope="playlist-read-collaborative playlist-modify-public",
                open_browser=False,
                cache_handler=spotipy.cache_handler.CacheFileHandler(cache_path=".spotify_cache")
            )
            return spotipy.Spotify(auth_manager=auth_manager)
    except (FileNotFoundError, KeyError, AttributeError):
        pass
    return None

def fetch_playlist_snapshot(input_text):
    sp = get_spotify_client()
    
    # CASE 1: API is available AND input looks like a playlist URL
    if sp and "playlist" in input_text and "spotify.com" in input_text:
        try:
            if "spotify.com" in input_text:
                playlist_id = input_text.split("/")[-1].split("?")[0]
            else:
                playlist_id = input_text
                
            results = sp.playlist_items(playlist_id)
            tracks = results['items']
            while results['next']:
                results = sp.next(results)
                tracks.extend(results['items'])
                
            snapshot = []
            for item in tracks:
                track = item['track']
                if track:
                    artist_name = track['artists'][0]['name'] if track['artists'] else "Unknown"
                    snapshot.append({
                        "id": track['id'],
                        "name": track['name'],
                        "artist": artist_name,
                        "embed_url": f"https://open.spotify.com/embed/track/{track['id']}"
                    })
            return snapshot
        except Exception as e:
            st.error(f"API Error: {e}. Falling back to manual parsing.")

    # CASE 2: No API or Manual Input (Fallback)
    # Regex matches 'track/' or 'track:' followed by exactly 22 alphanumeric characters.
    # Works for full URLs, Embed codes, or URI strings.
    track_ids = re.findall(r'track[:/]([a-zA-Z0-9]{22})', input_text)
    # Remove duplicates while preserving order
    seen = set()
    unique_ids = [x for x in track_ids if not (x in seen or seen.add(x))]
    
    if not unique_ids:
        raise ValueError("No valid Spotify Track IDs found. Please paste a Playlist URL (if API configured) or a list of Spotify Links.")

    snapshot = []
    for tid in unique_ids:
        # Without API, we cannot fetch names, but the Embed player will show them.
        snapshot.append({
            "id": tid,
            "name": "(Metadata Unavailable)",
            "artist": "",
            "embed_url": f"https://open.spotify.com/embed/track/{tid}"
        })
        
    return snapshot

# --- UI Components ---
def render_track_row(track, on_vote_change=None):
    # Layout: Text (3) | Player (4) | Checkbox (1)
    c1, c2, c3 = st.columns([3, 4, 1])
    
    with c1:
        st.subheader(track['name'])
        if track['artist']:
            st.write(f"**Artist:** {track['artist']}")
        # Add a direct link as a fallback
        st.caption(f"[Open in Spotify](https://open.spotify.com/track/{track['id']})")
    
    with c2:
        # We reconstruct the URL to ensure it has the correct parameters for embedding
        embed_url = f"https://open.spotify.com/embed/track/{track['id']}?utm_source=generator"
        
        # Use st.components.v1.html to render the iframe in a sandboxed container
        # This is more reliable for 'encrypted-media' permissions than st.markdown
        st.components.v1.html(
            f"""
            <iframe style="border-radius:12px" 
                src="{embed_url}" 
                width="100%" 
                height="152" 
                frameBorder="0" 
                allowfullscreen="" 
                allow="autoplay; clipboard-write; encrypted-media; fullscreen; picture-in-picture" 
                loading="lazy">
            </iframe>
            """,
            height=152
        )
        
    with c3:
        # Checkbox state management
        is_selected = track['id'] in st.session_state['votes']
        
        # Callback to update session state immediately
        def update_vote():
            if st.session_state[f"chk_{track['id']}"]:
                st.session_state['votes'].add(track['id'])
            else:
                st.session_state['votes'].discard(track['id'])
            
            # Trigger auto-save if callback provided
            if on_vote_change:
                on_vote_change()
        
        # Spacer to align checkbox with the taller player
        st.write("")
        st.write("")
        st.checkbox(
            "Keep", 
            value=is_selected, 
            key=f"chk_{track['id']}", 
            on_change=update_vote
        )

# --- Main Application ---
def main():
    st.set_page_config(page_title="Spotify Approval Voting", layout="wide")
    st.title("🎵 Spotify Playlist Pruning")

    # Initialize Session State
    if 'votes' not in st.session_state:
        st.session_state['votes'] = set()
    
    # Load Data
    all_votes = load_json(VOTES_FILE, {})
    snapshot_tracks = load_json(TRACKS_FILE, [])
    history = load_json(HISTORY_FILE, [])

    # --- Calculations ---
    # Calculate vote counts for display and pruning
    vote_counts = {}
    for user, user_votes in all_votes.items():
        for tid in user_votes:
            vote_counts[tid] = vote_counts.get(tid, 0) + 1
            
    # Attach votes to track objects for sorting (creates a temporary list)
    tracks_with_votes = []
    for t in snapshot_tracks:
        t_copy = t.copy()
        t_copy['votes'] = vote_counts.get(t['id'], 0)
        tracks_with_votes.append(t_copy)

    # --- Sidebar: Auth & Admin ---
    with st.sidebar:
        st.header("User Login")
        username = st.text_input("Enter your name to vote").strip()
        
        st.markdown("---")
        st.header("Admin Controls")
        with st.expander("Admin Tools"):
            admin_password = st.text_input("Admin Password", type="password")
            if admin_password == "admin123":  # Replace with env var in production
                st.write("### 1. New Round (Import)")
                st.caption("Start from scratch with a new playlist.")
                new_playlist_input = st.text_area("Playlist URL / Links")
                
                if st.button("Fetch Tracks & Reset"):
                    if new_playlist_input:
                        with st.spinner("Processing..."):
                            try:
                                # Archive if data exists
                                if snapshot_tracks:
                                    history.append({
                                        "round": len(history) + 1,
                                        "tracks": snapshot_tracks,
                                        "votes": all_votes,
                                        "final_counts": vote_counts
                                    })
                                    save_json(HISTORY_FILE, history)

                                data = fetch_playlist_snapshot(new_playlist_input)
                                save_json(TRACKS_FILE, data)
                                save_json(VOTES_FILE, {}) # Reset votes for new tracks
                                st.success(f"Snapshot saved! {len(data)} tracks loaded.")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Error: {e}")

                st.markdown("---")
                st.write("### 2. Next Round (Prune)")
                st.caption("Keep Top N tracks and reset votes.")
                
                # Sort for pruning logic
                sorted_by_votes = sorted(tracks_with_votes, key=lambda x: x['votes'], reverse=True)
                max_val = len(snapshot_tracks) if snapshot_tracks else 1
                top_n = st.number_input("Keep Top N", min_value=1, max_value=max_val, value=min(10, max_val))
                
                if st.button("Prune & Advance"):
                    if snapshot_tracks:
                        # 1. Archive current state
                        history.append({
                            "round": len(history) + 1,
                            "tracks": snapshot_tracks,
                            "votes": all_votes,
                            "final_counts": vote_counts
                        })
                        save_json(HISTORY_FILE, history)
                        
                        # 2. Slice top N tracks (clean metadata only)
                        keep_ids = {t['id'] for t in sorted_by_votes[:top_n]}
                        next_round_tracks = [t for t in snapshot_tracks if t['id'] in keep_ids]
                        
                        # 3. Save and Reset
                        save_json(TRACKS_FILE, next_round_tracks)
                        save_json(VOTES_FILE, {}) # Clear votes for the next round
                        st.success(f"Advanced to next round with top {len(next_round_tracks)} tracks!")
                        st.rerun()

    # --- Main Logic ---
    if not username:
        st.info("👈 Please enter your name in the sidebar to start voting.")
        return

    # Tabs for separation of concerns
    tab_vote, tab_results = st.tabs(["🗳️ Vote & Listen", "📊 Results"])

    # --- TAB 1: Voting ---
    with tab_vote:
        if not snapshot_tracks:
            st.warning("No voting round is currently active. Ask an admin to start a round.")
        else:
            # Load existing votes for user if first load of session
            if username in all_votes and 'loaded_user' not in st.session_state:
                st.session_state['votes'] = set(all_votes[username])
                st.session_state['loaded_user'] = username
                st.toast(f"Loaded {len(st.session_state['votes'])} previous votes.")
            elif 'loaded_user' not in st.session_state:
                st.session_state['loaded_user'] = username

            # Handle User Switching
            if st.session_state['loaded_user'] != username:
                st.session_state['votes'] = set(all_votes.get(username, []))
                st.session_state['loaded_user'] = username
                st.rerun()

            # --- Auto-Save Logic ---
            def auto_save():
                all_votes[username] = list(st.session_state['votes'])
                save_json(VOTES_FILE, all_votes)

            st.caption(f"Showing all {len(snapshot_tracks)} tracks. ✅ Votes are saved automatically.")
            
            # Render List (No Pagination)
            for track in snapshot_tracks:
                render_track_row(track, on_vote_change=auto_save)

    # --- TAB 2: Results ---
    with tab_results:
        st.header("Current Round Standings")
        if not snapshot_tracks:
            st.info("No active round.")
        else:
            # Sort tracks by votes descending
            sorted_tracks = sorted(tracks_with_votes, key=lambda x: x['votes'], reverse=True)
            total_voters = len(all_votes) if all_votes else 1 # avoid div/0
            
            for t in sorted_tracks:
                col_meta, col_bar = st.columns([2, 3])
                with col_meta:
                    st.write(f"**{t['name']}**")
                    st.caption(t['artist'])
                with col_bar:
                    vote_count = t['votes']
                    pct = vote_count / total_voters
                    st.progress(pct)
                    st.caption(f"{vote_count} votes ({int(pct*100)}%)")
                st.markdown("---")
        
        if history:
            st.header("📜 History")
            st.markdown("---")
            for record in reversed(history):
                with st.expander(f"Round {record['round']} Results ({len(record['tracks'])} tracks)"):
                    h_counts = record.get('final_counts', {})
                    h_tracks = record['tracks']
                    # Sort by historical counts
                    h_sorted = sorted(h_tracks, key=lambda x: h_counts.get(x['id'], 0), reverse=True)
                    
                    for t in h_sorted:
                        vc = h_counts.get(t['id'], 0)
                        st.write(f"**{vc}** - {t['name']} - _{t['artist']}_")
if __name__ == "__main__":
    main()