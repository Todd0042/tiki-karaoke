function previewCleanupTrack(trackId, title, artist, videoId, isFiller) {
    const modal = document.getElementById("rehearsalPreviewModal");
    if (!modal) return;

    const titleEl = document.getElementById("rehearsalModalTitle");
    const artistEl = document.getElementById("rehearsalModalArtist");
    const localV = document.getElementById("rehearsalLocalVideo");
    const iframe = document.getElementById("rehearsalYoutubeIframe");
    const muteBtn = document.getElementById("rehearsalMuteToggleBtn");

    if (titleEl) titleEl.innerText = title || "Track";
    if (artistEl) artistEl.innerText = (isFiller ? "🎬 Music Video • " : "") + (artist || "");

    rehearsalIsMuted = true;
    if (muteBtn) {
        muteBtn.innerText = "🔇 Unmute Audio";
        muteBtn.style.color = "var(--neon-gold)";
        muteBtn.style.borderColor = "var(--neon-gold)";
    }

    if (iframe) { iframe.src = ""; iframe.style.display = "none"; }
    if (localV) {
        localV.style.display = "block";
        localV.muted = true;
        localV.src = isFiller ? `/stream/filler/${trackId}` : `/stream/${trackId}`;
        localV.play().catch(() => {});
    }

    modal.style.display = "flex";
}

function openEditTrackModal(trackId, title, artist, isFiller) {
    const modal = document.getElementById("editTrackModal");
    if (!modal) return;
    document.getElementById("editTrackId").value = trackId;
    document.getElementById("editTrackIsFiller").value = isFiller ? "1" : "0";
    document.getElementById("editTrackTitleInput").value = title || "";
    document.getElementById("editTrackArtistInput").value = artist || "";
    modal.style.display = "flex";
}

function closeEditTrackModal() {
    const modal = document.getElementById("editTrackModal");
    if (modal) modal.style.display = "none";
}

