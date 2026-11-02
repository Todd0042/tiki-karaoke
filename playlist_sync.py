import os
import json
import datetime
from pathlib import Path
from typing import Optional, Dict, Any

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import DATA_DIR

SCOPES = [
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl"
]

TOKEN_PATH = DATA_DIR / "youtube_oauth_token.json"
CLIENT_SECRETS_PATH = DATA_DIR / "client_secrets.json"

playlist_cache: Dict[str, str] = {}

def get_credentials() -> Optional[Credentials]:
    if not TOKEN_PATH.exists():
        return None
    try:
        data = json.loads(TOKEN_PATH.read_text())
        creds = Credentials.from_authorized_user_info(data, SCOPES)
        return creds
    except Exception as e:
        print(f"[YouTube Auth] Credential load error: {e}")
        return None

def is_authenticated() -> bool:
    creds = get_credentials()
    return creds is not None and creds.valid

def get_auth_url(redirect_uri: str) -> Optional[str]:
    if not CLIENT_SECRETS_PATH.exists():
        return None
    try:
        flow = Flow.from_client_secrets_file(
            str(CLIENT_SECRETS_PATH),
            scopes=SCOPES,
            redirect_uri=redirect_uri
        )
        auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
        return auth_url
    except Exception as e:
        print(f"[YouTube Auth] Error creating flow: {e}")
        return None

def exchange_code(code: str, redirect_uri: str) -> bool:
    if not CLIENT_SECRETS_PATH.exists():
        return False
    try:
        flow = Flow.from_client_secrets_file(
            str(CLIENT_SECRETS_PATH),
            scopes=SCOPES,
            redirect_uri=redirect_uri
        )
        flow.fetch_token(code=code)
        creds = flow.credentials
        TOKEN_PATH.write_text(creds.to_json())
        return True
    except Exception as e:
        print(f"[YouTube Auth] Code exchange error: {e}")
        return False

def get_youtube_service():
    creds = get_credentials()
    if not creds:
        return None
    return build("youtube", "v3", credentials=creds)

def get_or_create_playlist(youtube, playlist_title: str) -> Optional[str]:
    if playlist_title in playlist_cache:
        return playlist_cache[playlist_title]

    try:
        request = youtube.playlists().list(
            part="snippet",
            mine=True,
            maxResults=50
        )
        response = request.execute()
        for item in response.get("items", []):
            if item["snippet"]["title"].lower() == playlist_title.lower():
                p_id = item["id"]
                playlist_cache[playlist_title] = p_id
                return p_id

        # Not found, create new playlist
        insert_req = youtube.playlists().insert(
            part="snippet,status",
            body={
                "snippet": {
                    "title": playlist_title,
                    "description": "Auto-synced from Tiki Karaoke Lounge party session."
                },
                "status": {
                    "privacyStatus": "unlisted"
                }
            }
        )
        insert_res = insert_req.execute()
        p_id = insert_res["id"]
        playlist_cache[playlist_title] = p_id
        return p_id
    except Exception as e:
        print(f"[YouTube Sync] Playlist get/create error: {e}")
        return None

def is_video_in_playlist(youtube, playlist_id: str, video_id: str) -> bool:
    try:
        req = youtube.playlistItems().list(
            part="snippet",
            playlistId=playlist_id,
            maxResults=50
        )
        res = req.execute()
        for item in res.get("items", []):
            if item["snippet"]["resourceId"]["videoId"] == video_id:
                return True
        return False
    except Exception:
        return False

def sync_played_song_to_youtube(singer_name: str, video_id: str) -> Optional[str]:
    youtube = get_youtube_service()
    if not youtube:
        return None

    clean_singer = singer_name.strip() if singer_name.strip() else "Guest"
    playlist_title = f"Karaoke - {clean_singer}"

    try:
        playlist_id = get_or_create_playlist(youtube, playlist_title)
        if not playlist_id:
            return None

        # Check for duplicate
        if is_video_in_playlist(youtube, playlist_id, video_id):
            return playlist_id

        youtube.playlistItems().insert(
            part="snippet",
            body={
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {
                        "kind": "youtube#video",
                        "videoId": video_id
                    }
                }
            }
        ).execute()

        # Also add to party master playlist for today
        today_title = f"Karaoke Party - {datetime.date.today().strftime('%b %d, %Y')}"
        master_id = get_or_create_playlist(youtube, today_title)
        if master_id and not is_video_in_playlist(youtube, master_id, video_id):
            youtube.playlistItems().insert(
                part="snippet",
                body={
                    "snippet": {
                        "playlistId": master_id,
                        "resourceId": {
                            "kind": "youtube#video",
                            "videoId": video_id
                        }
                    }
                }
            ).execute()

        return playlist_id
    except Exception as e:
        print(f"[YouTube Sync] Sync failed for video {video_id}: {e}")
        return None
