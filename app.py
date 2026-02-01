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
PAGE_SIZE = 10  # Number of tracks per page

# --- Data Persistence ---
def load_json(filepath, default_value):
    if not os.path.exists(filepath):
        return default_value
    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return default_value

def save_json(filepath, data):
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
def render_track_row(track):
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
    if 'page' not in st.session_state:
        st.session_state['page'] = 0
    
    # Load Data
    all_votes = load_json(VOTES_FILE, {})
    snapshot_tracks = load_json(TRACKS_FILE, [])

    # --- Sidebar: Auth & Admin ---
    with st.sidebar:
        st.header("User Login")
        username = st.text_input("Enter your name to vote").strip()
        
        st.markdown("---")
        st.header("Admin Controls")
        with st.expander("Admin Tools"):
            admin_password = st.text_input("Admin Password", type="password")
            if admin_password == "admin123":  # Replace with env var in production
                st.write("**Start New Round**")
                st.caption("Paste a Spotify Playlist URL OR a list of song links (if API is down).")
                new_playlist_input = st.text_area("Input Data")
                
                if st.button("Fetch Tracks & Start Round"):
                    if new_playlist_input:
                        with st.spinner("Processing..."):
                            try:
                                data = fetch_playlist_snapshot(new_playlist_input)
                                save_json(TRACKS_FILE, data)
                                st.success(f"Snapshot saved! {len(data)} tracks loaded.")
                                # Reset pagination
                                st.session_state['page'] = 0
                                st.rerun()
                            except Exception as e:
                                st.error(f"Error: {e}")
                
                st.markdown("---")
                if st.button("Show Current Results"):
                    st.write("**Current Votes:**")
                    st.json(all_votes)

    # --- Main Logic ---
    if not username:
        st.info("👈 Please enter your name in the sidebar to start voting.")
        return

    if not snapshot_tracks:
        st.warning("No voting round is currently active. Ask an admin to start a round.")
        return

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

    # --- Pagination ---
    total_tracks = len(snapshot_tracks)
    total_pages = math.ceil(total_tracks / PAGE_SIZE)
    
    col_prev, col_info, col_next = st.columns([1, 10, 1])
    
    with col_prev:
        if st.button("Previous") and st.session_state['page'] > 0:
            st.session_state['page'] -= 1
            st.rerun()
            
    with col_next:
        if st.button("Next") and st.session_state['page'] < total_pages - 1:
            st.session_state['page'] += 1
            st.rerun()
            
    with col_info:
        st.caption(f"Page {st.session_state['page'] + 1} of {total_pages} | Total Tracks: {total_tracks}")

    # --- Render List ---
    current_page = st.session_state['page']
    start_idx = current_page * PAGE_SIZE
    end_idx = start_idx + PAGE_SIZE
    current_batch = snapshot_tracks[start_idx:end_idx]
    
    for track in current_batch:
        render_track_row(track)

    # --- Submission ---
    st.markdown("---")
    st.write(f"### You have selected {len(st.session_state['votes'])} tracks to keep.")
    
    if st.button("Submit / Update Votes", type="primary"):
        all_votes[username] = list(st.session_state['votes'])
        save_json(VOTES_FILE, all_votes)
        st.balloons()
        st.success("✅ Votes saved successfully! You can close this tab or update votes later.")

if __name__ == "__main__":
    main()