async function submitEditTrack() {
    const trackId = document.getElementById("editTrackId").value;
    const isFiller = document.getElementById("editTrackIsFiller").value === "1";
    const newTitle = document.getElementById("editTrackTitleInput").value.trim();
    const newArtist = document.getElementById("editTrackArtistInput").value.trim();

    if (!newTitle || !newArtist) {
        alert("Please enter both title and artist");
        return;
    }

    try {
        const res = await fetch(`/api/host/tracks/${encodeURIComponent(trackId)}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                title: newTitle,
                artist: newArtist,
                is_filler: isFiller
            })
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || "Server error");
        }
        closeEditTrackModal();
        showToast("✅ Track metadata updated & file renamed!");
        loadCleanupCandidates();
        if (typeof loadLibraryTracks === 'function') loadLibraryTracks();
        if (typeof loadFillerTracks === 'function') loadFillerTracks();
    } catch (e) {
        alert("Error saving metadata: " + e.message);
    }
}

async function convertCleanupTrackType(trackId, targetType, sourceType, isFiller) {
    if (targetType === sourceType) return;
    try {
        const res = await fetch(`/api/host/tracks/${encodeURIComponent(trackId)}/convert-type`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                target_type: targetType,
                source_type: sourceType,
                is_filler: isFiller
            })
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || "Server error");
        }
        showToast(`✅ Converted to ${targetType.replace('_', ' ')}! File relocated.`);
        loadCleanupCandidates();
        if (typeof loadLibraryTracks === 'function') loadLibraryTracks();
        if (typeof loadFillerTracks === 'function') loadFillerTracks();
    } catch (e) {
        alert("Error converting type: " + e.message);
        loadCleanupCandidates();
    }
}

// =============================================================================
// BGM FILLER & MUSIC VIDEOS MANAGEMENT (HOST DJ)
// =============================================================================

let fillerSearchDebounceTimer = null;
let currentStagedFillerIds = new Set();

function onFillerSearchInput(val) {
    const clearBtn = document.getElementById("fillerSearchClearBtn");
    if (clearBtn) clearBtn.style.display = val.trim() ? "flex" : "none";

    clearTimeout(fillerSearchDebounceTimer);
    if (!val || val.trim().length < 2) {
        const resultsEl = document.getElementById("fillerSearchResults");
        if (resultsEl) resultsEl.innerHTML = "";
        return;
    }

    const loader = document.getElementById("fillerSearchLoading");
    if (loader) loader.style.display = "block";

    fillerSearchDebounceTimer = setTimeout(() => {
        fetch(`/api/host/search-filler?q=${encodeURIComponent(val.trim())}`)
            .then(res => res.json())
            .then(results => {
                if (loader) loader.style.display = "none";
                renderFillerSearchResults(results || []);
            })
            .catch(err => {
                if (loader) loader.style.display = "none";
                console.error("[Filler Search] Error:", err);
            });
    }, 400);
}

function clearFillerSearch() {
    const input = document.getElementById("fillerSearchInput");
    if (input) input.value = "";
    const clearBtn = document.getElementById("fillerSearchClearBtn");
    if (clearBtn) clearBtn.style.display = "none";
    const resultsEl = document.getElementById("fillerSearchResults");
    if (resultsEl) resultsEl.innerHTML = "";
}

function renderFillerSearchResults(items) {
    const container = document.getElementById("fillerSearchResults");
    if (!container) return;

    if (!items || items.length === 0) {
        container.innerHTML = `
            <div style="text-align: center; padding: 24px 16px; color: var(--text-muted); font-size: 0.9rem;">
                No music videos found. Try searching by artist or exact song name!
            </div>
        `;
        return;
    }

    let html = "";
    items.forEach(item => {
        const durMin = Math.floor(item.duration / 60);
        const durSec = (item.duration % 60).toString().padStart(2, "0");
        const durStr = item.duration ? `${durMin}:${durSec}` : "";
        const isStaged = currentStagedFillerIds.has(item.video_id);

        const safeTitle = escapeHtml(item.title);
        const safeArtist = escapeHtml(item.artist);

        html += `
            <div class="tiki-card" style="display: flex; gap: 12px; align-items: center; padding: 10px 12px;">
                <img src="${item.thumbnail || '/static/images/tiki_default.png'}" 
                     alt="thumb" 
                     style="width: 80px; height: 50px; object-fit: cover; border-radius: 8px; flex-shrink: 0; background: #000;"
                     onerror="this.style.display='none'">
                <div style="flex: 1; min-width: 0;">
                    <div style="font-weight: 700; font-size: 0.92rem; color: #fff; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">
                        ${safeTitle}
                    </div>
                    <div style="font-size: 0.8rem; color: var(--neon-cyan); white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">
                        ${safeArtist}
                    </div>
                    <div style="font-size: 0.72rem; color: var(--text-muted); margin-top: 2px;">
                        ${durStr ? `⏱️ ${durStr}` : ""} ${item.channel ? `• ${escapeHtml(item.channel)}` : ""}
                    </div>
                </div>
                <div style="display: flex; gap: 6px; align-items: center; flex-shrink: 0;">
                    <button type="button" 
                            onclick='previewFillerSearchResult(${JSON.stringify(item)})'
                            class="btn-ghost" 
                            style="padding: 8px 10px; font-size: 0.8rem;" 
                            title="Preview video privately on phone">
                        👁️ Preview
                    </button>
                    <button type="button" 
                            id="stageBtn_${item.video_id}"
                            onclick='stageFillerVideo(${JSON.stringify(item)})'
                            class="${isStaged ? 'btn-ghost' : 'btn-neon-pink'}" 
                            style="padding: 8px 12px; font-size: 0.8rem;">
                        ${isStaged ? "✓ Staged" : "+ Stage"}
                    </button>
                </div>
            </div>
        `;
    });

    container.innerHTML = html;
}

function stageFillerVideo(video) {
    if (!video || !video.video_id) return;
    const btn = document.getElementById(`stageBtn_${video.video_id}`);
    if (btn) {
        btn.disabled = true;
        btn.innerText = "Staging...";
    }

    fetch("/api/host/filler-tracks/stage", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            video_id: video.video_id,
            title: video.title,
            artist: video.artist,
            original_title: video.original_title || video.title,
            duration: video.duration || 0
        })
    })
    .then(res => {
        if (!res.ok) throw new Error("Failed to stage video");
        return res.json();
    })
    .then(data => {
        currentStagedFillerIds.add(video.video_id);
        if (btn) {
            btn.className = "btn-ghost";
            btn.innerText = "✓ Staged";
            btn.disabled = false;
        }
        showToast("🎬 Added to BGM Crate!");
        loadFillerTracks();
    })
    .catch(err => {
        alert("Error staging video: " + err.message);
        if (btn) btn.disabled = false;
    });
}

function loadFillerTracks() {
    // 1. Load Settings
    fetch("/api/host/filler-settings")
        .then(res => res.ok ? res.json() : null)
        .then(settings => {
            if (settings) loadFillerSettings(settings);
        })
        .catch(() => {});

    // 2. Load Tracks
    fetch("/api/host/filler-tracks")
        .then(res => res.ok ? res.json() : [])
        .then(tracks => {
            renderFillerCrateView(tracks || []);
        })
        .catch(err => {
            console.error("[Filler] Failed to load tracks:", err);
        });
}

function loadFillerSettings(settings) {
    if (!settings) return;
    const toggle = document.getElementById("bgmAutopilotToggle");
    if (toggle) toggle.checked = !!settings.bgm_video_enabled;

    const policy = settings.bgm_transition_policy || "fade";
    const fadeBtn = document.getElementById("policyFadeBtn");
    const finishBtn = document.getElementById("policyFinishBtn");
    if (fadeBtn && finishBtn) {
        if (policy === "finish") {
            finishBtn.classList.add("active");
            fadeBtn.classList.remove("active");
        } else {
            fadeBtn.classList.add("active");
            finishBtn.classList.remove("active");
        }
    }

    const vibeMode = settings.intermission_vibe_mode || "sing_along";
    updateVibeModeUI(vibeMode);
}

function updateVibeModeUI(mode) {
    const cleanMode = (mode || "sing_along").trim().toLowerCase();

    // BGM Crate Tab buttons & description
    const fillerBtn = document.getElementById("vibeModeFillerBtn");
    const singAlongBtn = document.getElementById("vibeModeSingAlongBtn");
    const mashupBtn = document.getElementById("vibeModeMashupBtn");
    const desc = document.getElementById("vibeModeDesc");

    // Main Host Desk buttons, badge & description
    const hostFillerBtn = document.getElementById("hostVibeModeFillerBtn");
    const hostSingAlongBtn = document.getElementById("hostVibeModeSingAlongBtn");
    const hostMashupBtn = document.getElementById("hostVibeModeMashupBtn");
    const hostDesc = document.getElementById("hostVibeModeDesc");
    const hostBadge = document.getElementById("hostVibeActiveBadge");

    if (fillerBtn) fillerBtn.classList.toggle("active", cleanMode === "pure_filler");
    if (singAlongBtn) singAlongBtn.classList.toggle("active", cleanMode === "sing_along");
    if (mashupBtn) mashupBtn.classList.toggle("active", cleanMode === "intelligent_mashup");

    if (hostFillerBtn) hostFillerBtn.classList.toggle("active", cleanMode === "pure_filler");
    if (hostSingAlongBtn) hostSingAlongBtn.classList.toggle("active", cleanMode === "sing_along");
    if (hostMashupBtn) hostMashupBtn.classList.toggle("active", cleanMode === "intelligent_mashup");

    let textDesc = "";
    let badgeText = "";
    if (cleanMode === "pure_filler") {
        textDesc = "Pure filler music videos only. Great for a classic party lounge vibe.";
        badgeText = "📼 Music Videos";
    } else if (cleanMode === "sing_along") {
        textDesc = "True Karaoke: Crowd sing-along tracks with guide vocals & lyrics between songs.";
        badgeText = "🎤 Sing-Alongs";
    } else {
        textDesc = "Intelligent mashup: Sing-alongs when queue is empty, music videos when show is stopped.";
        badgeText = "🧠 Smart Mashup";
    }

    if (desc) desc.innerText = textDesc;
    if (hostDesc) hostDesc.innerText = textDesc;
    if (hostBadge) hostBadge.innerText = badgeText;
}

function setIntermissionVibeMode(mode) {
    updateVibeModeUI(mode);
    fetch("/api/host/filler-settings", {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "X-Host-Token": typeof HOST_TOKEN !== "undefined" ? HOST_TOKEN : ""
        },
        body: JSON.stringify({ intermission_vibe_mode: mode })
    })
    .then(res => res.json())
    .then(() => {
        const labels = {
            pure_filler: "📼 Music Videos Only",
            sing_along: "🎤 Sing-Alongs Only",
            intelligent_mashup: "🧠 Intelligent Mashup"
        };
        showToast(`🌴 Intermission mode: ${labels[mode] || mode}`);
    })
    .catch(err => {
        showToast("⚠️ Failed to update intermission mode: " + err);
    });
}

function renderFillerCrateView(tracks) {
    currentStagedFillerIds.clear();
    tracks.forEach(t => currentStagedFillerIds.add(t.video_id));

    const staged = tracks.filter(t => !t.is_downloaded || t.download_status !== "ready");
    const ready = tracks.filter(t => t.is_downloaded || t.download_status === "ready");

    const batchBanner = document.getElementById("fillerBatchBanner");
    const stagedSection = document.getElementById("stagedSection");
    const stagedList = document.getElementById("stagedList");
    const stagedCountText = document.getElementById("stagedCountText");
    const batchBtn = document.getElementById("batchDownloadBtn");

    const pendingDownloadCount = staged.filter(t => t.download_status !== "downloading" && t.download_status !== "fetching_metadata" && t.download_status !== "normalizing").length;

    if (staged.length > 0) {
        if (batchBanner) {
            batchBanner.style.display = "flex";
            if (stagedCountText) stagedCountText.innerText = `${staged.length} video(s) staged in crate`;
            if (batchBtn) {
                batchBtn.disabled = pendingDownloadCount === 0;
                batchBtn.innerText = pendingDownloadCount > 0 ? `📥 Download Batch (${pendingDownloadCount})` : "⏳ Downloading...";
            }
        }
        if (stagedSection) stagedSection.style.display = "block";
        if (stagedList) {
            let stagedHtml = "";
            staged.forEach(t => {
                let badgeStyle = "background: rgba(255,183,3,0.18); color: var(--neon-gold); border: 1px solid var(--neon-gold);";
                let badgeText = "⏳ Staged";

                if (t.download_status === "fetching_metadata") {
                    badgeStyle = "background: rgba(0,240,255,0.18); color: var(--neon-cyan); border: 1px solid var(--neon-cyan);";
                    badgeText = "🔍 Metadata";
                } else if (t.download_status === "downloading") {
                    badgeStyle = "background: rgba(0,240,255,0.25); color: var(--neon-cyan); border: 1px solid var(--neon-cyan);";
                    badgeText = "📥 Downloading";
                } else if (t.download_status === "normalizing") {
                    badgeStyle = "background: rgba(255,42,133,0.2); color: var(--neon-pink); border: 1px solid var(--neon-pink);";
                    badgeText = "🎚️ Normalizing LUFS";
                } else if (t.download_status === "failed") {
                    badgeStyle = "background: rgba(255,85,85,0.2); color: #ff5555; border: 1px solid #ff5555;";
                    badgeText = "⚠️ Failed";
                }

                stagedHtml += `
                    <div class="tiki-card" style="display: flex; justify-content: space-between; align-items: center; padding: 10px 12px;">
                        <div style="flex: 1; min-width: 0; padding-right: 8px;">
                            <div style="font-weight: 700; font-size: 0.88rem; color: #fff; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">
                                ${escapeHtml(t.title)}
                            </div>
                            <div style="font-size: 0.78rem; color: var(--neon-cyan); white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">
                                ${escapeHtml(t.artist)}
                            </div>
                        </div>
                        <div style="display: flex; align-items: center; gap: 8px; flex-shrink: 0;">
                            <span style="font-size: 0.72rem; font-weight: 700; padding: 3px 8px; border-radius: 8px; ${badgeStyle}">
                                ${badgeText}
                            </span>
                            <button type="button" onclick="deleteFillerTrack(${t.id})" class="btn-ghost" style="padding: 2px 8px; font-size: 0.75rem; color: #ff5555;">
                                ✕
                            </button>
                        </div>
                    </div>
                `;
            });
            stagedList.innerHTML = stagedHtml;
        }
    } else {
        if (batchBanner) batchBanner.style.display = "none";
        if (stagedSection) stagedSection.style.display = "none";
    }

    // Ready Crate Section
    const readyCountEl = document.getElementById("readyCrateCount");
    const readyListEl = document.getElementById("readyCrateList");
    const emptyNotice = document.getElementById("emptyCrateNotice");

    if (readyCountEl) readyCountEl.innerText = `${ready.length} ready`;
    if (emptyNotice) emptyNotice.style.display = ready.length === 0 ? "block" : "none";

    if (readyListEl) {
        let readyHtml = "";
        ready.forEach(t => {
            const genreTag = t.genre ? `${t.genre}` : "Pop";
            const yearTag = t.release_year ? ` • ${t.release_year}` : "";
            const playsTag = t.play_count > 0 ? `Played ${t.play_count}x` : "Unplayed";

            readyHtml += `
                <div class="tiki-card" style="display: flex; justify-content: space-between; align-items: center; padding: 12px 14px;">
                    <div style="flex: 1; min-width: 0; padding-right: 10px;">
                        <div style="font-weight: 700; font-size: 0.92rem; color: #fff; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">
                            ${escapeHtml(t.title)}
                        </div>
                        <div style="font-size: 0.8rem; color: var(--neon-cyan); white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">
                            ${escapeHtml(t.artist)}
                        </div>
                        <div style="display: flex; align-items: center; gap: 6px; margin-top: 4px;">
                            <span style="font-size: 0.7rem; font-weight: 700; padding: 1px 7px; border-radius: 6px; background: rgba(0,255,100,0.15); color: var(--neon-lime); border: 1px solid var(--neon-lime);">
                                🎸 ${escapeHtml(genreTag)}${yearTag}
                            </span>
                            <span style="font-size: 0.7rem; color: var(--text-muted);">
                                ${playsTag}
                            </span>
                        </div>
                    </div>
                    <div style="display: flex; align-items: center; gap: 6px; flex-shrink: 0;">
                        <button type="button" 
                                onclick='previewFillerCrateTrack(${t.id}, ${JSON.stringify(t.title)}, ${JSON.stringify(t.artist)}, "${t.video_id}", ${!!t.is_downloaded})' 
                                class="btn-ghost" 
                                style="padding: 6px 10px; font-size: 0.78rem;" 
                                title="Preview video privately on phone">
                            👁️ Preview
                        </button>
                        <button type="button" onclick="forcePlayFillerTrack(${t.id})" class="btn-ghost" style="padding: 6px 10px; font-size: 0.78rem; border-color: var(--neon-gold); color: var(--neon-gold);" title="Play this video on stage right now">
                            ▶ Stage
                        </button>
                        <button type="button" onclick="deleteFillerTrack(${t.id})" class="btn-ghost" style="padding: 6px 8px; font-size: 0.78rem; color: #ff5555;" title="Remove track from crate">
                            🗑️
                        </button>
                    </div>
                </div>
            `;
        });
        readyListEl.innerHTML = readyHtml;
    }
}

function downloadFillerBatch() {
    const btn = document.getElementById("batchDownloadBtn");
    if (btn) {
        btn.disabled = true;
        btn.innerText = "⏳ Enqueueing...";
    }

    fetch("/api/host/filler-tracks/download-batch", {
        method: "POST",
        headers: { "Content-Type": "application/json" }
    })
    .then(res => res.json())
    .then(data => {
        showToast(`📥 Started background download for ${data.enqueued_count} tracks!`);
        loadFillerTracks();
    })
    .catch(err => {
        alert("Error starting batch download: " + err.message);
        if (btn) btn.disabled = false;
    });
}

function deleteFillerTrack(id) {
    if (!confirm("Are you sure you want to remove this track from your BGM crate?")) return;

    fetch(`/api/host/filler-tracks/${id}`, {
        method: "DELETE"
    })
    .then(res => res.json())
    .then(() => {
        showToast("Track removed from crate");
        loadFillerTracks();
    })
    .catch(err => alert("Error deleting track: " + err.message));
}

function forcePlayFillerTrack(id) {
    fetch(`/api/host/filler-tracks/${id}/play`, {
        method: "POST"
    })
    .then(res => {
        if (!res.ok) throw new Error("Could not play track");
        return res.json();
    })
    .then(data => {
        showToast(`▶️ Now playing on stage: ${data.playing.title}!`);
    })
    .catch(err => alert("Error: " + err.message));
}

function toggleBgmVideoAutopilot(checked) {
    fetch("/api/host/filler-settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ bgm_video_enabled: checked })
    })
    .then(res => res.json())
    .then(() => {
        showToast(checked ? "▶️ BGM Autopilot ON: Plays when queue empty" : "⏸️ BGM Autopilot OFF");
    });
}

function setBgmTransitionPolicy(policy) {
    const fadeBtn = document.getElementById("policyFadeBtn");
    const finishBtn = document.getElementById("policyFinishBtn");
    if (fadeBtn && finishBtn) {
        if (policy === "finish") {
            finishBtn.classList.add("active");
            fadeBtn.classList.remove("active");
        } else {
            fadeBtn.classList.add("active");
            finishBtn.classList.remove("active");
        }
    }

    fetch("/api/host/filler-settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ bgm_transition_policy: policy })
    })
    .then(res => res.json())
    .then(() => {
        showToast(policy === "finish" ? "⏳ Policy: Let current video finish" : "⚡ Policy: 3s soft fade when singer queues");
    });
}

// =============================================================================
// HOST VIDEO PREVIEW MODAL LOGIC
// =============================================================================

function openHostVideoPreviewModal(title, artist, badgeText) {
    const modal = document.getElementById("hostVideoPreviewModal");
    if (!modal) return;
    const badgeEl = document.getElementById("previewModalBadge");
    const titleEl = document.getElementById("previewModalTitle");
    const artistEl = document.getElementById("previewModalArtist");
    if (badgeEl) badgeEl.innerText = badgeText || "🎬 BGM PREVIEW";
    if (titleEl) titleEl.innerText = title;
    if (artistEl) artistEl.innerText = artist;
    modal.style.display = "flex";
}

function closeHostVideoPreview() {
    const modal = document.getElementById("hostVideoPreviewModal");
    if (modal) modal.style.display = "none";

    const v = document.getElementById("previewLocalVideo");
    if (v) {
        v.pause();
        v.removeAttribute("src");
        v.load();
        v.style.display = "none";
    }

    const iframe = document.getElementById("previewYoutubeIframe");
    if (iframe) {
        iframe.src = "";
        iframe.style.display = "none";
    }
}

function previewFillerSearchResult(video) {
    if (!video || !video.video_id) return;
    openHostVideoPreviewModal(video.title, video.artist, "🔍 YOUTUBE SEARCH PREVIEW");

    const localV = document.getElementById("previewLocalVideo");
    if (localV) localV.style.display = "none";

    const iframe = document.getElementById("previewYoutubeIframe");
    if (iframe) {
        iframe.style.display = "block";
        iframe.src = `https://www.youtube-nocookie.com/embed/${video.video_id}?autoplay=1`;
    }

    const actions = document.getElementById("previewModalActions");
    if (actions) {
        const isStaged = currentStagedFillerIds.has(video.video_id);
        actions.innerHTML = `
            <button type="button" onclick="closeHostVideoPreview()" class="btn-ghost" style="padding: 10px 16px; font-size: 0.9rem; border-radius: 8px; cursor: pointer;">
                Close
            </button>
            <button type="button" 
                    id="previewStageBtn"
                    onclick='stageFillerVideo(${JSON.stringify(video)}); closeHostVideoPreview();' 
                    class="${isStaged ? 'btn-ghost' : 'btn-neon-pink'}" 
                    style="padding: 10px 18px; font-size: 0.9rem; border-radius: 8px; cursor: pointer;">
                ${isStaged ? "✓ In Crate" : "+ Add to Crate"}
            </button>
        `;
    }
}

function previewFillerCrateTrack(trackId, title, artist, videoId, isDownloaded) {
    openHostVideoPreviewModal(title, artist, "📦 CRATE VIDEO PREVIEW");

    const localV = document.getElementById("previewLocalVideo");
    const iframe = document.getElementById("previewYoutubeIframe");

    if (isDownloaded && localV) {
        if (iframe) iframe.style.display = "none";
        localV.style.display = "block";
        localV.src = `/stream/filler/${trackId}`;
        localV.play().catch(() => {});
    } else if (videoId && iframe) {
        if (localV) localV.style.display = "none";
        iframe.style.display = "block";
        iframe.src = `https://www.youtube-nocookie.com/embed/${videoId}?autoplay=1`;
    }

    const actions = document.getElementById("previewModalActions");
    if (actions) {
        actions.innerHTML = `
            <button type="button" onclick="deleteFillerTrack(${trackId}); closeHostVideoPreview();" class="btn-ghost" style="padding: 10px 14px; font-size: 0.85rem; color: #ff5555; border-color: rgba(255,85,85,0.4); border-radius: 8px; cursor: pointer;">
                🗑️ Remove from Crate
            </button>
            <button type="button" onclick="forcePlayFillerTrack(${trackId}); closeHostVideoPreview();" class="btn-ghost" style="padding: 10px 14px; font-size: 0.85rem; color: var(--neon-gold); border-color: var(--neon-gold); border-radius: 8px; cursor: pointer;">
                ▶ Play on Stage
            </button>
            <button type="button" onclick="closeHostVideoPreview()" class="btn-ghost" style="padding: 10px 16px; font-size: 0.9rem; border-radius: 8px; cursor: pointer;">
                Close
            </button>
        `;
    }
}

function confirmHostServerShutdown() {
    if (!confirm("Are you sure you want to shut down Tiki Karaoke and stop all server processes?")) return;
    fetch("/api/host/shutdown", { method: "POST" })
        .then(res => res.json())
        .then(data => {
            alert(data.message || "Tiki Karaoke is shutting down. Aloha! 🌺");
            setTimeout(() => { location.reload(); }, 1200);
        })
        .catch(() => {
            alert("Shutdown command sent to server.");
        });
}

// -----------------------------------------------------------------
// Bring Your Own Media (BYOM) Local Importer
// -----------------------------------------------------------------
let hostDefaultPathsCache = null;

async function loadHostDefaultPaths() {
    try {
        const res = await fetch("/api/host/default-paths");
        if (res.ok) {
            const data = await res.json();
            hostDefaultPathsCache = data;
            const input = document.getElementById("hostImportFolderPath");
            if (input && !input.value) {
                input.value = data.default_music_dir || "";
            }
        }
    } catch (err) {
        console.warn("[BYOM] Could not load default paths:", err);
    }
}

function useHostPathPreset(presetType) {
    const input = document.getElementById("hostImportFolderPath");
    if (!input) return;
    if (presetType === "default") {
        if (hostDefaultPathsCache && hostDefaultPathsCache.default_music_dir) {
            input.value = hostDefaultPathsCache.default_music_dir;
        } else {
            input.value = "~/Music/Karaoke/Tiki";
        }
    } else if (presetType === "parent") {
        if (hostDefaultPathsCache && hostDefaultPathsCache.user_karaoke_parent) {
            input.value = hostDefaultPathsCache.user_karaoke_parent;
        } else {
            input.value = "~/Music/Karaoke";
        }
    }
}

async function launchSmartImporter() {
    const btn = document.getElementById("btnLaunchImporter");
    const statusDiv = document.getElementById("importerLaunchStatus");
    if (btn) {
        btn.disabled = true;
        btn.innerText = "⏳ Launching Importer...";
    }
    if (statusDiv) {
        statusDiv.style.display = "block";
        statusDiv.style.color = "var(--neon-cyan)";
        statusDiv.innerText = "⏳ Launching Smart Importer on host machine...";
    }
    try {
        const res = await fetch("/api/host/launch-importer", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-Host-Token": typeof HOST_TOKEN !== "undefined" ? HOST_TOKEN : ""
            }
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || data.message || "Failed to launch importer");
        if (statusDiv) {
            statusDiv.style.color = "var(--neon-lime)";
            statusDiv.innerText = "✅ Smart Importer is open on your desktop!";
        }
        showToast("✅ Smart Importer launched!");
    } catch (e) {
        if (statusDiv) {
            statusDiv.style.color = "var(--neon-pink)";
            statusDiv.innerText = `⚠️ ${e.message}`;
        }
        showToast(`⚠️ ${e.message}`);
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerText = "🚀 Launch Smart Media Importer";
        }
    }
}

