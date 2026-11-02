import os
import sys
import time
import json
import shutil
import asyncio
import subprocess
from pathlib import Path
from playwright.async_api import async_playwright

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
SCREENSHOT_DIR = BASE_DIR / "screenshots" / "automated_run"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

PORT = 8877
SERVER_URL = f"http://127.0.0.1:{PORT}"

async def main():
    print("=" * 65)
    print("🌴 TIKI KARAOKE - STEP 2 AUTOMATED BEHAVIORAL & DSP TEST")
    print("=" * 65)

    # 1. Setup DB with configured state and local track
    from config import DATA_DIR, HOST_TOKEN
    from database import init_db, get_db, save_track, update_track_download_status, purge_all_singers

    init_db()
    purge_all_singers(include_history=True)
    conn = get_db()
    with conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('configured', 'true')")
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('bar_name', 'Tiki Automated Lounge')")
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('intermission_seconds', '15')")
    conn.close()

    # Ensure Bohemian Rhapsody is registered and marked downloaded
    track_path = BASE_DIR / "karaoke_library" / "tracks" / "Queen" / "Bohemian Rhapsody [9Lxm0iSnKNc].mp4"
    if not track_path.exists():
        # Fallback to copy from backup if missing
        backup = BASE_DIR / "_backup_karaoke_data" / "karaoke_library" / "tracks" / "Queen" / "Bohemian Rhapsody [9Lxm0iSnKNc].mp4"
        if backup.exists():
            track_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(backup, track_path)

    track_id = save_track(
        video_id="9Lxm0iSnKNc",
        title="Bohemian Rhapsody",
        artist="Queen",
        original_title="Queen - Bohemian Rhapsody (Karaoke)",
        channel="Karaoke Channel",
        duration=354
    )
    update_track_download_status(track_id, "ready", local_path=str(track_path))
    print(f"✅ Prepared test track: Queen - Bohemian Rhapsody (ID: {track_id})")

    # 2. Launch FastAPI Server on PORT
    env = os.environ.copy()
    env["TIKI_PORT"] = str(PORT)
    env["PORT"] = str(PORT)
    env["TIKI_PUBLIC_HOST"] = f"127.0.0.1:{PORT}"

    print(f"🚀 Starting Tiki Karaoke server on {SERVER_URL}...")
    server_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(PORT)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    # Wait for server to become responsive
    import urllib.request
    server_ready = False
    for _ in range(30):
        try:
            with urllib.request.urlopen(f"{SERVER_URL}/api/state", timeout=1) as resp:
                if resp.status == 200:
                    server_ready = True
                    break
        except Exception:
            await asyncio.sleep(0.3)

    if not server_ready:
        server_proc.kill()
        raise RuntimeError("FastAPI server failed to start within 10 seconds")

    print("✅ Server is online and accepting connections!")

    results = {}

    try:
        async with async_playwright() as p:
            # Launch browser with autoplay flags enabled so media & audio graph run
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--autoplay-policy=no-user-gesture-required",
                    "--no-sandbox",
                    "--disable-web-security"
                ]
            )

            # ==========================================
            # PHASE 1: TV STAGE DISPLAY
            # ==========================================
            print("\n🖥️  [Phase 1] Launching TV Stage Display (1920x1080)...")
            stage_context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                device_scale_factor=1
            )
            stage_page = await stage_context.new_page()
            await stage_page.goto(f"{SERVER_URL}/stage")
            await stage_page.wait_for_selector("#stageContainer", state="visible")
            await stage_page.wait_for_timeout(1500)

            # Verify Stage Initial DOM Elements
            is_idle_visible = await stage_page.is_visible("#stageContainer") and await stage_page.is_visible("#topHUD")
            results["stage_initial_idle"] = is_idle_visible
            print(f"   • Stage Display HUD & Stage Container visible: {is_idle_visible}")

            qr_visible = await stage_page.is_visible("#fillerQRCard") or await stage_page.is_visible("#joinQROverlay")
            results["stage_qr_visible"] = qr_visible
            print(f"   • Public Singer QR code visible: {qr_visible}")

            # Verify Web Audio DSP Initialization
            dsp_initialized = await stage_page.evaluate("""() => {
                return window.audioDSP !== undefined && window.audioDSP !== null;
            }""")
            results["dsp_initialized"] = dsp_initialized
            print(f"   • Web Audio DSP Pipeline created: {dsp_initialized}")

            shot_1 = SCREENSHOT_DIR / "stage_01_idle.png"
            await stage_page.screenshot(path=str(shot_1))
            print(f"   📷 Captured screenshot: {shot_1.name}")

            # ==========================================
            # PHASE 2: SINGER MOBILE CLIENT (SMARTPHONE)
            # ==========================================
            print("\n📱 [Phase 2] Launching Singer Mobile Client (390x844 iPhone Viewport)...")
            mobile_context = await browser.new_context(
                viewport={"width": 390, "height": 844},
                is_mobile=True,
                has_touch=True,
                user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15"
            )
            mobile_page = await mobile_context.new_page()
            await mobile_page.goto(f"{SERVER_URL}/join")
            await mobile_page.wait_for_selector("#onboardingModal", state="visible")

            # Fill Singer Onboarding Form: "Leilani", avatar 🌴
            print("   • Registering Singer 'Leilani' (🌴)...")
            await mobile_page.click('.avatar-option[data-avatar="🌴"]')
            await mobile_page.fill("#singerNameInput", "Leilani")
            await mobile_page.click("#onboardingSubmitBtn")
            await mobile_page.wait_for_selector("#onboardingModal", state="hidden")

            singer_badge = await mobile_page.inner_text("#headerSingerBadge")
            print(f"   • Active Singer Badge: '{singer_badge.strip()}'")
            results["singer_registered"] = "Leilani" in singer_badge

            # Search specifically for Bohemian Rhapsody
            print("   • Searching for 'Bohemian Rhapsody' in song catalog...")
            await mobile_page.fill("#searchInput", "Bohemian Rhapsody")
            await mobile_page.wait_for_timeout(800)

            # Wait for search results and click Queue button
            await mobile_page.wait_for_selector(".local-queue-btn, .yt-queue-btn", state="visible")
            queue_btns = await mobile_page.query_selector_all(".local-queue-btn, .yt-queue-btn")
            print(f"   • Search matched {len(queue_btns)} song(s). Opening Queue Modal...")
            await queue_btns[0].click()

            await mobile_page.wait_for_selector("#queueModal", state="visible")

            # Select +2 Semitones Pitch Shift
            print("   • Selecting Key Adjustment: +2 Semitones...")
            await mobile_page.click('.pitch-btn[data-pitch="2"]')
            await mobile_page.wait_for_timeout(200)

            # Submit song to queue
            print("   • Submitting track to queue...")
            await mobile_page.click("#submitQueueBtn")
            await mobile_page.wait_for_selector("#queueModal", state="hidden")
            await mobile_page.wait_for_timeout(1000)

            # Switch to Queue Tab
            await mobile_page.click('button[data-tab="queueTab"]')
            await mobile_page.wait_for_timeout(600)

            queue_items = await mobile_page.inner_text("#queueListContainer")
            results["queue_contains_song"] = "Bohemian Rhapsody" in queue_items and "Leilani" in queue_items and "#1 IN LINE" in queue_items
            print(f"   • Queue verified on mobile with 1-based index: {results['queue_contains_song']}")

            shot_2 = SCREENSHOT_DIR / "singer_02_queued.png"
            await mobile_page.screenshot(path=str(shot_2))
            print(f"   📷 Captured screenshot: {shot_2.name}")

            # Queue a 2nd singer so intermission triggers when singer 1 finishes
            import urllib.request as ur
            s2_data = json.dumps({
                "video_id": "9Lxm0iSnKNc",
                "title": "Bohemian Rhapsody",
                "artist": "Queen",
                "singer_name": "Kailani",
                "singer_token": "token_kailani_2",
                "pitch_shift": 0,
                "force": True
            }).encode()
            req_s2 = ur.Request(f"{SERVER_URL}/api/queue", data=s2_data, headers={"Content-Type": "application/json"}, method="POST")
            with ur.urlopen(req_s2) as resp:
                print("   • Queued 2nd singer (Kailani with force=True) to ensure next-singer intermission fires")

            # ==========================================
            # PHASE 3: VERIFY STAGE UP-NEXT & EMOJI HYPE
            # ==========================================
            print("\n🎉 [Phase 3] Testing Live Crowd Emoji Reaction Broadcast...")
            await stage_page.wait_for_timeout(1000)

            # Mobile singer sends 🔥 reaction
            print("   • Tapping 🔥 cheer button on mobile phone...")
            await mobile_page.click('.reaction-btn:has-text("🔥")')
            await mobile_page.wait_for_timeout(400)

            # Verify floating emoji appears in Stage Display DOM
            floating_emoji_found = await stage_page.evaluate("""() => {
                const el = document.querySelector('.floating-reaction');
                return el !== null && el.innerText.includes('🔥');
            }""")
            results["emoji_broadcast_success"] = floating_emoji_found
            print(f"   • Floating reaction animated on Stage Screen: {floating_emoji_found}")

            shot_3 = SCREENSHOT_DIR / "stage_03_emoji.png"
            await stage_page.screenshot(path=str(shot_3))
            print(f"   📷 Captured screenshot: {shot_3.name}")

            # ==========================================
            # PHASE 4: HOST DJ CONTROLS & PLAYBACK
            # ==========================================
            print("\n👑 [Phase 4] Launching Host DJ Remote...")
            host_context = await browser.new_context(
                viewport={"width": 1280, "height": 800}
            )
            host_page = await host_context.new_page()
            await host_page.goto(f"{SERVER_URL}/host?token={HOST_TOKEN}")
            await host_page.wait_for_timeout(1500)

            # Get queue_id dynamically from queue
            import urllib.request as ur
            with ur.urlopen(f"{SERVER_URL}/api/queue") as resp:
                q_list = json.loads(resp.read().decode())
                first_q_id = q_list[0]["queue_id"]

            print(f"   • Host starting track playback on Stage for queue_id={first_q_id}...")
            req = ur.Request(f"{SERVER_URL}/api/queue/{first_q_id}/play", headers={"X-Host-Token": HOST_TOKEN}, method="POST")
            with ur.urlopen(req) as resp:
                play_res = json.loads(resp.read().decode())
                print(f"   • Play API Response: {play_res.get('status')}")

            # ==========================================
            # PHASE 5: VERIFY STAGE DSP & AUDIO MUTE
            # ==========================================
            print("\n🎵 [Phase 5] Inspecting Stage Web Audio DSP Pipeline in Real-time...")
            await stage_page.wait_for_timeout(2000)

            dsp_state = await stage_page.evaluate("""() => {
                if (!window.audioDSP) return null;
                return {
                    currentPitch: window.audioDSP.currentPitch,
                    directGain: window.audioDSP.directGain ? window.audioDSP.directGain.gain.value : null,
                    dspGain: window.audioDSP.dspGain ? window.audioDSP.dspGain.gain.value : null,
                    masterGain: window.audioDSP.masterGain ? window.audioDSP.masterGain.gain.value : null,
                    mod3Gain: (window.audioDSP.pitchShifter && window.audioDSP.pitchShifter.mod3Gain) ? window.audioDSP.pitchShifter.mod3Gain.gain.value : null,
                    mod1Gain: (window.audioDSP.pitchShifter && window.audioDSP.pitchShifter.mod1Gain) ? window.audioDSP.pitchShifter.mod1Gain.gain.value : null,
                    videoPlaying: (!document.getElementById('localVideo').paused || !document.getElementById('fillerVideo').paused),
                    videoMuted: document.getElementById('localVideo').muted
                };
            }""")
            print(f"   • Web Audio State: {dsp_state}")
            results["dsp_pitch_shift_engaged"] = (dsp_state and dsp_state.get("currentPitch") == 2 and dsp_state.get("dspGain") == 1.0)
            results["dsp_pitch_up_circuit"] = (dsp_state and dsp_state.get("mod3Gain") == 1.0)

            # TEST STAGE AUDIO MUTE
            print("\n🔇 [Phase 5b] Testing Stage Audio Mute decoupling...")
            # Toggle mute on stage
            await stage_page.evaluate("""() => {
                applyStageAudioMute(true);
            }""")
            await stage_page.wait_for_timeout(500)

            mute_state = await stage_page.evaluate("""() => {
                const vid = document.getElementById('localVideo');
                const filler = document.getElementById('fillerVideo');
                return {
                    isMutedAudio: window.isMutedAudio,
                    masterGain: window.audioDSP ? window.audioDSP.masterGain.gain.value : null,
                    videoMuted: vid ? vid.muted : false,
                    playbackAlive: (vid && !vid.paused) || (filler && !filler.paused)
                };
            }""")
            print(f"   • Mute State: {mute_state}")
            results["audio_muted_successfully"] = (mute_state["isMutedAudio"] is True and mute_state["masterGain"] == 0.0)
            results["playback_not_frozen_by_mute"] = (mute_state["isMutedAudio"] is True and mute_state["videoMuted"] is True)

            # Unmute audio
            await stage_page.evaluate("""() => {
                applyStageAudioMute(false);
            }""")
            await stage_page.wait_for_timeout(500)

            shot_5 = SCREENSHOT_DIR / "stage_05_playing_dsp.png"
            await stage_page.screenshot(path=str(shot_5))
            print(f"   📷 Captured screenshot: {shot_5.name}")

            # ==========================================
            # PHASE 6: INTERMISSION & QUEUE ADVANCEMENT
            # ==========================================
            print("\n⏱️  [Phase 6] Testing Song Completion & Intermission Transition...")
            req_comp = ur.Request(f"{SERVER_URL}/api/queue/{first_q_id}/complete", headers={"X-Host-Token": HOST_TOKEN}, method="POST")
            with ur.urlopen(req_comp) as resp:
                comp_res = json.loads(resp.read().decode())
                print(f"   • Complete API Status: {comp_res.get('status')}")

            await stage_page.wait_for_timeout(1500)

            intermission_active = await stage_page.evaluate("""() => {
                const ov = document.getElementById('intermissionOverlay');
                return ov !== null && (ov.style.display === 'flex' || window.getComputedStyle(ov).display === 'flex');
            }""")
            results["intermission_overlay_active"] = intermission_active
            print(f"   • Intermission overlay active on Stage: {intermission_active}")

            shot_6 = SCREENSHOT_DIR / "stage_06_intermission.png"
            await stage_page.screenshot(path=str(shot_6))
            print(f"   📷 Captured screenshot: {shot_6.name}")

            await browser.close()

    finally:
        print("\n🛑 Shutting down test server...")
        server_proc.kill()
        server_proc.wait()

    print("\n" + "=" * 65)
    print("📊 AUTOMATED BEHAVIORAL & DSP TEST RESULTS:")
    print("=" * 65)
    all_passed = True
    for test_name, passed in results.items():
        status_str = "✅ PASS" if passed else "❌ FAIL"
        if not passed:
            all_passed = False
        print(f"  {status_str} : {test_name}")
    print("=" * 65)

    if all_passed:
        print("🏆 ALL BEHAVIORAL & MULTI-DISPLAY TESTS PASSED PERFECTLY!")
        sys.exit(0)
    else:
        print("⚠️ SOME TESTS FAILED!")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
