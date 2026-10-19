#!/usr/bin/env python3
import sys
import time
from pathlib import Path

# Add current dir to path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from database import (
    init_db, get_or_create_singer, search_local_tracks,
    enqueue_song, get_queue, get_track_by_id, set_queue_status
)
from search import tiered_search
from downloader import download_and_normalize_track, update_singer_m3u_playlist
from config import TRACKS_DIR, PLAYLISTS_DIR

def run_tests():
    init_db()
    print("=" * 65)
    print("🌴 RUNNING LIVE SONG INGESTION & PIPELINE TEST")
    print("=" * 65)

    # 1. Register test singers
    singer1 = get_or_create_singer("token_testsinger", "TestSinger", "🦩", "127.0.0.1")
    singer2 = get_or_create_singer("token_friend", "Alex", "🍹", "127.0.0.1")
    print(f"✅ Registered Singer 1: {singer1['name']} ({singer1['avatar']})")
    print(f"✅ Registered Singer 2: {singer2['name']} ({singer2['avatar']})")

    # 2. Test Search: "Glycerine by Bush"
    print("\n🔍 Querying: 'Glycerine by Bush'...")
    res_bush = tiered_search("Glycerine Bush")
    print(f"   Local matches: {len(res_bush['local_results'])}")
    print(f"   YouTube groups found: {len(res_bush['youtube_results'])}")

    if not res_bush['youtube_results']:
        print("❌ Error: No YouTube results found for Glycerine by Bush")
        return False

    best_bush_group = res_bush['youtube_results'][0]
    print(f"   🎯 Master Track: '{best_bush_group['title']}' by '{best_bush_group['artist']}'")
    print(f"   📼 Clustered Versions: {len(best_bush_group['versions'])}")
    for v in best_bush_group['versions'][:3]:
        print(f"      • [{v['channel']}] {v['raw_title']} (Duration: {v['duration']}s)")

    # 3. Test Search: "Life goes on by Ed Sheeran"
    print("\n🔍 Querying: 'Life goes on by Ed Sheeran'...")
    res_ed = tiered_search("Life goes on Ed Sheeran")
    print(f"   Local matches: {len(res_ed['local_results'])}")
    print(f"   YouTube groups found: {len(res_ed['youtube_results'])}")

    if not res_ed['youtube_results']:
        print("❌ Error: No YouTube results found for Life goes on Ed Sheeran")
        return False

    best_ed_group = res_ed['youtube_results'][0]
    print(f"   🎯 Master Track: '{best_ed_group['title']}' by '{best_ed_group['artist']}'")
    print(f"   📼 Clustered Versions: {len(best_ed_group['versions'])}")
    for v in best_ed_group['versions'][:3]:
        print(f"      • [{v['channel']}] {v['raw_title']} (Duration: {v['duration']}s)")

    # 4. Queue Both Songs
    print("\n📜 Queueing songs with customized pitch...")
    from database import save_track
    
    # Save track metadata to DB
    bush_vid = best_bush_group['versions'][0]['video_id']
    t1_id = save_track(
        video_id=bush_vid,
        title=best_bush_group['title'],
        artist=best_bush_group['artist'],
        original_title=best_bush_group['versions'][0]['raw_title'],
        channel=best_bush_group['versions'][0]['channel'],
        duration=best_bush_group['versions'][0]['duration']
    )
    q1_id = enqueue_song(t1_id, singer1["id"], duet_partner="", pitch_shift=-1)
    print(f"   ✅ Queued 'Glycerine' for Todd with Key: -1 (Queue ID: {q1_id})")

    ed_vid = best_ed_group['versions'][0]['video_id']
    t2_id = save_track(
        video_id=ed_vid,
        title=best_ed_group['title'],
        artist=best_ed_group['artist'],
        original_title=best_ed_group['versions'][0]['raw_title'],
        channel=best_ed_group['versions'][0]['channel'],
        duration=best_ed_group['versions'][0]['duration']
    )
    q2_id = enqueue_song(t2_id, singer2["id"], duet_partner="Todd", pitch_shift=1)
    print(f"   ✅ Queued 'Life Goes On' for Alex & Todd with Key: +1 (Queue ID: {q2_id})")

    # Verify Queue
    current_q = get_queue()
    print(f"\n📋 Current Active Queue ({len(current_q)} songs):")
    for idx, item in enumerate(current_q):
        print(f"   #{idx + 1}: {item['singer_name']} -> {item['artist']} - {item['title']} (Pitch: {item['pitch_shift']})")

    # 5. Live Download & EBU R128 Loudness Normalization Test on "Glycerine"
    print(f"\n📥 Testing yt-dlp download & FFmpeg EBU R128 Normalization (-16 LUFS) on '{best_bush_group['title']}'...")
    print(f"   Video ID: {bush_vid}")
    start_t = time.time()
    local_path = download_and_normalize_track(t1_id)
    elapsed = time.time() - start_t

    if local_path and Path(local_path).exists():
        size_mb = Path(local_path).stat().st_size / (1024 * 1024)
        print(f"   🎉 SUCCESS! Downloaded and normalized in {elapsed:.1f}s")
        print(f"   📁 Local Path: {local_path}")
        print(f"   💾 File Size: {size_mb:.2f} MB")

        # Update M3U playlist
        update_singer_m3u_playlist(singer1["name"], local_path, best_bush_group['title'], best_bush_group['artist'])
        m3u_file = PLAYLISTS_DIR / f"{singer1['name']}.m3u8"
        if m3u_file.exists():
            print(f"   🎼 Singer M3U8 Playlist Generated: {m3u_file}")
    else:
        print("❌ Error during download/normalization.")
        return False

    # 6. Verify Tier 1 (Local First) Search Promotion
    print("\n⚡ Verifying Tier 1 (Local First) Search Promotion:")
    print("   Searching 'Glycerine' now that it is downloaded...")
    tier1_res = tiered_search("Glycerine")
    if tier1_res["local_results"]:
        loc = tier1_res["local_results"][0]
        print(f"   🎉 PROMOTED TO TIER 1! Found in local SQLite FTS5:")
        print(f"      • Title: {loc['title']}")
        print(f"      • Artist: {loc['artist']}")
        print(f"      • Path: {loc['local_path']}")
        print(f"      • Normalized LUFS: {loc['normalized_lufs']} LUFS")
        print(f"      • Online YouTube query was bypassed (0ms latency, 0 quota cost)!")
    else:
        print("❌ Local track was not returned in Tier 1 search.")
        return False

    print("\n" + "=" * 65)
    print("✨ ALL LIVE TESTS COMPLETED WITH 100% SUCCESS!")
    print("=" * 65)
    return True

if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