async function launchCdgRipper() {
    const btn = document.getElementById("btnLaunchCdgRipper");
    const statusDiv = document.getElementById("importerLaunchStatus");
    if (btn) {
        btn.disabled = true;
        btn.innerText = "⏳ Launching Ripper...";
    }
    if (statusDiv) {
        statusDiv.style.display = "block";
        statusDiv.style.color = "var(--neon-gold)";
        statusDiv.innerText = "⏳ Launching CD+G Optical Disc Ripper on host machine...";
    }
    try {
        const res = await fetch("/api/host/launch-cdg-ripper", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-Host-Token": typeof HOST_TOKEN !== "undefined" ? HOST_TOKEN : ""
            }
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || data.message || "Failed to launch CD+G Ripper");
        if (statusDiv) {
            statusDiv.style.color = "var(--neon-lime)";
            statusDiv.innerText = "✅ CD+G Ripper window is open on your desktop!";
        }
        showToast("📀 CD+G Optical Disc Ripper launched!");
    } catch (e) {
        if (statusDiv) {
            statusDiv.style.color = "var(--neon-pink)";
            statusDiv.innerText = `⚠️ ${e.message}`;
        }
        showToast(`⚠️ ${e.message}`);
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerText = "📀 Launch CD+G Optical Disc Ripper";
        }
    }
}

async function submitHostMarquee(durationMinutes = 10) {
    const input = document.getElementById("hostMarqueeInput");
    const feedback = document.getElementById("marqueeBroadcastFeedback");
    const speedSelect = document.getElementById("hostMarqueeSpeedSelect");
    const speed = speedSelect ? parseInt(speedSelect.value, 10) : 85;
    const msg = input ? input.value.trim() : "";
    if (!msg) {
        showToast("⚠️ Please enter an announcement message to broadcast");
        if (input) input.focus();
        return;
    }
    try {
        const res = await fetch("/api/host/marquee", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-Host-Token": typeof HOST_TOKEN !== "undefined" ? HOST_TOKEN : ""
            },
            body: JSON.stringify({
                message: msg,
                duration_minutes: durationMinutes,
                mode: "announcement",
                speed: speed
            })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Failed to broadcast marquee message");
        if (feedback) {
            feedback.style.display = "block";
            feedback.style.color = "var(--neon-lime)";
            feedback.innerText = `📢 Broadcast active on TV Stage: "${msg}" (${durationMinutes === 0 ? 'All Night' : durationMinutes + ' mins'} • ${speed} px/s)`;
        }
        showToast("📢 Stage Marquee broadcast active!");
    } catch (e) {
        if (feedback) {
            feedback.style.display = "block";
            feedback.style.color = "var(--neon-pink)";
            feedback.innerText = `⚠️ ${e.message}`;
        }
        showToast(`⚠️ ${e.message}`);
    }
}

async function updateHostMarqueeSpeed(speedVal) {
    const speed = parseInt(speedVal, 10) || 85;
    try {
        const res = await fetch("/api/host/marquee/speed", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-Host-Token": typeof HOST_TOKEN !== "undefined" ? HOST_TOKEN : ""
            },
            body: JSON.stringify({ speed: speed })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Failed to update marquee speed");
        showToast(`⚡ Marquee crawl speed: ${speed} px/s`);
    } catch (e) {
        showToast(`⚠️ ${e.message}`);
    }
}

async function clearHostMarquee() {
    const input = document.getElementById("hostMarqueeInput");
    const feedback = document.getElementById("marqueeBroadcastFeedback");
    try {
        const res = await fetch("/api/host/marquee", {
            method: "DELETE",
            headers: {
                "Content-Type": "application/json",
                "X-Host-Token": typeof HOST_TOKEN !== "undefined" ? HOST_TOKEN : ""
            }
        });
        if (input) input.value = "";
        if (feedback) {
            feedback.style.display = "block";
            feedback.style.color = "var(--neon-cyan)";
            feedback.innerText = "Stage marquee broadcast cleared. TV will show standard queue ticker.";
        }
        showToast("Stage marquee cleared");
    } catch (e) {
        showToast(`⚠️ ${e.message}`);
    }
}

async function loadSingerRecordings() {
    const container = document.getElementById("singerRecordingsList");
    if (!container) return;
    if (!currentSinger || !currentSinger.token) {
        container.innerHTML = `<div style="text-align: center; color: var(--text-muted); font-size: 0.8rem; padding: 12px;">Log in or pick a nickname to access your live recording souvenirs.</div>`;
        return;
    }
    container.innerHTML = `<div style="text-align: center; color: var(--text-muted); font-size: 0.8rem; padding: 12px;">Loading your recordings...</div>`;
    try {
        const res = await fetch(`/api/singer/${currentSinger.token}/recordings`);
        if (!res.ok) throw new Error("Could not load recordings");
        const list = await res.json();
        container.innerHTML = "";
        if (!list || list.length === 0) {
            container.innerHTML = `
                <div style="text-align: center; padding: 16px; background: rgba(16,28,45,0.5); border-radius: 10px; border: 1px dashed rgba(255,255,255,0.15);">
                    <div style="font-size: 1.8rem; margin-bottom: 6px;">🎙️</div>
                    <div style="font-size: 0.85rem; color: #fff; font-weight: 600;">No live recordings yet!</div>
                    <div style="font-size: 0.75rem; color: var(--text-muted); margin-top: 4px;">Sing a song with our stage mics connected, and your souvenir MP3 will appear here instantly!</div>
                </div>
            `;
            return;
        }
        list.forEach(r => {
            const card = document.createElement("div");
            card.className = "tiki-card";
            card.style.marginBottom = "10px";
            card.style.padding = "12px";
            card.style.border = "1px solid rgba(255, 183, 3, 0.35)";

            const durationMins = r.duration_seconds ? `${Math.floor(r.duration_seconds / 60)}:${String(Math.floor(r.duration_seconds % 60)).padStart(2, '0')}` : "";
            const createdDate = r.created_at ? new Date(r.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : "";

            card.innerHTML = `
                <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 8px; margin-bottom: 8px;">
                    <div>
                        <div style="font-weight: 700; font-size: 0.95rem; color: #fff;">🎵 ${escapeHtml(r.title || 'Karaoke Performance')}</div>
                        <div style="font-size: 0.8rem; color: var(--neon-gold);">${escapeHtml(r.artist || 'Live at Tiki Lounge')}</div>
                        <div style="font-size: 0.72rem; color: var(--text-muted); margin-top: 2px;">Recorded at ${createdDate} ${durationMins ? `• ${durationMins}` : ''}</div>
                    </div>
                    <div style="display: flex; gap: 6px; flex-shrink: 0;">
                        <button onclick="generateSouvenirAlbumArt(${r.id}, '${escapeHtml(r.title || 'Karaoke Performance')}', '${escapeHtml(r.artist || 'Live at Tiki Lounge')}', '${escapeHtml(currentSinger ? currentSinger.name : 'Singer')}', '${currentSinger ? currentSinger.avatar : '🌺'}', '${createdDate}')" class="btn-ghost" style="padding: 6px 10px; font-size: 0.75rem; border-color: var(--neon-pink); color: var(--neon-pink); border-radius: 6px; display: flex; align-items: center; gap: 4px; white-space: nowrap;">
                            🎨 Art
                        </button>
                        <a href="/api/recordings/${r.id}/download" download class="btn-ghost" style="padding: 6px 10px; font-size: 0.75rem; border-color: var(--neon-cyan); color: var(--neon-cyan); text-decoration: none; border-radius: 6px; display: flex; align-items: center; gap: 4px; white-space: nowrap;">
                            ⬇️ MP3
                        </a>
                    </div>
                </div>
                <audio controls preload="none" style="width: 100%; height: 32px; border-radius: 6px; margin-top: 4px;">
                    <source src="/api/recordings/${r.id}/stream" type="audio/mpeg">
                    Your browser does not support audio playback.
                </audio>
            `;
            container.appendChild(card);
        });
    } catch (e) {
        container.innerHTML = `<div style="text-align: center; color: #ff5555; font-size: 0.8rem; padding: 12px;">Failed to load recordings: ${e.message}</div>`;
    }
}

// -------------------------------------------------------------
// Singer Rehearsal & Lyric Preview Engine
// -------------------------------------------------------------
let rehearsalIsMuted = true;

function openRehearsalModal(trackId, title, artist, videoId, isDownloaded) {
    const modal = document.getElementById("rehearsalPreviewModal");
    if (!modal) return;

    const titleEl = document.getElementById("rehearsalModalTitle");
    const artistEl = document.getElementById("rehearsalModalArtist");
    const localV = document.getElementById("rehearsalLocalVideo");
    const iframe = document.getElementById("rehearsalYoutubeIframe");
    const muteBtn = document.getElementById("rehearsalMuteToggleBtn");

    if (titleEl) titleEl.innerText = title || "Karaoke Song";
    if (artistEl) artistEl.innerText = artist || "";

    rehearsalIsMuted = true;
    if (muteBtn) {
        muteBtn.innerText = "🔇 Unmute Audio";
        muteBtn.style.color = "var(--neon-gold)";
        muteBtn.style.borderColor = "var(--neon-gold)";
    }

    if (isDownloaded && localV) {
        if (iframe) { iframe.src = ""; iframe.style.display = "none"; }
        localV.style.display = "block";
        localV.muted = true;
        localV.src = `/stream/${trackId}`;
        localV.play().catch(() => {});
    } else if (videoId && iframe) {
        if (localV) { localV.pause(); localV.removeAttribute("src"); localV.style.display = "none"; }
        iframe.style.display = "block";
        iframe.src = `https://www.youtube-nocookie.com/embed/${videoId}?autoplay=1&mute=1`;
    }

    modal.style.display = "flex";
}

function closeRehearsalModal() {
    const modal = document.getElementById("rehearsalPreviewModal");
    if (modal) modal.style.display = "none";

    const localV = document.getElementById("rehearsalLocalVideo");
    if (localV) {
        localV.pause();
        localV.removeAttribute("src");
        localV.load();
        localV.style.display = "none";
    }

    const iframe = document.getElementById("rehearsalYoutubeIframe");
    if (iframe) {
        iframe.src = "";
        iframe.style.display = "none";
    }
}

function toggleRehearsalMute() {
    rehearsalIsMuted = !rehearsalIsMuted;
    const localV = document.getElementById("rehearsalLocalVideo");
    const muteBtn = document.getElementById("rehearsalMuteToggleBtn");

    if (localV && localV.style.display !== "none") {
        localV.muted = rehearsalIsMuted;
    }
    if (muteBtn) {
        if (rehearsalIsMuted) {
            muteBtn.innerText = "🔇 Unmute Audio";
            muteBtn.style.color = "var(--neon-gold)";
            muteBtn.style.borderColor = "var(--neon-gold)";
        } else {
            muteBtn.innerText = "🔊 Mute Audio";
            muteBtn.style.color = "var(--neon-cyan)";
            muteBtn.style.borderColor = "var(--neon-cyan)";
        }
    }
}

// -------------------------------------------------------------
// Tiki Persona & Nickname Roller
// -------------------------------------------------------------
const TIKI_PERSONAS = [
    { name: "Captain Mai Tai", avatar: "🍹" },
    { name: "Neon Kahuna", avatar: "🌴" },
    { name: "Disco Flamingo", avatar: "🦩" },
    { name: "Velvet Volcano", avatar: "🌋" },
    { name: "The Ukulele Phantom", avatar: "🌺" },
    { name: "Sunset Sinatra", avatar: "🕶️" },
    { name: "Retro Rumrunner", avatar: "🥥" },
    { name: "Coco Loco", avatar: "🍍" },
    { name: "Lagoon Legend", avatar: "🏄" },
    { name: "Tropical Thunder", avatar: "⚡" },
    { name: "Bamboo Boogeyman", avatar: "🦜" },
    { name: "Island Idol", avatar: "👑" },
    { name: "Electric Hibiscus", avatar: "🌺" },
    { name: "Polynesian Prince", avatar: "🌴" }
];

function rollTikiPersona() {
    const input = document.getElementById("singerNameInput");
    if (!input) return;

    const pick = TIKI_PERSONAS[Math.floor(Math.random() * TIKI_PERSONAS.length)];
    input.value = pick.name;

    const options = document.querySelectorAll("#avatarPickerContainer .avatar-option");
    options.forEach(opt => {
        if (opt.dataset.avatar === pick.avatar) {
            options.forEach(o => o.classList.remove("selected"));
            opt.classList.add("selected");
        }
    });

    onSingerNameInput(pick.name);
    showToast(`🎲 Rolled Persona: ${pick.avatar} ${pick.name}!`);
}

// -------------------------------------------------------------
// Souvenir Retro Vinyl / Cassette Album Art Generator
// -------------------------------------------------------------
function generateSouvenirAlbumArt(recId, title, artist, singerName, singerAvatar, dateStr) {
    const modal = document.getElementById("souvenirAlbumModal");
    const imgEl = document.getElementById("souvenirAlbumImage");
    const downloadBtn = document.getElementById("souvenirDownloadBtn");
    if (!modal || !imgEl) return;

    const canvas = document.createElement("canvas");
    canvas.width = 1080;
    canvas.height = 1080;
    const ctx = canvas.getContext("2d");

    // 1. Synthwave Dark Tropical Background Gradient
    const bgGrad = ctx.createLinearGradient(0, 0, 1080, 1080);
    bgGrad.addColorStop(0, "#080612");
    bgGrad.addColorStop(0.5, "#15092a");
    bgGrad.addColorStop(1, "#031726");
    ctx.fillStyle = bgGrad;
    ctx.fillRect(0, 0, 1080, 1080);

    // 2. Vinyl Record Grooves Graphic
    const cx = 540;
    const cy = 460;
    ctx.save();
    for (let r = 400; r >= 160; r -= 18) {
        ctx.beginPath();
        ctx.arc(cx, cy, r, 0, Math.PI * 2);
        ctx.strokeStyle = "rgba(255, 255, 255, 0.04)";
        ctx.lineWidth = 1.5;
        ctx.stroke();
    }

    // Vinyl Outer Edge Glow
    ctx.beginPath();
    ctx.arc(cx, cy, 415, 0, Math.PI * 2);
    ctx.strokeStyle = "rgba(0, 240, 255, 0.35)";
    ctx.lineWidth = 4;
    ctx.stroke();

    // Vinyl Center Label
    const centerGrad = ctx.createRadialGradient(cx, cy, 30, cx, cy, 150);
    centerGrad.addColorStop(0, "#ff2a85");
    centerGrad.addColorStop(0.7, "#7928ca");
    centerGrad.addColorStop(1, "#180829");
    ctx.beginPath();
    ctx.arc(cx, cy, 150, 0, Math.PI * 2);
    ctx.fillStyle = centerGrad;
    ctx.fill();
    ctx.strokeStyle = "rgba(255, 183, 3, 0.6)";
    ctx.lineWidth = 3;
    ctx.stroke();

    // Center Spindle Hole
    ctx.beginPath();
    ctx.arc(cx, cy, 24, 0, Math.PI * 2);
    ctx.fillStyle = "#080612";
    ctx.fill();
    ctx.restore();

    // 3. Vintage Outer Cardboard Frame & Neon Border
    ctx.strokeStyle = "rgba(0, 240, 255, 0.5)";
    ctx.lineWidth = 14;
    ctx.strokeRect(40, 40, 1000, 1000);

    ctx.strokeStyle = "rgba(255, 42, 133, 0.6)";
    ctx.lineWidth = 3;
    ctx.strokeRect(58, 58, 964, 964);

    // 4. Vintage Header Badges
    ctx.fillStyle = "#ffb703";
    ctx.font = "bold 26px sans-serif";
    ctx.textAlign = "left";
    ctx.fillText("🌴 TIKI LOUNGE RECORDS • STEREO HIGH-FIDELITY", 80, 105);

    ctx.fillStyle = "#00f0ff";
    ctx.font = "bold 24px sans-serif";
    ctx.textAlign = "right";
    ctx.fillText("45 RPM • SOUVENIR EDITION", 1000, 105);

    // 5. Singer & Track Title Typography
    ctx.textAlign = "center";

    ctx.font = "60px sans-serif";
    ctx.fillText(singerAvatar || "🌺", cx, 360);

    ctx.fillStyle = "#ffffff";
    ctx.font = "bold 34px sans-serif";
    ctx.fillText(`FEATURING: ${singerName.toUpperCase()}`, cx, 415);

    ctx.fillStyle = "rgba(255,255,255,0.7)";
    ctx.font = "bold 20px sans-serif";
    ctx.fillText("SIDE A • LIVE STAGE RECORDING", cx, 450);

    // Bottom Track Info Plaque
    ctx.fillStyle = "rgba(8, 14, 24, 0.85)";
    ctx.fillRect(80, 860, 920, 130);
    ctx.strokeStyle = "rgba(255, 183, 3, 0.5)";
    ctx.lineWidth = 2;
    ctx.strokeRect(80, 860, 920, 130);

    // Track Title
    ctx.fillStyle = "#ffffff";
    ctx.font = "bold 44px sans-serif";
    const displayTitle = title.length > 28 ? title.substring(0, 26) + "..." : title;
    ctx.fillText(`"${displayTitle}"`, cx, 915);

    // Artist & Recording Info
    ctx.fillStyle = "#00f0ff";
    ctx.font = "26px sans-serif";
    ctx.fillText(`Originally by ${artist} • Recorded Live ${dateStr || ''}`, cx, 960);

    // Convert to Image Data
    const dataUrl = canvas.toDataURL("image/png");
    imgEl.src = dataUrl;
    if (downloadBtn) {
        downloadBtn.href = dataUrl;
        downloadBtn.download = `${title.replace(/[^a-zA-Z0-9]/g, '_')}_tiki_record.png`;
    }
    modal.style.display = "flex";
}

function closeSouvenirAlbumModal() {
    const modal = document.getElementById("souvenirAlbumModal");
    if (modal) modal.style.display = "none";
}

async function submitLocalKaraokeImport() {
    const folderInput = document.getElementById("hostImportFolderPath");
    const recursiveToggle = document.getElementById("hostImportRecursive");
    const singAlongToggle = document.getElementById("hostImportSingAlong");
    const submitBtn = document.getElementById("hostImportSubmitBtn");
    const resultBox = document.getElementById("hostImportResultContainer");

    const folderPath = folderInput ? folderInput.value.trim() : "";
    if (!folderPath) {
        showToast("⚠️ Please enter a folder path to scan");
        if (folderInput) folderInput.focus();
        return;
    }

    if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.innerHTML = "⏳ Scanning files & importing...";
    }
    if (resultBox) {
        resultBox.style.display = "block";
        resultBox.style.background = "rgba(0, 240, 255, 0.08)";
        resultBox.style.border = "1px solid rgba(0, 240, 255, 0.3)";
        resultBox.style.color = "#fff";
        resultBox.innerHTML = `🔍 Scanning <code>${folderPath}</code> for supported karaoke videos...`;
    }

    const destSelect = document.getElementById("hostImportDestination");
    const targetDest = destSelect ? destSelect.value : "karaoke";

    try {
        const res = await fetch("/api/host/import-local-library", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                folder_path: folderPath,
                recursive: recursiveToggle ? recursiveToggle.checked : true,
                is_sing_along: (targetDest === "sing_along"),
                target_destination: targetDest
            })
        });

        const data = await res.json();
        if (!res.ok) {
            throw new Error(data.detail || data.message || "Failed to import local folder");
        }

        if (resultBox) {
            resultBox.style.display = "block";
            if (data.imported_count > 0) {
                resultBox.style.background = "rgba(57, 255, 20, 0.1)";
                resultBox.style.border = "1px solid var(--neon-lime)";
                resultBox.style.color = "#dcfce7";
                resultBox.innerHTML = `
                    <div style="font-weight: 700; color: var(--neon-lime); margin-bottom: 4px;">🎉 Import Successful!</div>
                    <div>Imported <strong>${data.imported_count}</strong> of <strong>${data.total_found}</strong> media files into songbook.</div>
                    <div style="font-size: 0.72rem; color: var(--text-muted); margin-top: 4px;">Songs are now instantly searchable for all singers.</div>
                `;
                showToast(`🎉 Imported ${data.imported_count} karaoke songs!`);
                if (typeof loadLibraryTracks === "function") loadLibraryTracks();
                if (typeof loadFillerTracks === "function") loadFillerTracks();
            } else {
                resultBox.style.background = "rgba(255, 183, 3, 0.1)";
                resultBox.style.border = "1px solid var(--neon-gold)";
                resultBox.style.color = "#fef3c7";
                resultBox.innerHTML = `
                    <div style="font-weight: 700; color: var(--neon-gold); margin-bottom: 4px;">ℹ️ No New Tracks Found</div>
                    <div>${data.message || "No supported media files (.mp4, .mkv, .webm, .avi) found in directory."}</div>
                `;
                showToast("ℹ️ No new karaoke files found in folder");
            }
        }
    } catch (err) {
        console.error("[BYOM] Import error:", err);
        if (resultBox) {
            resultBox.style.display = "block";
            resultBox.style.background = "rgba(255, 85, 85, 0.1)";
            resultBox.style.border = "1px solid #ff5555";
            resultBox.style.color = "#fee2e2";
            resultBox.innerHTML = `
                <div style="font-weight: 700; color: #ff5555; margin-bottom: 4px;">❌ Import Error</div>
                <div>${err.message}</div>
            `;
        }
        showToast("❌ " + err.message);
    } finally {
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.innerHTML = "🚀 Scan & Import Local Songs";
        }
    }
}

// -------------------------------------------------------------
// Strict Commercial / BYOM Only Mode Controls
// -------------------------------------------------------------
async function loadHostStrictMode() {
    try {
        const res = await fetch("/api/host/strict-mode");
        if (res.ok) {
            const data = await res.json();
            syncHostStrictModeUI(data.strict_byom_only);
        }
    } catch (e) {
        console.error("[Host] Error loading strict mode:", e);
    }
}

function syncHostStrictModeUI(enabled) {
    const toggle = document.getElementById("hostStrictByomToggle");
    if (toggle) {
        toggle.checked = Boolean(enabled);
    }
}

async function toggleStrictByomMode(enabled) {
    try {
        const res = await fetch("/api/host/strict-mode", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ enabled: Boolean(enabled) })
        });
        if (res.ok) {
            const data = await res.json();
            syncHostStrictModeUI(data.strict_byom_only);
            if (data.strict_byom_only) {
                showToast("🔒 Curated Local Mode ON: Online requests disabled; only local crate active.");
            } else {
                showToast("🔓 Curated Local Mode OFF: Hybrid local crate & online requests active.");
            }
        } else {
            showToast("⚠️ Failed to update Strict Mode setting");
        }
    } catch (e) {
        console.error("[Host] Error setting strict mode:", e);
        showToast("⚠️ Failed to update Strict Mode setting");
    }
}

// -------------------------------------------------------------
// Offline Wi-Fi Hotspot & Host Network Hub
// -------------------------------------------------------------
async function loadHostNetworkStatus() {
    try {
        const res = await fetch("/api/host/network");
        if (res.ok) {
            const data = await res.json();
            updateHostNetworkUI(data);
        }
    } catch (e) {
        console.error("[Host] Error loading network status:", e);
    }
}

function updateHostNetworkUI(data) {
    if (!data) return;

    const modeBadge = document.getElementById("networkModeBadge");
    const currentUrl = document.getElementById("networkCurrentUrl");
    const ssidInput = document.getElementById("hotspotSsidInput");
    const passInput = document.getElementById("hotspotPassInput");

    if (modeBadge) {
        if (data.is_hotspot) {
            modeBadge.innerText = "📶 Offline Hotspot";
            modeBadge.style.color = "#ffaa00";
            modeBadge.style.background = "rgba(255, 170, 0, 0.15)";
        } else if (data.is_internet_connected) {
            modeBadge.innerText = "🌐 Online Wi-Fi";
            modeBadge.style.color = "var(--neon-lime)";
            modeBadge.style.background = "rgba(0, 255, 100, 0.15)";
        } else {
            modeBadge.innerText = "🏠 Local Subnet";
            modeBadge.style.color = "var(--neon-cyan)";
            modeBadge.style.background = "rgba(0, 240, 255, 0.15)";
        }
    }

    if (currentUrl && data.join_url) {
        currentUrl.innerText = data.join_url;
    }

    const toggle = document.getElementById("hotspotEnabledToggle");
    if (toggle && data.hotspot_enabled !== undefined) {
        toggle.checked = !!data.hotspot_enabled;
    }

    if (ssidInput && data.hotspot_ssid !== undefined && !ssidInput.matches(":focus")) {
        ssidInput.value = data.hotspot_ssid;
    }
    if (passInput && data.hotspot_password !== undefined && !passInput.matches(":focus")) {
        passInput.value = data.hotspot_password;
    }

    const showWifi = !!data.show_wifi_card;
    const openBtn = document.getElementById("openWifiQrBtn");
    if (openBtn) {
        openBtn.innerText = showWifi ? "📱 Show Dual Wi-Fi & Songbook QR" : "📱 Show Songbook QR Code";
    }

    // Modal elements
    const modalWifiSection = document.getElementById("modalWifiQrSection");
    const modalWifiDetail = document.getElementById("modalWifiDetailBox");
    const modalAppTitle = document.getElementById("modalAppTitleText");
    const modalWifiQr = document.getElementById("modalWifiQrImg");
    const modalAppQr = document.getElementById("modalAppQrImg");
    const modalWifiSsidText = document.getElementById("modalWifiSsidText");
    const modalAppUrlText = document.getElementById("modalAppUrlText");
    const modalWifiSsidDetail = document.getElementById("modalWifiSsidDetail");
    const modalWifiPassDetail = document.getElementById("modalWifiPassDetail");

    if (modalWifiSection) modalWifiSection.style.display = showWifi ? "block" : "none";
    if (modalWifiDetail) modalWifiDetail.style.display = showWifi ? "block" : "none";
    if (modalAppTitle) modalAppTitle.innerText = showWifi ? "2. OPEN SONGBOOK" : "📱 SCAN TO OPEN SONGBOOK";

    const t = Date.now();
    if (modalWifiQr && showWifi) modalWifiQr.src = `/qr/wifi?t=${t}`;
    if (modalAppQr) modalAppQr.src = `/qr/public?t=${t}`;
    if (modalWifiSsidText) modalWifiSsidText.innerText = data.hotspot_ssid || "";
    if (modalAppUrlText) modalAppUrlText.innerText = data.join_url ? new URL(data.join_url).pathname : "/join";
    if (modalWifiSsidDetail) modalWifiSsidDetail.innerText = data.hotspot_ssid || "";
    if (modalWifiPassDetail) modalWifiPassDetail.innerText = data.hotspot_password || "";

    const hostPinEl = document.getElementById("hostPairingPinText");
    if (hostPinEl && data.host_pin) {
        hostPinEl.innerText = data.host_pin;
    }
    const hostPairQr = document.getElementById("hostPairingQrImg");
    if (hostPairQr) {
        hostPairQr.src = `/qr/host?t=${t}`;
    }

    document.querySelectorAll('img[src*="/qr/"]').forEach(img => {
        const base = img.src.split('?')[0];
        img.src = `${base}?t=${t}`;
    });
}

async function saveHotspotConfig() {
    const ssid = document.getElementById("hotspotSsidInput")?.value?.trim() || "";
    const password = document.getElementById("hotspotPassInput")?.value?.trim() || "";
    const enabled = document.getElementById("hotspotEnabledToggle") ? document.getElementById("hotspotEnabledToggle").checked : false;

    try {
        const res = await fetch("/api/host/network/hotspot-config", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ssid, password, enabled })
        });
        if (res.ok) {
            const data = await res.json();
            updateHostNetworkUI(data);
            showToast("💾 Hotspot Wi-Fi settings saved & QR refreshed! 🌺");
        } else {
            showToast("⚠️ Failed to save hotspot settings");
        }
    } catch (e) {
        console.error("[Host] Error saving hotspot config:", e);
        showToast("⚠️ Network error saving hotspot settings");
    }
}

function openWifiQrModal() {
    const modal = document.getElementById("wifiQrModal");
    if (modal) {
        modal.style.display = "flex";
        loadHostNetworkStatus();
    }
}

function closeWifiQrModal() {
    const modal = document.getElementById("wifiQrModal");
    if (modal) {
        modal.style.display = "none";
    }
}

function openHostPairingQrModal() {
    const modal = document.getElementById("hostPairingQrModal");
    if (modal) {
        modal.style.display = "flex";
        loadHostNetworkStatus();
    }
}

function closeHostPairingQrModal() {
    const modal = document.getElementById("hostPairingQrModal");
    if (modal) {
        modal.style.display = "none";
    }
}

async function submitRoomCodeVerify() {
    const input = document.getElementById("roomCodeInput");
    const errEl = document.getElementById("roomCodeErrorMsg");
    const btn = document.getElementById("roomCodeSubmitBtn");
    const code = (input ? input.value : "").trim().toUpperCase();
    if (!code) {
        if (errEl) {
            errEl.innerText = "Please enter the Room Code shown on the TV screen.";
            errEl.style.display = "block";
        }
        return;
    }
    if (btn) btn.disabled = true;
    try {
        const res = await fetch("/api/room/verify", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ code })
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.verified) {
            window.TIKI_ROOM_VERIFIED = true;
            const modal = document.getElementById("roomCodeModal");
            if (modal) modal.style.display = "none";
            if (errEl) errEl.style.display = "none";
            window.location.reload();
        } else {
            if (errEl) {
                errEl.innerText = data.detail || "Invalid Room Code. Please check the TV screen.";
                errEl.style.display = "block";
            }
        }
    } catch (e) {
        if (errEl) {
            errEl.innerText = "Network error verifying Room Code.";
            errEl.style.display = "block";
        }
    } finally {
        if (btn) btn.disabled = false;
    }
}

async function rotateHostRoomCode() {
    try {
        const res = await fetch("/api/host/room-code/rotate", { method: "POST" });
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.room_code) {
            showToast(`🔄 New Room Code active on TV: ${data.room_code}`);
        } else {
            showToast(data.detail || "Failed to rotate Room Code", true);
        }
    } catch (e) {
        showToast("Network error rotating Room Code", true);
    }
}

