/**
 * Tiki Karaoke Mobile Client (Smartphones)
 * Manages singer identity, debounced tiered search, key selection,
 * live WebSocket queue updates, crowd emoji reactions, and Host DJ controls.
 */

let currentSinger = null;
let isHost = false;
let ws = null;
let searchDebounceTimer = null;
let selectedTrackForQueue = null;
let selectedPitch = 0;
let activeDuplicateInfo = null;
let lastAlertedQueueId = null;
let currentAvSyncOffset = 0;
let lastQueuedSongTime = 0;
let lastQueuedSongId = null;
let isSingAlongMode = false;

document.addEventListener("DOMContentLoaded", () => {
    initSingAlongMode();
    checkIdentity();
    initWebSocket();
    setupNavigation();
    setupSearch();
    loadLiveQueue();
    loadLibraryTracks();
    loadHostStrictMode();
});

function initSingAlongMode() {
    const cookieVal = getCookie("tiki_sing_along");
    if (cookieVal !== null) {
        isSingAlongMode = (cookieVal === "1" || cookieVal === "true");
    } else {
        const localVal = localStorage.getItem("tiki_sing_along");
        isSingAlongMode = (localVal === "1" || localVal === "true");
    }
    syncSingAlongToggleUI();
}

function syncSingAlongToggleUI() {
    const tSearch = document.getElementById("singAlongToggleSearch");
    if (tSearch) tSearch.checked = isSingAlongMode;
    const tLib = document.getElementById("singAlongToggleLibrary");
    if (tLib) tLib.checked = isSingAlongMode;
}

function onSingAlongToggleChange(checked) {
    isSingAlongMode = !!checked;
    setCookie("tiki_sing_along", isSingAlongMode ? "1" : "0", 365);
    localStorage.setItem("tiki_sing_along", isSingAlongMode ? "1" : "0");
    syncSingAlongToggleUI();

    const searchInput = document.getElementById("searchInput");
    if (searchInput && searchInput.value.trim().length > 0) {
        performSearch(searchInput.value.trim());
    }

    if (typeof loadLibraryTracks === "function") {
        currentLibPage = 1;
        loadLibraryTracks();
    }
}

// Identity & Session
function checkIdentity() {
    let token = localStorage.getItem("tiki_token") || getCookie("tiki_token");
    let name = localStorage.getItem("tiki_name") || getCookie("tiki_name");
    let avatar = localStorage.getItem("tiki_avatar") || getCookie("tiki_avatar") || "🌺";

    if (!token || token === "undefined" || token === "null") {
        token = null;
        localStorage.removeItem("tiki_token");
        setCookie("tiki_token", "", -1);
    }
    if (!name || name === "undefined" || name === "null") {
        name = null;
        localStorage.removeItem("tiki_name");
        setCookie("tiki_name", "", -1);
    }

    if (token && name) {
        currentSinger = { token, name, avatar };
        updateSingerHeader();
        registerSingerWithServer();
    } else if (token) {
        // Token was preserved in cookie or storage, restore profile from server!
        fetch(`/api/singer/${token}/profile`)
            .then(res => res.ok ? res.json() : null)
            .then(prof => {
                if (prof && prof.name) {
                    currentSinger = prof;
                    localStorage.setItem("tiki_token", prof.token);
                    localStorage.setItem("tiki_name", prof.name);
                    localStorage.setItem("tiki_avatar", prof.avatar || "🌺");
                    setCookie("tiki_token", prof.token, 365);
                    setCookie("tiki_name", prof.name, 365);
                    setCookie("tiki_avatar", prof.avatar || "🌺", 365);
                    updateSingerHeader();
                    registerSingerWithServer();
                } else {
                    currentSinger = null;
                    updateSingerHeader();
                    showOnboardingModal();
                }
            })
            .catch(() => {
                currentSinger = null;
                updateSingerHeader();
                showOnboardingModal();
            });
    } else {
        currentSinger = null;
        updateSingerHeader();
        const urlP = new URLSearchParams(window.location.search);
        const isHostParam = urlP.get("tab") === "host" || urlP.get("tab") === "hostTab" || Boolean(getCookie("tiki_host"));
        if (!isHostParam) {
            showOnboardingModal();
        }
    }

    // Check if host
    checkRole();
}

function checkRole() {
    return fetch("/api/auth/role")
        .then(res => res.json())
        .then(data => {
            isHost = !!data.is_host;
            const hostTab = document.getElementById("hostNavTab");
            if (hostTab) {
                hostTab.style.display = isHost ? "flex" : "none";
            }
            const hostRow = document.getElementById("hostNavRow");
            if (hostRow) {
                hostRow.style.display = isHost ? "flex" : "none";
            }
            document.body.classList.toggle("host-mode-active", isHost);
            if (isHost) {
                const onboarding = document.getElementById("onboardingModal");
                if (onboarding) onboarding.style.display = "none";
                loadHostSettings();
                loadHostDisplays();
                loadHostStorageStatus();
                checkYtdlpStatus();
                loadVenueAvSync();
                checkHostHealth();
                loadHostStrictMode();
                loadHostNetworkStatus();
                loadPartyPacks();

                const urlParams = new URLSearchParams(window.location.search);
                const reqTab = urlParams.get("tab");
                if (reqTab === "host" || reqTab === "hostTab") {
                    switchTab("hostTab");
                }
            }
            return isHost;
        })
        .catch(err => {
            console.error("[Auth] Role check failed:", err);
            return false;
        });
}

function openHostLoginModal() {
    if (isHost) {
        showToast("👑 You are already authorized as Host DJ!");
        switchTab("host");
        return;
    }
    const modal = document.getElementById("hostLoginModal");
    const pinInput = document.getElementById("hostPinInput");
    const err = document.getElementById("hostPinErrorMsg");
    if (err) err.style.display = "none";
    if (pinInput) pinInput.value = "";
    if (modal) {
        modal.style.display = "flex";
        setTimeout(() => pinInput && pinInput.focus(), 150);
    }
}

function closeHostLoginModal() {
    const modal = document.getElementById("hostLoginModal");
    if (modal) modal.style.display = "none";
}

async function submitHostPinLogin() {
    const pinInput = document.getElementById("hostPinInput");
    const err = document.getElementById("hostPinErrorMsg");
    const btn = document.getElementById("hostPinSubmitBtn");
    const pin = (pinInput ? pinInput.value : "").trim();

    if (!pin) {
        if (err) {
            err.innerText = "Please enter the PIN.";
            err.style.display = "block";
        }
        return;
    }

    if (btn) btn.disabled = true;
    if (err) err.style.display = "none";

    try {
        const res = await fetch("/api/auth/pin", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ pin: pin })
        });

        if (res.ok) {
            closeHostLoginModal();
            showToast("👑 Host DJ privileges unlocked!");
            await checkRole();
            reconnectWebSocket();
            switchTab("host");
        } else {
            const data = await res.json().catch(() => ({}));
            const msg = data.detail || (res.status === 429 ? "Too many failed attempts. Please wait 1 minute." : "Invalid Host PIN.");
            if (err) {
                err.innerText = msg;
                err.style.display = "block";
            }
        }
    } catch (e) {
        if (err) {
            err.innerText = "Connection error. Please try again.";
            err.style.display = "block";
        }
    } finally {
        if (btn) btn.disabled = false;
    }
}

function registerSingerWithServer() {
    if (!currentSinger || !currentSinger.token || !currentSinger.name) return;
    fetch("/api/singer/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(currentSinger)
    })
    .then(async res => {
        if (!res.ok) {
            console.warn("[Identity] Registration conflict or error on boot. Re-opening onboarding.");
            localStorage.removeItem("tiki_token");
            localStorage.removeItem("tiki_name");
            currentSinger = null;
            updateSingerHeader();
            showOnboardingModal();
            return null;
        }
        return res.json();
    })
    .then(data => {
        if (data && data.token && data.name && data.name !== "undefined") {
            currentSinger = data;
            localStorage.setItem("tiki_token", data.token);
            localStorage.setItem("tiki_name", data.name);
            localStorage.setItem("tiki_avatar", data.avatar || "🌺");
            setCookie("tiki_token", data.token, 365);
            setCookie("tiki_name", data.name, 365);
            setCookie("tiki_avatar", data.avatar || "🌺", 365);
            updateSingerHeader();
        }
    })
    .catch(err => {
        console.error("[Identity] Failed to register with server:", err);
    });
}

let avatarCheckDebounceTimer = null;

function showOnboardingModal() {
    const modal = document.getElementById("onboardingModal");
    const nameInput = document.getElementById("singerNameInput");
    const errEl = document.getElementById("nameErrorMsg");
    if (errEl) errEl.style.display = "none";

    let initialName = (currentSinger && currentSinger.name && currentSinger.name !== "undefined")
        ? currentSinger.name
        : (localStorage.getItem("tiki_name") && localStorage.getItem("tiki_name") !== "undefined" ? localStorage.getItem("tiki_name") : "");

    if (nameInput) nameInput.value = initialName;

    const currentAv = (currentSinger && currentSinger.avatar) || localStorage.getItem("tiki_avatar") || "🌺";
    document.querySelectorAll(".avatar-option").forEach(opt => {
        if (opt.dataset.avatar === currentAv) {
            opt.classList.add("selected");
        } else {
            opt.classList.remove("selected");
        }
    });

    if (initialName) {
        checkAvailableAvatarsForName(initialName);
    } else {
        document.querySelectorAll(".avatar-option").forEach(opt => opt.classList.remove("disabled-avatar"));
    }

    const leaveBtnContainer = document.getElementById("singerLeavePartyContainer");
    if (leaveBtnContainer) {
        leaveBtnContainer.style.display = (currentSinger && currentSinger.name) ? "block" : "none";
    }

    if (modal) modal.style.display = "flex";
}

function selectAvatar(el) {
    if (!el || el.classList.contains("disabled-avatar")) return;
    document.querySelectorAll(".avatar-option").forEach(opt => opt.classList.remove("selected"));
    el.classList.add("selected");
}

function onSingerNameInput(val) {
    clearTimeout(avatarCheckDebounceTimer);
    avatarCheckDebounceTimer = setTimeout(() => {
        checkAvailableAvatarsForName(val);
    }, 250);
}

function checkAvailableAvatarsForName(name) {
    const cleanName = (name || "").trim();
    const token = (currentSinger && currentSinger.token) || localStorage.getItem("tiki_token") || "";
    const errEl = document.getElementById("nameErrorMsg");
    const submitBtn = document.getElementById("onboardingSubmitBtn");

    if (!cleanName) {
        document.querySelectorAll(".avatar-option").forEach(opt => opt.classList.remove("disabled-avatar"));
        if (errEl) errEl.style.display = "none";
        if (submitBtn) submitBtn.disabled = false;
        return;
    }

    fetch(`/api/singer/available-avatars?name=${encodeURIComponent(cleanName)}&token=${encodeURIComponent(token)}`)
        .then(res => res.json())
        .then(data => {
            const taken = Array.isArray(data.taken_avatars) ? data.taken_avatars : [];
            const avatarEls = document.querySelectorAll(".avatar-option");
            let availableCount = 0;

            avatarEls.forEach(el => {
                const av = el.dataset.avatar;
                if (taken.includes(av)) {
                    el.classList.add("disabled-avatar");
                    el.classList.remove("selected");
                } else {
                    el.classList.remove("disabled-avatar");
                    availableCount++;
                }
            });

            // If selected avatar became disabled, select the first available avatar
            const hasSelected = document.querySelector(".avatar-option.selected:not(.disabled-avatar)");
            if (!hasSelected && availableCount > 0) {
                const firstAvailable = document.querySelector(".avatar-option:not(.disabled-avatar)");
                if (firstAvailable) firstAvailable.classList.add("selected");
            }

            if (availableCount === 0) {
                if (errEl) {
                    errEl.innerText = `⚠️ All tropical icons for "${cleanName}" are claimed! Please choose another nickname or add an initial.`;
                    errEl.style.display = "block";
                }
                if (submitBtn) submitBtn.disabled = true;
            } else {
                if (errEl) errEl.style.display = "none";
                if (submitBtn) submitBtn.disabled = false;
            }
        })
        .catch(err => {
            console.error("[AvatarCheck] Error checking avatars:", err);
        });
}

function completeOnboarding() {
    const nameInput = document.getElementById("singerNameInput").value.trim();
    const errEl = document.getElementById("nameErrorMsg");
    if (errEl) errEl.style.display = "none";

    if (!nameInput || nameInput.toLowerCase() === "undefined" || nameInput.toLowerCase() === "null") {
        if (errEl) {
            errEl.innerText = "Please enter a valid nickname!";
            errEl.style.display = "block";
        } else {
            alert("Please enter a valid nickname!");
        }
        return;
    }

    const selectedEl = document.querySelector(".avatar-option.selected:not(.disabled-avatar)");
    if (!selectedEl) {
        if (errEl) {
            errEl.innerText = "Please choose an available avatar icon!";
            errEl.style.display = "block";
        } else {
            alert("Please choose an available avatar icon!");
        }
        return;
    }
    const selectedAvatar = selectedEl.dataset.avatar || "🌺";
    
    let existingToken = (currentSinger && currentSinger.token && currentSinger.token !== "undefined" && currentSinger.token !== "null")
        ? currentSinger.token
        : (localStorage.getItem("tiki_token") && localStorage.getItem("tiki_token") !== "undefined" ? localStorage.getItem("tiki_token") : null);

    if (!existingToken) {
        existingToken = "usr_" + Math.random().toString(36).substring(2, 12);
    }

    const candidate = {
        token: existingToken,
        name: nameInput,
        avatar: selectedAvatar
    };

    fetch("/api/singer/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(candidate)
    })
    .then(async res => {
        if (!res.ok) {
            const errData = await res.json().catch(() => ({}));
            throw new Error(errData.detail || "That avatar is already taken for this name! Please choose another.");
        }
        return res.json();
    })
    .then(data => {
        if (!data || !data.token || !data.name || data.name === "undefined") {
            throw new Error("Invalid singer registration received from server.");
        }
        currentSinger = data;
        localStorage.setItem("tiki_token", data.token);
        localStorage.setItem("tiki_name", data.name);
        localStorage.setItem("tiki_avatar", data.avatar || "🌺");
        setCookie("tiki_token", data.token, 365);
        setCookie("tiki_name", data.name, 365);
        setCookie("tiki_avatar", data.avatar || "🌺", 365);

        document.getElementById("onboardingModal").style.display = "none";
        updateSingerHeader();
        showToast(`Welcome to the party, ${data.name}! ${data.avatar}`);
    })
    .catch(err => {
        if (errEl) {
            errEl.innerText = `⚠️ ${err.message}`;
            errEl.style.display = "block";
        } else {
            alert(err.message);
        }
        checkAvailableAvatarsForName(nameInput);
    });
}

function updateSingerHeader() {
    const badge = document.getElementById("headerSingerBadge");
    if (!badge) return;
    if (currentSinger && currentSinger.name && currentSinger.name !== "undefined") {
        const isOnBreak = currentSinger.status === "on_break";
        badge.innerHTML = `${currentSinger.avatar || '🌺'} ${escapeHtml(currentSinger.name)} ${isOnBreak ? '<span style="color:var(--neon-gold); font-size:0.75rem; margin-left: 4px;">(Paused ⏸️)</span>' : ''}`;
    } else {
        badge.innerText = "🌺 Tap to Set Nickname";
    }
    renderSingerStatusBanner();
}

function renderSingerStatusBanner() {
    const banner = document.getElementById("singerSelfStatusBanner");
    if (!banner) return;
    if (!currentSinger || !currentSinger.token) {
        banner.innerHTML = "";
        return;
    }
    const isOnBreak = currentSinger.status === "on_break";
    if (isOnBreak) {
        banner.innerHTML = `
            <div style="background: rgba(255, 183, 3, 0.12); border: 1px solid var(--neon-gold); border-radius: 12px; padding: 12px 14px; margin-bottom: 14px; display: flex; justify-content: space-between; align-items: center; gap: 10px; box-shadow: 0 0 15px rgba(255, 183, 3, 0.15);">
                <div>
                    <div style="font-weight: 700; font-size: 0.9rem; color: var(--neon-gold); display: flex; align-items: center; gap: 6px;">
                        <span>⏸️</span> <span>Your Stage Turn is Paused</span>
                    </div>
                    <div style="font-size: 0.74rem; color: #cbd5e1; margin-top: 2px;">Your songs stay in queue without losing your spot. Stage will skip to active singers until you tap ready!</div>
                </div>
                <button onclick="toggleSingerSelfStatus('active')" class="btn-ghost" style="padding: 8px 14px; font-size: 0.82rem; font-weight: 700; background: rgba(57, 255, 20, 0.15); color: var(--neon-lime); border-color: var(--neon-lime); border-radius: 8px; white-space: nowrap; cursor: pointer;">
                    🎤 I'm Ready!
                </button>
            </div>
        `;
    } else {
        banner.innerHTML = `
            <div style="display: flex; justify-content: flex-end; margin-bottom: 10px;">
                <button onclick="toggleSingerSelfStatus('on_break')" class="btn-ghost" style="padding: 4px 10px; font-size: 0.75rem; border-color: rgba(255,183,3,0.4); color: var(--neon-gold); border-radius: 8px; display: inline-flex; align-items: center; gap: 5px; cursor: pointer;">
                    <span>🍹</span> <span>Step Away (Pause My Turn)</span>
                </button>
            </div>
        `;
    }
}

async function toggleSingerSelfStatus(forcedStatus = null) {
    if (!currentSinger || !currentSinger.token) {
        showOnboardingModal();
        return;
    }
    const currentStatus = currentSinger.status || "active";
    const nextStatus = forcedStatus || (currentStatus === "active" ? "on_break" : "active");
    try {
        const res = await fetch(`/api/singer/${currentSinger.token}/status`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ status: nextStatus })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Failed to update your status");
        currentSinger.status = nextStatus;
        updateSingerHeader();
        loadLiveQueue();
        if (nextStatus === "on_break") {
            showToast("🍹 You're on break! Your turn in queue is paused until you're back.");
        } else {
            showToast("🎤 Welcome back! You are active in the rotation.");
        }
    } catch (e) {
        showToast(`⚠️ ${e.message}`);
    }
}

// WebSocket Live Updates
let wsPingInterval = null;
let wsReconnectTimeout = null;

function initWebSocket() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws`;

    if (wsPingInterval) {
        clearInterval(wsPingInterval);
        wsPingInterval = null;
    }
    if (wsReconnectTimeout) {
        clearTimeout(wsReconnectTimeout);
        wsReconnectTimeout = null;
    }

    try {
        if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
            ws.onclose = null;
            ws.onerror = null;
            ws.close();
        }
    } catch (e) {}

    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        const pill = document.getElementById("reconnectingPill");
        if (pill) pill.style.display = "none";

        // Keepalive heartbeat every 10s to prevent router/tunnel drops on mobile
        wsPingInterval = setInterval(() => {
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ type: "ping" }));
            }
        }, 10000);

        // Sync fresh queue upon connection or reconnection
        loadLiveQueue();
    };

    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            if (data.type === "pong") {
                return;
            } else if (data.type === "queue_updated" || data.type === "play_track") {
                loadLiveQueue();
                if (isHost) {
                    loadHostSingerRoster();
                }
            } else if (data.type === "pending_downloads_updated") {
                currentPendingDownloads = data.pending_downloads || [];
                renderQueue(currentQueueCache);
            } else if (data.type === "download_progress") {
                if (data.pending_downloads) {
                    currentPendingDownloads = data.pending_downloads;
                } else if (data.pending_id) {
                    const item = currentPendingDownloads.find(p => p.id === data.pending_id);
                    if (item) {
                        item.progress = data.progress;
                        item.status = data.status;
                    }
                }
                renderQueue(currentQueueCache);
            } else if (data.type === "host_alert") {
                if (isHost) {
                    showToast(`⚠️ ${data.message}`);
                }
            } else if (data.type === "singer_status_updated") {
                if (currentSinger && (data.singer_id === currentSinger.id || data.token === currentSinger.token)) {
                    currentSinger.status = data.status;
                    updateSingerHeader();
                }
                if (isHost) {
                    loadHostSingerRoster();
                }
                loadLiveQueue();
            } else if (data.type === "recording_ready") {
                showToast(`🎙️ Performance recording ready: "${data.track_title || 'Your Song'}"! Check My Songbook to stream or download MP3 souvenir!`);
                const recList = document.getElementById("singerRecordingsList");
                if (recList) {
                    loadSingerRecordings();
                }
            } else if (data.type === "displays_updated") {
                if (isHost) {
                    loadHostDisplays();
                }
            } else if (data.type === "library_updated") {
                loadLibraryTracks();
                if (isHost) {
                    loadHostStorageStatus();
                    loadPartyPacks();
                }
            } else if (data.type === "filler_tracks_updated" || data.type === "filler_track_updated") {
                if (isHost) {
                    loadFillerTracks();
                }
            } else if (data.type === "filler_settings_updated") {
                if (isHost) {
                    loadFillerSettings(data.settings);
                }
            } else if (data.type === "singer_on_deck") {
                handleSingerOnDeckMessage(data);
            } else if (data.type === "host_mic_settings") {
                if (isHost) {
                    syncHostMicSettingsUI(data);
                }
            } else if (data.type === "strict_mode_updated") {
                if (isHost) {
                    syncHostStrictModeUI(data.strict_byom_only);
                }
            } else if (data.type === "network_updated") {
                if (isHost) {
                    loadHostNetworkStatus();
                }
            } else if (data.type === "server_shutting_down") {
                showToast("🛑 Host closed the session. Aloha! 🌺");
                setTimeout(() => {
                    document.body.innerHTML = '<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh;background:#05080f;color:#00f0ff;font-family:sans-serif;text-align:center;padding:20px;"><h1>🌴 Party Closed</h1><p style="color:#aaa;font-size:1.1rem;margin-top:12px;">Tiki Karaoke has been shut down by the host.<br>Aloha and see you next time! 🌺</p></div>';
                }, 1000);
            }
        } catch (e) {
            console.error("[WS] Error:", e);
        }
    };

    ws.onclose = () => {
        const pill = document.getElementById("reconnectingPill");
        if (pill) pill.style.display = "flex";
        if (wsPingInterval) {
            clearInterval(wsPingInterval);
            wsPingInterval = null;
        }
        if (!wsReconnectTimeout) {
            wsReconnectTimeout = setTimeout(() => {
                wsReconnectTimeout = null;
                initWebSocket();
            }, 2000);
        }
    };

    ws.onerror = () => {
        const pill = document.getElementById("reconnectingPill");
        if (pill) pill.style.display = "flex";
    };
}

function reconnectWebSocket() {
    if (wsReconnectTimeout) {
        clearTimeout(wsReconnectTimeout);
        wsReconnectTimeout = null;
    }
    if (ws) {
        try {
            ws.onclose = null;
            ws.close();
        } catch (e) {}
    }
    initWebSocket();
}

// Wi-Fi Self-Healing & Tab Wake Synchronization
window.addEventListener("online", () => {
    console.log("[Network] Online event detected. Resyncing WebSocket and Queue...");
    reconnectWebSocket();
    loadLiveQueue();
});

document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
        console.log("[App] Tab restored to foreground. Checking connection & Queue...");
        if (!ws || ws.readyState !== WebSocket.OPEN) {
            reconnectWebSocket();
        }
        loadLiveQueue();
    }
});

function switchTab(tabName) {
    if (!tabName) return;
    const target = tabName.endsWith("Tab") ? tabName : (tabName + "Tab");
    const btn = document.querySelector(`.nav-tab-btn[data-tab="${target}"]`) || document.querySelector(`.nav-tab-btn[data-tab="${tabName}"]`);
    if (btn) {
        btn.click();
        return;
    }
    // Fallback if button not found or hidden
    document.querySelectorAll(".nav-tab-btn[data-tab]").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".tab-view").forEach(v => v.style.display = "none");
    const targetEl = document.getElementById(target) || document.getElementById(tabName);
    if (targetEl) targetEl.style.display = "block";
}

// Navigation Tabs
function setupNavigation() {
    document.querySelectorAll(".nav-tab-btn[data-tab]").forEach(btn => {
        btn.addEventListener("click", () => {
            const target = btn.dataset.tab;
            if (!target) return;

            document.querySelectorAll(".nav-tab-btn[data-tab]").forEach(b => b.classList.remove("active"));
            document.querySelectorAll(".tab-view").forEach(v => v.style.display = "none");

            btn.classList.add("active");
            const targetEl = document.getElementById(target);
            if (targetEl) targetEl.style.display = "block";

            if (target === "libraryTab") loadLibraryTracks();
            if (target === "queueTab") loadLiveQueue();
            if (target === "historyTab") loadHistory();
            if (target === "hostTab") {
                loadHostSingerRoster();
                loadHostDisplays();
                loadHostStorageStatus();
                checkYtdlpStatus();
                loadVenueAvSync();
                loadHostDefaultPaths();
                loadHostStrictMode();
                loadHostNetworkStatus();
                loadPartyPacks();
            }
            if (target === "fillerCrateTab") {
                loadFillerTracks();
            }
            if (target === "fillerSearchTab") {
                const fInput = document.getElementById("fillerSearchInput");
                if (fInput) fInput.focus();
            }
        });
    });
}

function openStageTab(event) {
    if (event) {
        event.preventDefault();
        event.stopPropagation();
    }
    const hostToken = localStorage.getItem("tiki_host_token") || "";
    let stageUrl = "/stage";
    if (isHost && hostToken) {
        stageUrl = `/stage?token=${encodeURIComponent(hostToken)}`;
    } else if (isHost) {
        stageUrl = "/stage";
    } else {
        stageUrl = "/stage?mute=1";
    }
    window.open(stageUrl, "_blank");
}

// Tiered Search & Queue
function setupSearch() {
    const input = document.getElementById("searchInput");
    const clearBtn = document.getElementById("searchClearBtn");
    if (!input) return;

    const updateClearBtn = () => {
        if (clearBtn) {
            clearBtn.style.display = input.value.length > 0 ? "flex" : "none";
        }
    };

    input.addEventListener("input", () => {
        updateClearBtn();
        clearTimeout(searchDebounceTimer);
        const query = input.value.trim();
        if (query.length < 2) {
            const resultsContainer = document.getElementById("searchResults");
            if (resultsContainer) {
                resultsContainer.innerHTML = `
                    <div style="text-align: center; padding: 40px 20px; color: var(--text-muted);">
                        <div style="font-size: 2.5rem; margin-bottom: 8px;">🎤</div>
                        <p>Type a song or artist above to search our instant local library and YouTube!</p>
                    </div>`;
            }
            return;
        }

        searchDebounceTimer = setTimeout(() => {
            performSearch(query);
        }, 320);
    });

    input.addEventListener("keyup", updateClearBtn);
    input.addEventListener("change", updateClearBtn);
    updateClearBtn();
}

function clearSearchInput() {
    const input = document.getElementById("searchInput");
    const clearBtn = document.getElementById("searchClearBtn");
    if (input) {
        input.value = "";
        input.focus();
    }
    if (clearBtn) {
        clearBtn.style.display = "none";
    }
    clearTimeout(searchDebounceTimer);
    const resultsContainer = document.getElementById("searchResults");
    if (resultsContainer) {
        resultsContainer.innerHTML = `
            <div style="text-align: center; padding: 40px 20px; color: var(--text-muted);">
                <div style="font-size: 2.5rem; margin-bottom: 8px;">🎤</div>
                <p>Type a song or artist above to search our instant local library and YouTube!</p>
            </div>`;
    }
}

let currentSearchQuery = "";
let currentSearchForceOnline = false;
let currentSearchPage = 1;

function performSearch(query, forceOnline = false, page = 1) {
    const resultsContainer = document.getElementById("searchResults");
    if (!resultsContainer) return;

    currentSearchQuery = query;
    currentSearchForceOnline = forceOnline;
    currentSearchPage = page;

    if (page === 1) {
        const searchMsg = isSingAlongMode ? "🍹 Searching Tiki Sing-Along Library & YouTube..." : "🍹 Searching Tiki Library & YouTube...";
        resultsContainer.innerHTML = `<div style="text-align: center; padding: 25px; color: var(--neon-cyan);">${searchMsg}</div>`;
    } else {
        const findMoreBtn = document.getElementById("findMoreBtn");
        if (findMoreBtn) {
            findMoreBtn.disabled = true;
            findMoreBtn.innerText = "🌺 Digging up more songs...";
        }
    }

    fetch(`/api/search?q=${encodeURIComponent(query)}&force_online=${forceOnline}&sing_along=${isSingAlongMode}&page=${page}`)
        .then(async res => {
            if (res.status === 429) {
                const data = await res.json().catch(() => ({}));
                throw new Error(data.detail || "Search rate limit reached. Please wait a few seconds.");
            }
            if (!res.ok) {
                throw new Error("Search request failed.");
            }
            return res.json();
        })
        .then(data => {
            renderSearchResults(data, query, page > 1);
        })
        .catch(err => {
            if (page === 1) {
                resultsContainer.innerHTML = `<div style="text-align: center; color: #ffb703; padding: 20px;">⏳ ${err.message || "Search error. Check connection."}</div>`;
            } else {
                showToast("⚠️ " + (err.message || "Could not load more songs"));
                const findMoreBtn = document.getElementById("findMoreBtn");
                if (findMoreBtn) {
                    findMoreBtn.disabled = false;
                    findMoreBtn.innerText = "🔎 Find More Songs...";
                }
            }
        });
}

function renderSearchResults(data, query, isAppend = false) {
    const container = document.getElementById("searchResults");
    if (!container) return;

    const localList = data.local_results || [];
    const ytList = data.youtube_results || [];
    const hasMore = Boolean(data.has_more);

    if (!isAppend) {
        container.innerHTML = "";

        if (data.is_offline) {
            const offlineBanner = document.createElement("div");
            offlineBanner.style.cssText = "background: rgba(255, 170, 0, 0.12); border: 1px solid rgba(255, 170, 0, 0.4); border-radius: 8px; padding: 10px 14px; margin-bottom: 12px; font-size: 0.82rem; color: #ffaa00; display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap;";
            offlineBanner.innerHTML = `
                <div style="display: flex; align-items: center; gap: 8px;">
                    <span>📶</span> <span><strong>Offline Hotspot Mode:</strong> Searching downloaded library only.</span>
                </div>
                <button type="button" class="btn-ghost" style="border-color: #ffaa00; color: #ffaa00; padding: 4px 10px; font-size: 0.75rem; border-radius: 6px; cursor: pointer;" onclick="performSearch('${escapeHtml(query)}', true, 1)">
                    🌐 Search YouTube Anyway
                </button>
            `;
            container.appendChild(offlineBanner);
        }

        if (localList.length === 0 && ytList.length === 0) {
            const emptyNotice = document.createElement("div");
            emptyNotice.style.cssText = "text-align:center; padding: 30px; color: var(--text-muted);";
            if (data.is_offline) {
                emptyNotice.innerHTML = `
                    <div style="font-size: 1.5rem; margin-bottom: 8px;">🌴 Library Search</div>
                    No downloaded songs matching "<strong>${escapeHtml(query)}</strong>" found.<br>
                    <span style="font-size:0.85rem; color:#888;">If host is connected to the internet, tap below to search YouTube:</span>
                    <div style="margin-top: 14px;">
                        <button type="button" class="btn-neon-pink" style="padding: 10px 20px; font-size: 0.88rem; border-radius: 20px; cursor: pointer;" onclick="performSearch('${escapeHtml(query)}', true, 1)">
                            🌐 Force Online YouTube Search
                        </button>
                    </div>
                `;
            } else {
                emptyNotice.innerHTML = `No songs found for "${escapeHtml(query)}". Try another title or artist! 🌺`;
            }
            container.appendChild(emptyNotice);
            return;
        }

        // 1. Local Tracks (Tier 1)
        if (localList.length > 0) {
            const localSection = document.createElement("div");
            const localHeader = isSingAlongMode ? "⚡ LOCAL SING-ALONG TRACKS (Instant Play)" : "⚡ FROM LOCAL TIKI LIBRARY (Instant Play)";
            localSection.innerHTML = `<div style="font-size: 0.8rem; color: var(--neon-lime); font-weight: 700; margin: 15px 0 8px 4px;">${localHeader}</div>`;
            
            localList.forEach(track => {
                const card = document.createElement("div");
                card.className = "tiki-card";
                card.style.padding = "14px";
                card.style.marginBottom = "10px";
                card.style.display = "flex";
                card.style.justifyContent = "space-between";
                card.style.alignItems = "center";

                const isSingAlongTrack = Boolean(track.is_sing_along || isSingAlongMode);

                card.innerHTML = `
                    <div style="flex: 1; padding-right: 10px;">
                        <div style="font-weight: 700; font-size: 1.05rem;">${escapeHtml(track.title)}</div>
                        <div style="color: var(--text-muted); font-size: 0.85rem;">${escapeHtml(track.artist)}</div>
                        <div style="display: flex; gap: 6px; align-items: center; margin-top: 4px; flex-wrap: wrap;">
                            <span class="badge-local">⚡ Downloaded</span>
                            ${isSingAlongTrack ? '<span class="badge-sing-along">🎙️ Sing-Along</span>' : ''}
                        </div>
                    </div>
                    <button class="btn-neon-pink local-queue-btn" style="padding: 8px 14px; font-size: 0.85rem;">
                        + Queue
                    </button>
                `;
                card.querySelector(".local-queue-btn").onclick = () => openQueueModal(track.id, track.title, track.artist, track.video_id, 0, isSingAlongTrack);
                localSection.appendChild(card);
            });
            container.appendChild(localSection);
        }
    }

    // 2. YouTube Results (Tier 2)
    let ytSection = document.getElementById("ytResultsSection");
    if (!ytSection && ytList.length > 0) {
        ytSection = document.createElement("div");
        ytSection.id = "ytResultsSection";
        const ytHeader = isSingAlongMode ? "🌐 YOUTUBE SING-ALONG (LYRICS + VOCALS)" : "🌐 YOUTUBE KARAOKE RESULTS";
        ytSection.innerHTML = `<div style="font-size: 0.8rem; color: var(--neon-pink); font-weight: 700; margin: 15px 0 8px 4px;">${ytHeader}</div><div id="ytResultsCards"></div>`;
        container.appendChild(ytSection);
    }

    const cardsContainer = document.getElementById("ytResultsCards") || ytSection;
    if (cardsContainer && ytList.length > 0) {
        ytList.forEach(group => {
            const card = document.createElement("div");
            card.className = "tiki-card-pink";
            card.style.padding = "14px";
            card.style.marginBottom = "12px";

            const bestVersion = group.versions[0];
            const isSingAlongYt = Boolean(group.is_sing_along || isSingAlongMode);

            card.innerHTML = `
                <div style="display: flex; justify-content: space-between; align-items: flex-start;">
                    <div style="flex: 1; padding-right: 10px;">
                        <div style="font-weight: 700; font-size: 1.05rem;">${escapeHtml(group.title)}</div>
                        <div style="color: var(--text-muted); font-size: 0.85rem;">${escapeHtml(group.artist)}</div>
                        <div style="font-size: 0.75rem; color: var(--neon-gold); margin-top: 4px; display: flex; align-items: center; gap: 6px; flex-wrap: wrap;">
                            <span>${escapeHtml(bestVersion.channel || "YouTube")} • ${formatDuration(bestVersion.duration)}</span>
                            ${bestVersion.attributes && bestVersion.attributes.length ? `<span>• ${bestVersion.attributes.join(', ')}</span>` : ''}
                            ${isSingAlongYt ? '<span class="badge-sing-along">🎙️ Sing-Along</span>' : ''}
                        </div>
                    </div>
                    <button class="btn-neon-cyan yt-queue-btn" style="padding: 8px 14px; font-size: 0.85rem;">
                        + Queue
                    </button>
                </div>
            `;
            card.querySelector(".yt-queue-btn").onclick = () => openQueueModal(null, group.title, group.artist, bestVersion.video_id, 0, isSingAlongYt);
            cardsContainer.appendChild(card);
        });
    }

    // Clean up old button
    const oldBtn = document.getElementById("findMoreBtn");
    if (oldBtn) oldBtn.remove();

    // Render "Find More Songs" button
    if (hasMore || (!currentSearchForceOnline && localList.length > 0)) {
        const findMoreBtn = document.createElement("button");
        findMoreBtn.id = "findMoreBtn";
        findMoreBtn.className = "btn-ghost";
        findMoreBtn.style.width = "100%";
        findMoreBtn.style.margin = "14px 0 24px 0";
        findMoreBtn.style.padding = "12px";
        findMoreBtn.style.borderColor = "var(--neon-cyan)";
        findMoreBtn.style.color = "var(--neon-cyan)";
        findMoreBtn.style.fontWeight = "700";
        findMoreBtn.style.borderRadius = "10px";
        findMoreBtn.innerText = (!currentSearchForceOnline && localList.length > 0)
            ? (isSingAlongMode ? "🌐 Search YouTube for more sing-alongs..." : "🌐 Search YouTube for more versions...")
            : "🔎 Find More Songs...";
        findMoreBtn.onclick = () => {
            const nextPage = (!currentSearchForceOnline && localList.length > 0) ? 1 : (currentSearchPage + 1);
            performSearch(currentSearchQuery, true, nextPage);
        };
        container.appendChild(findMoreBtn);
    } else if (isAppend && ytList.length === 0) {
        const endBanner = document.createElement("div");
        endBanner.style.textAlign = "center";
        endBanner.style.padding = "16px";
        endBanner.style.color = "var(--text-muted)";
        endBanner.style.fontSize = "0.85rem";
        endBanner.innerText = "🌴 That's all the songs found for this search!";
        container.appendChild(endBanner);
    }

}

// Queue Modal & Pitch Selector
// Queue Modal & Pitch Selector
async function openQueueModal(trackId, title, artist, videoId, initialPitch = 0, isSingAlong = null) {
    const isSA = (isSingAlong !== null && isSingAlong !== undefined) ? Boolean(isSingAlong) : Boolean(isSingAlongMode);
    selectedTrackForQueue = { trackId, title, artist, videoId, isSingAlong: isSA };
    selectedPitch = initialPitch || 0;
    activeDuplicateInfo = null;

    const modalTitleEl = document.getElementById("modalSongTitle");
    if (modalTitleEl) {
        modalTitleEl.innerHTML = `${escapeHtml(artist)} - ${escapeHtml(title)} ${isSA ? ' <span class="badge-sing-along" style="font-size:0.75rem;">🎙️ Sing-Along</span>' : ''}`;
    }
    resetPitchButtons();
    if (selectedPitch !== 0) {
        selectPitch(selectedPitch);
    }
    document.getElementById("duetInput").value = "";
    const dedInput = document.getElementById("dedicationInput");
    if (dedInput) dedInput.value = "";

    const dupSec = document.getElementById("queueModalDuplicateSection");
    if (dupSec) dupSec.style.display = "none";
    const submitBtn = document.getElementById("submitQueueBtn");
    if (submitBtn) submitBtn.innerText = "+ Queue Song";

    document.getElementById("queueModal").style.display = "flex";

    // Asynchronously check if song is already in queue or performed recently
    try {
        const token = currentSinger ? currentSinger.token : "";
        const params = new URLSearchParams({
            singer_token: token,
            title: title || "",
            artist: artist || "",
            video_id: videoId || ""
        });
        if (trackId) params.set("track_id", trackId);
        const res = await fetch(`/api/queue/check-duplicate?${params.toString()}`);
        if (res.ok) {
            const data = await res.json();
            if (data.duplicate && data.duplicate.duplicate_found) {
                showDuplicateAdvisory(data.duplicate);
            }
        }
    } catch (e) {
        console.log("Duplicate pre-check error:", e);
    }
}

function closeQueueModal() {
    document.getElementById("queueModal").style.display = "none";
    activeDuplicateInfo = null;
}

function selectPitch(semitones) {
    selectedPitch = semitones;
    document.querySelectorAll(".pitch-btn").forEach(btn => {
        if (parseInt(btn.dataset.pitch) === semitones) {
            btn.classList.add("active");
            btn.style.borderColor = "var(--neon-pink)";
            btn.style.color = "var(--neon-pink)";
        } else {
            btn.classList.remove("active");
            btn.style.borderColor = "rgba(255,255,255,0.2)";
            btn.style.color = "var(--text-main)";
        }
    });
}

function resetPitchButtons() {
    selectPitch(0);
}

function submitQueue() {
    if (!selectedTrackForQueue) return;
    if (!currentSinger || !currentSinger.token || currentSinger.token === "undefined" || !currentSinger.name || currentSinger.name === "undefined") {
        showToast("⚠️ Please choose your nickname first!");
        showOnboardingModal();
        return;
    }

    const duet = document.getElementById("duetInput").value.trim();
    const dedicationEl = document.getElementById("dedicationInput");
    const dedication = dedicationEl ? dedicationEl.value.trim() : "";
    const payload = {
        track_id: selectedTrackForQueue.trackId,
        video_id: selectedTrackForQueue.videoId,
        title: selectedTrackForQueue.title,
        artist: selectedTrackForQueue.artist,
        singer_token: currentSinger.token,
        duet_partner: duet,
        pitch_shift: selectedPitch,
        dedication: dedication,
        force: Boolean(activeDuplicateInfo),
        is_sing_along: Boolean(selectedTrackForQueue.isSingAlong)
    };

    fetch("/api/queue", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
    })
    .then(async res => {
        const data = await res.json().catch(() => ({}));
        if (res.status === 409 && data.status === "duplicate_warning" && data.duplicate) {
            showDuplicateAdvisory(data.duplicate);
            return null;
        }
        if (!res.ok) {
            throw new Error(data.detail || "Failed to queue song.");
        }
        return data;
    })
    .then(data => {
        if (!data) return; // duplicate notice displayed in modal
        lastQueuedSongTime = Date.now();
        lastQueuedSongId = data.queue_id || null;
        closeQueueModal();
        showToast("🌺 Song Queued! Check Live Queue for your spot.");
        loadLiveQueue();
        checkNotificationPermissionPrompt();
        // Switch to Queue Tab
        document.querySelector('[data-tab="queueTab"]').click();
    })
    .catch((err) => {
        showToast(err.message || "Failed to queue song.");
    });
}

function showDuplicateAdvisory(dup) {
    activeDuplicateInfo = dup;
    const dupSec = document.getElementById("queueModalDuplicateSection");
    if (dupSec) dupSec.style.display = "block";

    const badge = document.getElementById("dupModalBadge");
    const singerAvatar = document.getElementById("dupSingerAvatar");
    const singerName = document.getElementById("dupSingerName");
    const duetTargetName = document.getElementById("dupDuetTargetName");
    const queuePos = document.getElementById("dupQueuePosition");
    const advisoryText = document.getElementById("dupAdvisoryText");
    const joinDuetBtn = document.getElementById("dupJoinDuetBtn");
    const submitBtn = document.getElementById("submitQueueBtn");

    if (singerAvatar) singerAvatar.innerText = dup.existing_singer_avatar || "🌺";
    if (singerName) singerName.innerText = dup.existing_singer_name || "Another Singer";
    if (duetTargetName) duetTargetName.innerText = dup.existing_singer_name || "Friend";

    if (dup.match_type === "playing") {
        if (badge) {
            badge.innerText = "Currently Playing";
            badge.style.color = "var(--neon-lime)";
            badge.style.background = "rgba(57,255,20,0.15)";
        }
        if (queuePos) queuePos.innerText = "Live on Stage";
        if (advisoryText) {
            advisoryText.innerText = dup.is_self 
                ? "You are singing this song on stage right now! Enjoy your performance!" 
                : `${dup.existing_singer_name} is performing this song on stage right now! Head up and cheer them on or sing along!`;
        }
        if (joinDuetBtn) joinDuetBtn.style.display = "none";
        if (submitBtn) submitBtn.innerText = "+ Queue Anyway";
    } else if (dup.match_type === "self") {
        if (badge) {
            badge.innerText = "Already In Your Queue";
            badge.style.color = "var(--neon-gold)";
            badge.style.background = "rgba(255,183,3,0.15)";
        }
        if (queuePos) queuePos.innerText = dup.position_in_line ? `Position #${dup.position_in_line} in line` : "Upcoming in queue";
        if (advisoryText) {
            advisoryText.innerText = `You already have this song in the queue (${dup.position_in_line ? '#' + dup.position_in_line + ' in line' : 'upcoming'})! Are you sure you want to sing the same song multiple times tonight?`;
        }
        if (joinDuetBtn) joinDuetBtn.style.display = "none";
        if (submitBtn) submitBtn.innerText = "+ Queue Again Anyway";
    } else if (dup.match_type === "played_tonight") {
        if (badge) {
            badge.innerText = "Sung Earlier Tonight";
            badge.style.color = "var(--neon-pink)";
            badge.style.background = "rgba(255,42,133,0.15)";
        }
        if (queuePos) queuePos.innerText = "Completed earlier tonight";
        if (advisoryText) {
            advisoryText.innerText = `This song was already sung earlier tonight by ${dup.existing_singer_name}. Feel free to queue an encore!`;
        }
        if (joinDuetBtn) joinDuetBtn.style.display = "none";
        if (submitBtn) submitBtn.innerText = "+ Queue Encore";
    } else {
        // Other singer has it queued
        if (badge) {
            badge.innerText = "Already in Queue";
            badge.style.color = "var(--neon-cyan)";
            badge.style.background = "rgba(0,240,255,0.15)";
        }
        if (queuePos) queuePos.innerText = dup.position_in_line ? `Position #${dup.position_in_line} in line` : "Upcoming in queue";
        if (advisoryText) {
            advisoryText.innerText = `Are you sure? ${dup.existing_singer_name} already has this song in queue (${dup.position_in_line ? '#' + dup.position_in_line + ' in line' : 'upcoming'}). Consider singing along with them as a duet and discuss as appropriate!`;
        }
        if (joinDuetBtn) joinDuetBtn.style.display = "block";
        if (submitBtn) submitBtn.innerText = "+ Queue Anyway";
    }
}

function handleJoinDuet() {
    if (!activeDuplicateInfo || !activeDuplicateInfo.existing_queue_id) return;
    if (!currentSinger || !currentSinger.token || !currentSinger.name || currentSinger.name === "undefined") {
        showToast("⚠️ Please choose your nickname first!");
        showOnboardingModal();
        return;
    }

    fetch(`/api/queue/${activeDuplicateInfo.existing_queue_id}/join-duet`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            singer_token: currentSinger.token,
            partner_name: currentSinger.name
        })
    })
    .then(async res => {
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || "Could not join duet.");
        }
        return res.json();
    })
    .then(data => {
        closeQueueModal();
        showToast(`🎉 You're now singing a duet with ${activeDuplicateInfo.existing_singer_name}!`);
        loadLiveQueue();
        document.querySelector('[data-tab="queueTab"]').click();
    })
    .catch(err => {
        showToast(err.message || "Failed to join duet.");
    });
}

let currentQueueCache = [];
let currentPendingDownloads = [];

// Live Queue View
function loadLiveQueue() {
    Promise.all([
        fetch("/api/queue").then(res => res.json()).catch(() => []),
        fetch("/api/downloads/pending").then(res => res.json()).then(d => d.pending_downloads || []).catch(() => [])
    ]).then(([queue, pending]) => {
        currentQueueCache = queue || [];
        currentPendingDownloads = pending || [];
        renderQueue(currentQueueCache);
    });
}

function renderQueue(queue) {
    currentQueueCache = queue || [];
    const quickstart = document.getElementById("hostQuickstartCard");
    if (quickstart) {
        let isDismissed = false;
        try { isDismissed = !!localStorage.getItem("tiki_hide_quickstart"); } catch (e) {}
        quickstart.style.display = (queue.length === 0 && !isDismissed && isHost) ? "block" : "none";
    }

    const container = document.getElementById("queueListContainer");
    if (!container) return;
    container.innerHTML = "";

    const hasActive = queue && queue.length > 0;
    const hasPending = currentPendingDownloads && currentPendingDownloads.length > 0;

    if (!hasActive && !hasPending) {
        container.innerHTML = `
            <div style="text-align: center; padding: 40px 20px; color: var(--text-muted);">
                <div style="font-size: 3rem; margin-bottom: 10px;">🌴</div>
                <div class="font-tiki neon-cyan" style="font-size: 1.4rem;">The Stage is Open!</div>
                <p style="margin-top: 8px;">Be the first rockstar to request a song tonight!</p>
            </div>
        `;
        return;
    }

    if (!hasActive && hasPending) {
        const waitingHeader = document.createElement("div");
        waitingHeader.style.cssText = "text-align: center; padding: 20px 15px; color: var(--text-muted);";
        waitingHeader.innerHTML = `
            <div style="font-size: 2rem; margin-bottom: 6px;">🌴</div>
            <div class="font-tiki neon-cyan" style="font-size: 1.15rem;">Stage is Ready</div>
            <p style="margin-top: 4px; font-size: 0.85rem;">Upcoming song is preparing in the staging crate below...</p>
        `;
        container.appendChild(waitingHeader);
    }

    queue.forEach((item, index) => {
        const isPlaying = item.status === "playing";
        const isMine = currentSinger && item.singer_id === currentSinger.id;

        const myName = (currentSinger && currentSinger.name) ? currentSinger.name.trim().toLowerCase() : "";
        const primaryName = (item.singer_name || "").trim().toLowerCase();
        const rawPartners = (item.duet_partner || "").trim();
        const partnerNames = rawPartners 
            ? rawPartners.replace(/&/g, ",").split(",").map(p => p.trim()).filter(Boolean)
            : [];
        
        const isPrimary = Boolean(currentSinger && (item.singer_id === currentSinger.id || (myName && primaryName === myName)));
        const isPartner = Boolean(myName && partnerNames.some(p => p.toLowerCase() === myName));
        const amISingerOnSong = isPrimary || isPartner;
        const totalSingersCount = 1 + partnerNames.length;

        // If user is the only remaining singer, do not show button (they have the ✕ button)
        const isOnlySinger = amISingerOnSong && (totalSingersCount === 1);
        const showDuetToggle = !isOnlySinger;
        const isLeaving = amISingerOnSong && totalSingersCount > 1;

        const duetBtnLabel = isLeaving ? "🚪 Leave Song" : "👯 Join Duet";
        const duetBtnClass = isLeaving ? "btn-leave-duet" : "btn-join-duet";
        const duetBtnStyle = isLeaving 
            ? "border-color: rgba(255, 170, 0, 0.45); color: #ffaa00;" 
            : "border-color: var(--neon-cyan); color: var(--neon-cyan);";

        const showCancelBtn = (isMine || isHost) && !isPlaying;

        const card = document.createElement("div");
        card.className = isPlaying ? "tiki-card-pink pulse-glow" : "tiki-card";
        card.style.padding = "16px";
        card.style.marginBottom = "12px";

        const pitchText = item.pitch_shift !== 0 ? `Key: ${item.pitch_shift > 0 ? '+' : ''}${item.pitch_shift}` : "Standard Key";
        const isOnBreak = item.singer_status === "on_break";
        const statusBadge = isOnBreak 
            ? `<span class="badge" style="background: rgba(255,183,3,0.15); color: var(--neon-gold); border: 1px solid var(--neon-gold); font-size: 0.72rem; padding: 2px 6px; border-radius: 4px; ${isHost ? 'cursor: pointer;' : ''}" ${isHost ? `onclick="toggleHostSingerStatus(${item.singer_id}, 'active')"` : ''}>⏸️ On Break ${isHost ? '(Tap to Resume)' : ''}</span>`
            : (isHost && !isPlaying ? `<span class="badge" style="background: rgba(57,255,20,0.1); color: var(--neon-lime); border: 1px solid var(--neon-lime); font-size: 0.72rem; padding: 2px 6px; border-radius: 4px; cursor: pointer;" onclick="toggleHostSingerStatus(${item.singer_id}, 'on_break')">🟢 Active (Pause)</span>` : '');

        const dedicationHtml = item.dedication 
            ? `<div style="font-size: 0.76rem; color: var(--neon-gold); font-style: italic; margin-top: 3px;">💌 "${escapeHtml(item.dedication)}"</div>`
            : '';

        card.innerHTML = `
            <div style="display: flex; justify-content: space-between; align-items: center; gap: 8px;">
                <div style="display: flex; align-items: center; gap: 12px; flex: 1; min-width: 0;">
                    <div style="font-size: 1.8rem; flex-shrink: 0;">${item.singer_avatar || "🌺"}</div>
                    <div style="min-width: 0; overflow: hidden;">
                        <div style="font-size: 0.8rem; font-weight: 700; color: ${isPlaying ? 'var(--neon-pink)' : 'var(--neon-cyan)'}; display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
                            <span>${isPlaying ? 'NOW SINGING' : `#${index + 1} IN LINE`}</span>
                            ${statusBadge}
                        </div>
                        <div style="font-weight: 700; font-size: 1.05rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">
                            ${escapeHtml(item.singer_name)} ${item.duet_partner ? `& ${escapeHtml(item.duet_partner)}` : ''}
                        </div>
                        <div style="color: var(--text-muted); font-size: 0.88rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">
                            ${escapeHtml(item.artist)} - ${escapeHtml(item.title)}
                        </div>
                        ${dedicationHtml}
                        <div style="display: flex; gap: 6px; align-items: center; margin-top: 4px; flex-wrap: wrap;">
                            <span class="badge-key">${pitchText}</span>
                            ${item.is_sing_along ? '<span class="badge-sing-along">🎙️ Sing-Along</span>' : ''}
                        </div>
                    </div>
                </div>

                <div style="display: flex; align-items: center; gap: 6px; flex-shrink: 0; flex-wrap: wrap; justify-content: flex-end;">
                    ${amISingerOnSong && !isPlaying ? `
                        <button onclick="openRehearsalModal(${item.track_id}, '${escapeHtml(item.title)}', '${escapeHtml(item.artist)}', '${item.video_id || ''}', ${item.is_downloaded ? 1 : 0})" 
                                class="btn-ghost" 
                                style="padding: 6px 10px; font-size: 0.76rem; font-weight: 700; color: var(--neon-cyan); border-color: rgba(0,240,255,0.4); border-radius: 8px; white-space: nowrap;" title="Privately rehearse video & lyrics">
                            👁️ Rehearse
                        </button>
                    ` : ''}

                    ${showDuetToggle ? `
                        <button onclick="${isLeaving ? `leaveQueueDuet(${item.queue_id})` : `joinQueueDuet(${item.queue_id})`}" 
                                class="btn-ghost ${duetBtnClass}" 
                                style="padding: 6px 10px; font-size: 0.78rem; font-weight: 700; border-radius: 8px; ${duetBtnStyle}; white-space: nowrap;">
                            ${duetBtnLabel}
                        </button>
                    ` : ''}

                    ${showCancelBtn ? `
                        <button onclick="cancelQueuedSong(${item.queue_id})" class="btn-ghost" style="padding: 6px 10px; font-size: 0.85rem; color: #ff5555; border-radius: 8px;" title="Remove song">
                            ✕
                        </button>
                    ` : ''}
                </div>
            </div>
        `;
        container.appendChild(card);
    });

    // Render Pending / Downloading Staging Section below Active Stage Queue
    if (hasPending) {
        const pendingSection = document.createElement("div");
        pendingSection.style.cssText = "margin-top: 24px; margin-bottom: 12px;";

        const pendingHeader = document.createElement("div");
        pendingHeader.style.cssText = "display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; padding: 0 4px;";
        pendingHeader.innerHTML = `
            <div style="font-size: 0.85rem; font-weight: 800; letter-spacing: 1px; color: var(--neon-cyan); display: flex; align-items: center; gap: 6px;">
                <span>⏳ PREPARING & DOWNLOADING</span>
                <span class="badge" style="background: rgba(0,240,255,0.15); border: 1px solid var(--neon-cyan); font-size: 0.72rem; padding: 1px 6px; border-radius: 10px;">${currentPendingDownloads.length}</span>
            </div>
            <div style="font-size: 0.72rem; color: var(--text-muted);">
                Slots into fair rotation once ready
            </div>
        `;
        pendingSection.appendChild(pendingHeader);

        currentPendingDownloads.forEach((item) => {
            const isMine = currentSinger && item.singer_id === currentSinger.id;
            const canManage = isMine || isHost;
            const progressPct = Math.min(100, Math.max(0, item.progress || 0));
            const retryCount = item.retry_count || 0;
            const isFailed = item.status === "failed";

            const card = document.createElement("div");
            card.className = "tiki-card";
            card.style.cssText = "padding: 14px; margin-bottom: 10px; border-style: dashed; border-color: rgba(0,240,255,0.35); background: rgba(18, 11, 30, 0.65);";

            let statusText = "Downloading...";
            if (isFailed) {
                statusText = item.error_message || "Download stalled";
            } else if (progressPct >= 99) {
                statusText = "Finalizing & Normalizing LUFS...";
            } else {
                statusText = `Downloading ${progressPct}%...`;
            }

            let actionsHtml = "";
            if (canManage) {
                if (retryCount >= 3 || (isFailed && retryCount >= 2)) {
                    // Fail 3+: offer medium, lowest, and find another version
                    actionsHtml = `
                        <div style="display: flex; gap: 6px; margin-top: 10px; flex-wrap: wrap;">
                            <button onclick="retryPendingDownload(${item.id}, 'medium_360p')" class="btn-ghost" style="padding: 4px 8px; font-size: 0.72rem; border-color: var(--neon-gold); color: var(--neon-gold); border-radius: 6px;">
                                🔻 Try Medium Quality
                            </button>
                            <button onclick="retryPendingDownload(${item.id}, 'lowest')" class="btn-ghost" style="padding: 4px 8px; font-size: 0.72rem; border-color: var(--neon-cyan); color: var(--neon-cyan); border-radius: 6px;">
                                🔻 Try Lowest Quality
                            </button>
                            <button onclick="findAnotherVersion(${item.id}, '${escapeHtml(item.title)}', '${escapeHtml(item.artist)}')" class="btn-ghost" style="padding: 4px 8px; font-size: 0.72rem; border-color: var(--neon-pink); color: var(--neon-pink); border-radius: 6px;">
                                🔎 Find Another Version
                            </button>
                            <button onclick="cancelPendingDownload(${item.id})" class="btn-ghost" style="padding: 4px 8px; font-size: 0.72rem; color: #ff5555; border-radius: 6px;" title="Cancel">
                                ✕
                            </button>
                        </div>
                    `;
                } else if (isFailed || retryCount >= 1) {
                    // Fail 2: retry button
                    actionsHtml = `
                        <div style="display: flex; gap: 6px; margin-top: 10px; align-items: center;">
                            <button onclick="retryPendingDownload(${item.id})" class="btn-ghost" style="padding: 4px 10px; font-size: 0.75rem; border-color: var(--neon-gold); color: var(--neon-gold); border-radius: 6px; font-weight: 700;">
                                🔄 Retry Download
                            </button>
                            <button onclick="cancelPendingDownload(${item.id})" class="btn-ghost" style="padding: 4px 8px; font-size: 0.72rem; color: #ff5555; border-radius: 6px;" title="Cancel">
                                ✕ Cancel
                            </button>
                        </div>
                    `;
                } else {
                    actionsHtml = `
                        <div style="display: flex; justify-content: flex-end; margin-top: 6px;">
                            <button onclick="cancelPendingDownload(${item.id})" class="btn-ghost" style="padding: 2px 8px; font-size: 0.75rem; color: #ff5555; border-radius: 6px;" title="Cancel download">
                                ✕ Cancel
                            </button>
                        </div>
                    `;
                }
            }

            card.innerHTML = `
                <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 8px;">
                    <div style="display: flex; align-items: center; gap: 10px; flex: 1; min-width: 0;">
                        <div style="font-size: 1.6rem; flex-shrink: 0;">${item.singer_avatar || "🌺"}</div>
                        <div style="min-width: 0; overflow: hidden; flex: 1;">
                            <div style="font-size: 0.75rem; font-weight: 700; color: ${isFailed ? '#ff5555' : 'var(--neon-cyan)'}; display: flex; align-items: center; gap: 6px;">
                                <span>${isFailed ? '⚠️ STALLED' : 'PREPARING'}</span>
                                <span style="font-size: 0.7rem; color: var(--text-muted);">${statusText}</span>
                            </div>
                            <div style="font-weight: 700; font-size: 0.98rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">
                                ${escapeHtml(item.singer_name)}
                            </div>
                            <div style="color: var(--text-muted); font-size: 0.85rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">
                                ${escapeHtml(item.artist)} - ${escapeHtml(item.title)}
                            </div>
                            <div style="margin-top: 8px; width: 100%; height: 6px; background: rgba(255,255,255,0.08); border-radius: 3px; overflow: hidden;">
                                <div style="width: ${progressPct}%; height: 100%; background: ${isFailed ? '#ff5555' : 'linear-gradient(90deg, var(--neon-cyan), var(--neon-pink))'}; transition: width 0.3s ease;"></div>
                            </div>
                            ${actionsHtml}
                        </div>
                    </div>
                </div>
            `;
            pendingSection.appendChild(card);
        });
        container.appendChild(pendingSection);
    }
}

function retryPendingDownload(pendingId, quality = "default") {
    fetch(`/api/download/retry/${pendingId}?quality=${encodeURIComponent(quality)}`, {
        method: "POST"
    })
    .then(res => res.json())
    .then(data => {
        if (data.status === "ok") {
            showToast("🔄 Retrying download...");
            loadLiveQueue();
        } else {
            showToast("❌ " + (data.detail || "Could not retry download"));
        }
    })
    .catch(() => showToast("Network error retrying download"));
}

function cancelPendingDownload(pendingId) {
    fetch(`/api/download/${pendingId}`, {
        method: "DELETE"
    })
    .then(res => res.json())
    .then(data => {
        if (data.status === "ok") {
            showToast("Removed from downloads");
            loadLiveQueue();
        } else {
            showToast("❌ " + (data.detail || "Could not cancel download"));
        }
    })
    .catch(() => showToast("Network error cancelling download"));
}

function findAnotherVersion(pendingId, title, artist) {
    cancelPendingDownload(pendingId);
    if (typeof switchTab === "function") {
        switchTab("search");
    }
    const searchInput = document.getElementById("searchInput");
    if (searchInput) {
        searchInput.value = `${artist} ${title}`.trim();
        if (typeof performSearch === "function") {
            performSearch();
        }
    }
}

// "You're On Deck (~30s)" Phone Alert Engine (Haptic, Push Notification, Non-blocking Top Banner)
function handleSingerOnDeckMessage(data) {
    if (isHost) return; // Never disturb the Host DJ
    if (!currentSinger || !currentSinger.name) return;

    // Suppress if singer queued this song within the last 15s (they already know)
    if (lastQueuedSongTime && (Date.now() - lastQueuedSongTime < 15000) && lastQueuedSongId === data.queue_id) {
        return;
    }

    const myName = currentSinger.name.trim().toLowerCase();
    const primaryName = (data.singer_name || "").trim().toLowerCase();
    const rawPartners = (data.duet_partner || "").trim();
    const partnerNames = rawPartners 
        ? rawPartners.replace(/&/g, ",").split(",").map(p => p.trim().toLowerCase()).filter(Boolean)
        : [];
    const isPrimary = (data.singer_id === currentSinger.id) || (myName && primaryName === myName);
    const isPartner = Boolean(myName && partnerNames.includes(myName));

    if (isPrimary || isPartner) {
        triggerSingerOnDeckAlert(data);
    }
}

function triggerSingerOnDeckAlert(track) {
    // 1. Subtle Haptic Vibration (Android Chrome / Firefox / Samsung Internet)
    if ("vibrate" in navigator) {
        try {
            navigator.vibrate([250, 150, 250]);
        } catch (e) {
            console.warn("[Vibrate] Haptic error:", e);
        }
    }

    // 2. Web Notification (if permission granted and user switched tabs/screen)
    if ("Notification" in window && Notification.permission === "granted") {
        try {
            new Notification("🎤 You're On Deck (~30s)!", {
                body: `Get ready to sing "${track.title}"! Head toward the stage microphone.`,
                icon: "/static/images/tiki-icon.png",
                vibrate: [250, 150, 250]
            });
        } catch (e) {
            console.warn("[Notification] Push error:", e);
        }
    }

    // 3. Sleek Top Slide-Down Banner (Non-Blocking, Auto-Dismisses after 8s)
    const banner = document.getElementById("singerOnDeckBanner");
    const titleEl = document.getElementById("onDeckSongTitle");
    if (titleEl) titleEl.innerText = track.title || "Your Song";
    if (banner) {
        banner.style.display = "flex";
        setTimeout(() => {
            banner.style.transform = "translateY(0)";
            banner.style.opacity = "1";
        }, 10);
        if (window._onDeckBannerTimer) clearTimeout(window._onDeckBannerTimer);
        window._onDeckBannerTimer = setTimeout(dismissSingerOnDeckBanner, 8000);
    }
}

function dismissSingerOnDeckBanner() {
    const banner = document.getElementById("singerOnDeckBanner");
    if (banner) {
        banner.style.transform = "translateY(-30px)";
        banner.style.opacity = "0";
        setTimeout(() => { banner.style.display = "none"; }, 300);
    }
}

function checkNotificationPermissionPrompt() {
    if (!("Notification" in window)) return;
    if (Notification.permission === "default" && !localStorage.getItem("tiki_dismissed_notif_banner")) {
        const banner = document.getElementById("notificationPermissionBanner");
        if (banner) banner.style.display = "flex";
    }
}

function requestNotificationPermission() {
    if (!("Notification" in window)) {
        showToast("Notifications not supported in this browser.");
        dismissNotificationBanner();
        return;
    }
    Notification.requestPermission().then(perm => {
        dismissNotificationBanner();
        if (perm === "granted") {
            showToast("🔔 Alerts enabled! We'll buzz you when you're up.");
        }
    }).catch(() => {
        dismissNotificationBanner();
    });
}

function dismissNotificationBanner() {
    const banner = document.getElementById("notificationPermissionBanner");
    if (banner) banner.style.display = "none";
    localStorage.setItem("tiki_dismissed_notif_banner", "true");
}

function joinQueueDuet(queueId) {
    if (!currentSinger || !currentSinger.token || !currentSinger.name || currentSinger.name === "undefined") {
        showToast("⚠️ Please choose your nickname first!");
        showOnboardingModal();
        return;
    }

    fetch(`/api/queue/${queueId}/join-duet`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            singer_token: currentSinger.token,
            partner_name: currentSinger.name
        })
    })
    .then(async res => {
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || "Could not join song.");
        }
        return res.json();
    })
    .then(() => {
        showToast("👯 You joined this song as a duet partner!");
        loadLiveQueue();
    })
    .catch(err => {
        showToast(err.message || "Failed to join song.");
    });
}

function leaveQueueDuet(queueId) {
    if (!currentSinger || !currentSinger.token) {
        showToast("⚠️ Nickname required!");
        showOnboardingModal();
        return;
    }

    if (!confirm("Remove yourself as a singer on this song?")) return;

    fetch(`/api/queue/${queueId}/leave-duet`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            singer_token: currentSinger.token,
            singer_name: currentSinger.name
        })
    })
    .then(async res => {
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || "Could not remove yourself from song.");
        }
        return res.json();
    })
    .then(() => {
        showToast("👋 You removed yourself from this song.");
        loadLiveQueue();
    })
    .catch(err => {
        showToast(err.message || "Failed to leave song.");
    });
}

function cancelQueuedSong(queueId) {
    if (!confirm("Remove this song from queue?")) return;
    fetch(`/api/queue/${queueId}?token=${currentSinger?.token || ''}`, { method: "DELETE" })
        .then(() => loadLiveQueue());
}

// Crowd Emoji Reactions
function sendReaction(emoji) {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "emoji_reaction", emoji: emoji }));
    }
    // Mobile tactile feedback
    showToast(`Sent ${emoji} to stage screen!`);
}

// Personal Songbook & Queue Frequency Tracking
let currentSongbookCache = [];
let currentSongbookSort = "frequency";

function setSongbookSort(sortType) {
    currentSongbookSort = sortType;
    document.querySelectorAll("[data-songbook-sort]").forEach(btn => {
        if (btn.getAttribute("data-songbook-sort") === sortType) {
            btn.classList.add("active");
        } else {
            btn.classList.remove("active");
        }
    });
    renderSongbookList();
}

function loadSongbook() {
    if (!currentSinger || !currentSinger.token) return;
    const list = document.getElementById("songbookList");
    if (!list) return;

    fetch(`/api/singer/${currentSinger.token}/songbook`)
        .then(res => res.json())
        .then(data => {
            currentSongbookCache = data.songs || [];
            const subtext = document.getElementById("songbookSubtext");
            if (subtext) {
                if (data.total_queued > 0) {
                    const singerName = data.singer?.name || currentSinger.name || "My";
                    subtext.innerText = `${singerName}'s Songbook • ${data.unique_songs} song${data.unique_songs === 1 ? '' : 's'} (${data.total_queued} total time${data.total_queued === 1 ? '' : 's'} queued)`;
                } else {
                    subtext.innerText = "Ranked by your most queued tracks across all visits";
                }
            }
            renderSongbookList();
        })
        .catch(err => {
            console.error("[Songbook] Error loading personal songbook:", err);
            list.innerHTML = '<div style="color: var(--text-muted); text-align: center; padding: 20px;">Unable to load personal songbook. Tap refresh to retry!</div>';
        });
}

function renderSongbookList() {
    const list = document.getElementById("songbookList");
    if (!list) return;
    list.innerHTML = "";

    if (!currentSongbookCache || currentSongbookCache.length === 0) {
        list.innerHTML = `
            <div style="text-align: center; padding: 30px 16px; color: var(--text-muted);">
                <div style="font-size: 2.2rem; margin-bottom: 8px;">🌴</div>
                <div style="font-size: 1.05rem; font-weight: 700; color: #fff; margin-bottom: 6px;">Your Personal Songbook is Empty!</div>
                <p style="font-size: 0.85rem; max-width: 280px; margin: 0 auto 16px auto;">Search for a song or spin the Tiki Wheel to queue your first karaoke performance!</p>
                <button onclick="document.querySelector('[data-tab=\\'searchTab\\']').click()" class="btn-neon-cyan" style="font-size: 0.85rem; padding: 8px 18px;">
                    🔍 Browse & Search Songs
                </button>
            </div>
        `;
        return;
    }

    // Sort according to currentSongbookSort
    const sorted = [...currentSongbookCache];
    if (currentSongbookSort === "frequency") {
        sorted.sort((a, b) => (b.times_queued || 0) - (a.times_queued || 0));
    } else if (currentSongbookSort === "recent") {
        sorted.sort((a, b) => new Date(b.last_queued_at || 0) - new Date(a.last_queued_at || 0));
    } else if (currentSongbookSort === "alpha") {
        sorted.sort((a, b) => (a.title || "").localeCompare(b.title || ""));
    }

    sorted.forEach((item, index) => {
        const card = document.createElement("div");
        card.className = "tiki-card";
        card.style.padding = "14px 16px";
        card.style.marginBottom = "10px";
        card.style.display = "flex";
        card.style.justifyContent = "space-between";
        card.style.alignItems = "center";
        card.style.gap = "12px";

        // Frequency rank badge
        let badgeHtml = "";
        const count = item.times_queued || 1;
        if (currentSongbookSort === "frequency" && index === 0 && count > 1) {
            badgeHtml = `<span style="background: rgba(255, 183, 3, 0.2); border: 1px solid var(--neon-gold); color: var(--neon-gold); padding: 2px 8px; border-radius: 8px; font-size: 0.72rem; font-weight: 700;">👑 #1 Pick • ${count}x Queued</span>`;
        } else if (count > 1) {
            badgeHtml = `<span style="background: rgba(255, 42, 133, 0.15); border: 1px solid var(--neon-pink); color: var(--neon-pink); padding: 2px 8px; border-radius: 8px; font-size: 0.72rem; font-weight: 700;">🔥 ${count}x Queued</span>`;
        } else {
            badgeHtml = `<span style="background: rgba(0, 240, 255, 0.12); border: 1px solid rgba(0, 240, 255, 0.3); color: var(--neon-cyan); padding: 2px 8px; border-radius: 8px; font-size: 0.72rem; font-weight: 600;">⭐ 1x Queued</span>`;
        }

        let pitchHtml = "";
        if (item.preferred_pitch && item.preferred_pitch !== 0) {
            const sign = item.preferred_pitch > 0 ? "+" : "";
            pitchHtml = `<span class="badge-key" style="font-size: 0.7rem; padding: 2px 6px;">Key: ${sign}${item.preferred_pitch}</span>`;
        }

        let hostActionsHtml = "";
        if (isHost) {
            let saveBtnHtml = "";
            if (!item.is_downloaded) {
                saveBtnHtml = `<button class="btn-ghost host-save-lib-btn" style="padding: 6px 10px; font-size: 0.75rem; font-weight: 600; color: var(--neon-lime); border-color: rgba(0,255,100,0.4); white-space: nowrap;">⬇️ Save</button>`;
            }
            const bgmBtnHtml = `<button class="btn-ghost host-bgm-import-btn" style="padding: 6px 10px; font-size: 0.75rem; font-weight: 600; color: var(--neon-gold); border-color: rgba(255,183,3,0.4); white-space: nowrap;">🎬 BGM</button>`;
            hostActionsHtml = `${saveBtnHtml}${bgmBtnHtml}`;
        }

        card.innerHTML = `
            <div style="flex: 1; min-width: 0;">
                <div style="display: flex; align-items: center; gap: 6px; margin-bottom: 4px; flex-wrap: wrap;">
                    ${badgeHtml}
                    ${pitchHtml}
                </div>
                <div style="font-weight: 700; font-size: 0.95rem; color: #fff; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${escapeHtml(item.title)}</div>
                <div style="color: var(--text-muted); font-size: 0.82rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${escapeHtml(item.artist)}</div>
            </div>
            <div style="display: flex; gap: 6px; align-items: center;">
                ${hostActionsHtml}
                <button class="btn-ghost history-requeue-btn" style="padding: 7px 14px; font-size: 0.82rem; font-weight: 700; color: var(--neon-cyan); border-color: rgba(0,240,255,0.4); white-space: nowrap;">
                    Sing Again
                </button>
            </div>
        `;

        card.querySelector(".history-requeue-btn").onclick = () => {
            openQueueModal(item.track_id, item.title, item.artist, item.video_id, item.preferred_pitch || 0);
        };

        const saveBtn = card.querySelector(".host-save-lib-btn");
        if (saveBtn) {
            saveBtn.onclick = (e) => {
                e.stopPropagation();
                saveBtn.disabled = true;
                saveBtn.innerText = "⏳ Saving...";
                fetch(`/api/host/tracks/${item.track_id}/download`, { method: "POST" })
                    .then(res => res.json())
                    .then(d => {
                        saveBtn.innerText = "✅ Saved";
                        showCustomToast(d.message || "Downloading track to library!");
                    })
                    .catch(() => {
                        saveBtn.innerText = "❌ Error";
                    });
            };
        }

        const bgmBtn = card.querySelector(".host-bgm-import-btn");
        if (bgmBtn) {
            bgmBtn.onclick = (e) => {
                e.stopPropagation();
                bgmBtn.disabled = true;
                bgmBtn.innerText = "🔍 Finding MV...";
                fetch(`/api/host/filler/import-from-song/${item.track_id}`, { method: "POST" })
                    .then(res => res.json())
                    .then(d => {
                        if (d.status === "ok") {
                            bgmBtn.innerText = "✅ Added to BGM";
                            showCustomToast(d.message || "Added music video to BGM!");
                        } else {
                            bgmBtn.innerText = "⚠️ In BGM";
                            showCustomToast(d.message || "Already in BGM crate");
                        }
                    })
                    .catch(() => {
                        bgmBtn.innerText = "❌ No Video";
                        showCustomToast("Could not find official music video");
                    });
            };
        }

        list.appendChild(card);
    });
}

function installStarterCrate(e) {
    const btn = document.getElementById("installStarterBtn") || (e && (e.currentTarget || e.target));
    const oldText = btn ? btn.innerText : "";
    if (btn) {
        btn.disabled = true;
        btn.innerText = "🌴 Installing 15 Classics...";
    }
    showCustomToast("🌴 Queueing starter music videos...");
    fetch("/api/host/filler/install-starter", { method: "POST" })
        .then(async res => {
            if (!res.ok) {
                const errData = await res.json().catch(() => ({}));
                throw new Error(errData.detail || `Server returned error (${res.status})`);
            }
            return res.json();
        })
        .then(data => {
            const count = data.total_queued || (data.added_count + (data.requeued_count || 0)) || 15;
            showCustomToast(`🌴 Queued ${count} starter music videos to BGM!`);
            loadFillerTracks();
        })
        .catch(err => {
            showCustomToast("⚠️ Error installing starter crate: " + err.message);
        })
        .finally(() => {
            if (btn) {
                btn.disabled = false;
                btn.innerText = oldText || "🌴 Install Starter Crate (15 Classics)";
            }
        });
}

function loadHistory() {
    loadSongbook();
    loadSingerRecordings();
}

function spinTikiWheel() {
    fetch("/api/surprise")
        .then(res => res.json())
        .then(track => {
            if (track) {
                openQueueModal(track.id, track.title, track.artist, track.video_id);
            } else {
                alert("No tracks in local library yet to spin! Search for a song to build the party catalog!");
            }
        });
}

// Host DJ Controls
function hostSkip() {
    fetch("/api/host/skip", { method: "POST" })
        .then(res => res.json())
        .then(data => {
            if (data && data.skipped_to_filler) {
                showToast(`⏭️ Skipped to filler: ${data.skipped_to_filler}`);
            } else if (data && data.skipped_to_bgm) {
                showToast("⏭️ Skipped to next background track");
            }
        })
        .catch(err => console.error("Skip error:", err));
}

function hostPauseResume() {
    fetch("/api/host/toggle-pause", { method: "POST" });
}

function hostStop() {
    fetch("/api/host/stop", { method: "POST" });
}

function hostStartNow() {
    fetch("/api/host/start-now", { method: "POST" });
}

function hostAdd15s() {
    fetch("/api/host/add-time", { method: "POST" });
}

function hostRefreshStage(hard = false) {
    const url = hard ? "/api/host/stage/refresh?hard=true" : "/api/host/stage/refresh";
    fetch(url, { method: "POST" })
        .then(res => res.json())
        .then(data => {
            showToast(hard ? "🔄 Reloading all stage display screens..." : "🔄 Stage displays refreshed and resynced!");
            loadLiveQueue();
        })
        .catch(err => {
            alert("Error refreshing stage: " + err.message);
        });
}

function hostPlaySound(sound) {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "soundboard_play", sound: sound }));
    }
}

function hostToggleTTS(enabled) {
    fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tts_enabled: enabled ? "true" : "false" })
    });
}

function hostDuckBGM(duck) {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "volume_change", volume: duck ? 0.05 : 1.0 }));
    }
}

// -------------------------------------------------------------
// Mobile Host Remote Microphone Controls
// -------------------------------------------------------------
let hostMicState = {
    device: "default",
    muted: true,
    volume: 80,
    reverb: true,
    ducking_enabled: true,
    ducking_db: -6,
    available: null,   // null = unknown yet; true/false from backend router
    latency_ms: null,  // negotiated buffering latency estimate (native path)
    monitoring_mode: "native"  // hardware_direct | native | browser
};

async function loadHostMicDevices() {
    const sel = document.getElementById("mobileHostMicDeviceSelect");
    if (!sel) return;

    try {
        const res = await fetch("/api/setup/audio-inputs");
        if (!res.ok) return;
        const devices = await res.json();
        sel.innerHTML = "";
        devices.forEach(d => {
            const opt = document.createElement("option");
            opt.value = d.id;
            opt.text = d.description || d.name;
            if (d.id === hostMicState.device) opt.selected = true;
            sel.appendChild(opt);
        });

        // Also fetch live state from backend audio router
        const stateRes = await fetch("/api/host/mic-state");
        if (stateRes.ok) {
            const st = await stateRes.json();
            if (st) {
                hostMicState.muted = Boolean(st.muted);
                hostMicState.volume = Number(st.volume) || 80;
                if (st.device && st.device !== "default") hostMicState.device = st.device;
                if (st.available !== undefined) hostMicState.available = Boolean(st.available);
                if (st.monitoring_mode) hostMicState.monitoring_mode = st.monitoring_mode;
                if (st.latency_ms_est !== undefined && st.latency_ms_est !== null) {
                    hostMicState.latency_ms = Number(st.latency_ms_est);
                }
                renderMobileMonitoringModeGating();
                updateMobileHostMicUI();
                const slider = document.getElementById("mobileHostMicSlider");
                if (slider) slider.value = hostMicState.volume;
                const volText = document.getElementById("mobileHostMicVolText");
                if (volText) volText.innerText = `${hostMicState.volume}%`;
                if (hostMicState.device) sel.value = hostMicState.device;
            }
        }
    } catch (e) {
        console.warn("[Host Remote] Error loading audio inputs:", e);
    }
}

function hostSetMonitoringMode(mode) {
    if (!mode) return;
    hostMicState.monitoring_mode = mode;
    sendHostMicSettings({ monitoring_mode: mode });
    renderMobileMonitoringModeGating();
    updateMobileHostMicUI();
}

function renderMobileMonitoringModeGating() {
    const isHwDirect = hostMicState.monitoring_mode === "hardware_direct";
    const btn = document.getElementById("mobileHostMicToggleBtn");
    const slider = document.getElementById("mobileHostMicSlider");
    const hint = document.getElementById("mobileMicModeHint");
    const modeSel = document.getElementById("mobileMonitoringModeSelect");

    if (modeSel && hostMicState.monitoring_mode && modeSel.value !== hostMicState.monitoring_mode) {
        modeSel.value = hostMicState.monitoring_mode;
    }
    if (btn) {
        btn.disabled = isHwDirect;
        btn.title = isHwDirect ? "Hardware Direct mode: use interface knob" : "";
        btn.style.opacity = isHwDirect ? "0.45" : "1";
    }
    if (slider) {
        slider.disabled = isHwDirect;
        slider.style.opacity = isHwDirect ? "0.45" : "1";
    }
    if (hint) {
        hint.style.display = isHwDirect ? "block" : "none";
    }
    renderMobileMicAvailability();
}

function hostSetMicDevice(dev) {
    hostMicState.device = dev;
    sendHostMicSettings({ device: dev });
}

function hostToggleMic(muted) {
    if (muted === undefined) muted = !hostMicState.muted;
    hostMicState.muted = muted;
    sendHostMicSettings({ muted: hostMicState.muted });
    updateMobileHostMicUI();
}

function hostSetMicVolume(vol) {
    hostMicState.volume = Number(vol) || 80;
    sendHostMicSettings({ volume: hostMicState.volume });
    const label = document.getElementById("mobileHostMicVolText");
    if (label) label.innerText = `${hostMicState.volume}%`;
}

function hostToggleMicReverb(enabled) {
    hostMicState.reverb = !!enabled;
    sendHostMicSettings({ reverb: hostMicState.reverb });
}

function hostToggleDucking(enabled) {
    hostMicState.ducking_enabled = !!enabled;
    sendHostMicSettings({ ducking_enabled: hostMicState.ducking_enabled });
}

function hostSetDuckingDb(db) {
    hostMicState.ducking_db = Number(db) || -6;
    sendHostMicSettings({ ducking_db: hostMicState.ducking_db });
}

function sendHostMicSettings(partial) {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({
            type: "host_mic_settings",
            monitoring_mode: hostMicState.monitoring_mode,
            ...partial
        }));
    }
}

function updateMobileHostMicUI() {
    const btn = document.getElementById("mobileHostMicToggleBtn");
    const badge = document.getElementById("mobileHostMicBadge");
    if (btn) {
        if (hostMicState.muted) {
            btn.innerHTML = "🔇 Stage Mic Muted (OFF)";
            btn.style.color = "#ff5555";
            btn.style.borderColor = "rgba(255,85,85,0.4)";
        } else {
            btn.innerHTML = "🎤 Stage Mic LIVE (ON)";
            btn.style.color = "#39ff14";
            btn.style.borderColor = "rgba(57,255,20,0.4)";
        }
    }
    if (badge) {
        if (hostMicState.monitoring_mode === "hardware_direct") {
            badge.innerText = "DIRECT HARDWARE";
            badge.style.background = "rgba(57, 255, 20, 0.2)";
            badge.style.color = "#39ff14";
            badge.style.borderColor = "rgba(57, 255, 20, 0.4)";
        } else if (hostMicState.monitoring_mode === "browser") {
            if (hostMicState.muted) {
                badge.innerText = "MUTED";
                badge.style.background = "rgba(255, 85, 85, 0.2)";
                badge.style.color = "#ff5555";
                badge.style.borderColor = "rgba(255, 85, 85, 0.4)";
            } else {
                badge.innerText = "LIVE (Browser WebRTC)";
                badge.style.background = "rgba(0, 240, 255, 0.2)";
                badge.style.color = "#00f0ff";
                badge.style.borderColor = "rgba(0, 240, 255, 0.4)";
            }
        } else if (hostMicState.available === false) {
            badge.innerText = "NO NATIVE ROUTING";
            badge.style.background = "rgba(255, 183, 3, 0.2)";
            badge.style.color = "#ffb703";
            badge.style.borderColor = "rgba(255, 183, 3, 0.4)";
        } else if (hostMicState.muted) {
            badge.innerText = "MUTED";
            badge.style.background = "rgba(255, 85, 85, 0.2)";
            badge.style.color = "#ff5555";
            badge.style.borderColor = "rgba(255, 85, 85, 0.4)";
        } else {
            badge.innerText = (hostMicState.latency_ms !== null && hostMicState.latency_ms > 0)
                ? `LIVE (~${Math.round(hostMicState.latency_ms)}ms)`
                : "LIVE";
            badge.style.background = "rgba(57, 255, 20, 0.2)";
            badge.style.color = "#39ff14";
            badge.style.borderColor = "rgba(57, 255, 20, 0.4)";
        }
    }
}

function renderMobileMicAvailability() {
    const warn = document.getElementById("mobileHostMicNativeWarning");
    if (!warn) return;
    if (hostMicState.available === false && hostMicState.monitoring_mode === "native") {
        const hint = document.getElementById("mobileHostMicNativeWarningHint");
        if (hint) {
            const isWin = /Windows/i.test(navigator.userAgent);
            hint.innerText = isWin
                ? "The tiki-mic-router helper was not found in the app's bin folder — reinstall, or copy tiki-mic-router.exe into bin\\ next to TikiKaraoke.exe."
                : "Install PipeWire or PulseAudio (pactl) on the host system to enable low-latency mic monitoring.";
        }
        warn.style.display = "block";
    } else {
        warn.style.display = "none";
    }
}

function syncHostMicSettingsUI(data) {
    if (data.available !== undefined) {
        hostMicState.available = Boolean(data.available);
        renderMobileMicAvailability();
    }
    if (data.latency_ms !== undefined && data.latency_ms !== null) {
        hostMicState.latency_ms = Number(data.latency_ms);
    }
    if (data.monitoring_mode !== undefined && data.monitoring_mode) {
        hostMicState.monitoring_mode = data.monitoring_mode;
        renderMobileMonitoringModeGating();
        updateMobileHostMicUI();
    }
    if (data.device !== undefined && data.device) {
        hostMicState.device = data.device;
        const sel = document.getElementById("mobileHostMicDeviceSelect");
        if (sel) {
            let opt = Array.from(sel.options).find(o => o.value === data.device);
            if (!opt) {
                opt = document.createElement("option");
                opt.value = data.device;
                opt.text = data.device;
                sel.appendChild(opt);
            }
            sel.value = data.device;
        }
    }
    if (data.muted !== undefined) {
        hostMicState.muted = !!data.muted;
        updateMobileHostMicUI();
    }
    if (data.volume !== undefined) {
        hostMicState.volume = Number(data.volume);
        const slider = document.getElementById("mobileHostMicSlider");
        if (slider) slider.value = hostMicState.volume;
        const volText = document.getElementById("mobileHostMicVolText");
        if (volText) volText.innerText = `${hostMicState.volume}%`;
    }
    if (data.reverb !== undefined) {
        hostMicState.reverb = !!data.reverb;
        const rev = document.getElementById("mobileMicReverbToggle");
        if (rev) rev.checked = hostMicState.reverb;
    }
    if (data.ducking_enabled !== undefined) {
        hostMicState.ducking_enabled = !!data.ducking_enabled;
        const duck = document.getElementById("mobileDuckingToggle");
        if (duck) duck.checked = hostMicState.ducking_enabled;
    }
    if (data.ducking_db !== undefined) {
        hostMicState.ducking_db = Number(data.ducking_db);
        const sel = document.getElementById("mobileDuckingDbSelect");
        if (sel) sel.value = String(hostMicState.ducking_db);
    }
}

function loadHostSettings() {
    loadHostMicDevices();
    fetch("/api/settings")
        .then(res => res.json())
        .then(settings => {
            const ttsCheck = document.getElementById("ttsToggle");
            if (ttsCheck) ttsCheck.checked = (settings.tts_enabled === "true");
            const speedSelect = document.getElementById("hostMarqueeSpeedSelect");
            if (speedSelect && settings.marquee_scroll_speed) {
                speedSelect.value = settings.marquee_scroll_speed;
            }
            if (settings.intermission_vibe_mode) {
                updateVibeModeUI(settings.intermission_vibe_mode);
            }
        });
}

// Venue A/V Sync (Audio Latency Offset)
function loadVenueAvSync() {
    const badge = document.getElementById("avSyncBadge");
    if (!badge) return;

    fetch("/api/host/av-sync")
        .then(res => res.json())
        .then(data => {
            currentAvSyncOffset = data.offset_ms || 0;
            updateAvSyncUI();
        })
        .catch(err => {
            console.error("[AVSync] Failed to load sync offset:", err);
        });
}

function updateAvSyncUI() {
    const badge = document.getElementById("avSyncBadge");
    const desc = document.getElementById("avSyncDesc");
    if (!badge) return;

    const sign = currentAvSyncOffset > 0 ? "+" : "";
    badge.innerText = `${sign}${currentAvSyncOffset} ms`;

    if (currentAvSyncOffset === 0) {
        badge.style.color = "var(--neon-gold)";
        badge.style.background = "rgba(255, 183, 3, 0.18)";
        if (desc) desc.innerText = "Direct PA / zero lag. Adjust if TV picture trails behind the speakers.";
    } else if (currentAvSyncOffset > 0) {
        badge.style.color = "var(--neon-cyan)";
        badge.style.background = "rgba(0, 240, 255, 0.18)";
        if (desc) desc.innerText = `Audio delayed by ${currentAvSyncOffset}ms to match HDMI TV / projector video lag.`;
    } else {
        badge.style.color = "#ffaa00";
        badge.style.background = "rgba(255, 170, 0, 0.18)";
        if (desc) desc.innerText = `Audio advanced by ${Math.abs(currentAvSyncOffset)}ms for Bluetooth / wireless PA delay.`;
    }

    // Highlight matching preset button
    document.querySelectorAll(".av-preset-btn").forEach(btn => {
        const val = parseInt(btn.dataset.offset, 10);
        if (val === currentAvSyncOffset) {
            btn.style.borderColor = "var(--neon-cyan)";
            btn.style.color = "var(--neon-cyan)";
            btn.style.background = "rgba(0, 240, 255, 0.15)";
        } else {
            btn.style.borderColor = "rgba(255,255,255,0.15)";
            btn.style.color = "var(--text-main)";
            btn.style.background = "transparent";
        }
    });
}

function setVenueAvSyncPreset(offsetMs) {
    currentAvSyncOffset = parseInt(offsetMs, 10) || 0;
    updateAvSyncUI();

    fetch("/api/host/av-sync", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ offset_ms: currentAvSyncOffset })
    })
    .then(res => res.json())
    .then(data => {
        const sign = data.offset_ms > 0 ? "+" : "";
        showToast(`🎛️ Venue A/V Sync: ${sign}${data.offset_ms} ms`);
    })
    .catch(err => {
        console.error("[AVSync] Failed to set sync offset:", err);
        showToast("⚠️ Failed to update A/V sync");
    });
}

function nudgeVenueAvSync(deltaMs) {
    const newOffset = Math.max(-300, Math.min(500, currentAvSyncOffset + deltaMs));
    setVenueAvSyncPreset(newOffset);
}

// Helpers
function showToast(msg) {
    const toast = document.createElement("div");
    toast.style.position = "fixed";
    toast.style.bottom = "80px";
    toast.style.left = "50%";
    toast.style.transform = "translateX(-50%)";
    toast.style.background = "rgba(16, 28, 45, 0.95)";
    toast.style.border = "1px solid var(--neon-cyan)";
    toast.style.color = "#fff";
    toast.style.padding = "10px 18px";
    toast.style.borderRadius = "20px";
    toast.style.fontSize = "0.9rem";
    toast.style.zIndex = "1000";
    toast.style.boxShadow = "0 0 15px rgba(0, 240, 255, 0.5)";
    toast.innerText = msg;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 2500);
}

function formatDuration(sec) {
    if (!sec) return "";
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return `${m}:${s < 10 ? '0' : ''}${s}`;
}

function escapeHtml(str) {
    if (!str) return "";
    return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}

function setCookie(name, val, days = 365) {
    const d = new Date();
    d.setTime(d.getTime() + (days * 24 * 60 * 60 * 1000));
    document.cookie = `${name}=${encodeURIComponent(val)};expires=${d.toUTCString()};path=/;SameSite=Lax`;
}

function getCookie(name) {
    const match = document.cookie.match(new RegExp('(^| )' + name + '=([^;]+)'));
    return match ? decodeURIComponent(match[2]) : null;
}

function toggleHostInstructionsModal(show) {
    const modal = document.getElementById("hostInstructionsModal");
    if (modal) {
        modal.style.display = show ? "flex" : "none";
    }
}

// Host Singer Roster & Mass Queue Removal
function loadHostSingerRoster() {
    const container = document.getElementById("hostSingerRosterContainer");
    if (!container) return;

    container.innerHTML = `<div style="text-align: center; color: var(--text-muted); font-size: 0.8rem; padding: 10px;">Loading singer roster...</div>`;

    fetch("/api/host/singers")
        .then(res => {
            if (!res.ok) throw new Error("Unauthorized or server error");
            return res.json();
        })
        .then(singers => {
            container.innerHTML = "";
            if (!singers || singers.length === 0) {
                container.innerHTML = `<div style="text-align: center; color: var(--text-muted); font-size: 0.8rem; padding: 10px;">No singers registered yet.</div>`;
                return;
            }

            singers.forEach(s => {
                const pendingSongs = s.songs ? s.songs.filter(song => song.status === 'pending') : [];
                const playingSongs = s.songs ? s.songs.filter(song => song.status === 'playing') : [];
                const totalActive = pendingSongs.length + playingSongs.length;

                const card = document.createElement("div");
                card.style.background = "rgba(8, 14, 24, 0.75)";
                card.style.border = totalActive > 0 ? "1px solid rgba(0, 240, 255, 0.35)" : "1px solid rgba(255, 255, 255, 0.08)";
                card.style.borderRadius = "12px";
                card.style.padding = "12px";

                let songsHtml = "";
                if (s.songs && s.songs.length > 0) {
                    songsHtml = `<div style="margin-top: 8px; border-top: 1px solid rgba(255,255,255,0.08); padding-top: 6px;">`;
                    s.songs.forEach(song => {
                        const statusBadge = song.status === 'playing' 
                            ? `<span style="background: rgba(255,42,133,0.25); color: var(--neon-pink); font-size: 0.7rem; font-weight: 700; padding: 2px 6px; border-radius: 4px;">PLAYING</span>`
                            : `<span style="background: rgba(0,240,255,0.15); color: var(--neon-cyan); font-size: 0.7rem; font-weight: 600; padding: 2px 6px; border-radius: 4px;">Pending</span>`;
                        songsHtml += `
                            <div style="display: flex; justify-content: space-between; align-items: center; padding: 4px 0; font-size: 0.8rem;">
                                <div style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 75%;">
                                    🎵 <strong>${escapeHtml(song.title)}</strong> <span style="color: var(--text-muted); font-size: 0.75rem;">- ${escapeHtml(song.artist)}</span>
                                </div>
                                <div>${statusBadge}</div>
                            </div>
                        `;
                    });
                    songsHtml += `</div>`;
                } else {
                    songsHtml = `<div style="margin-top: 6px; font-size: 0.75rem; color: var(--text-muted); font-style: italic;">No songs currently in queue</div>`;
                }

                let removeBtnHtml = "";
                if (pendingSongs.length > 0) {
                    removeBtnHtml = `
                        <button onclick="openHostSingerManageModal(${s.singer_id}, '${escapeHtml(s.singer_name)}', '${s.singer_avatar || '🌺'}', ${pendingSongs.length})" 
                                class="btn-ghost" 
                                style="margin-top: 10px; width: 100%; padding: 8px; font-size: 0.8rem; font-weight: 600; color: #ff5555; border-color: rgba(255,85,85,0.35); background: rgba(255,85,85,0.08); border-radius: 8px; cursor: pointer;">
                            🗑️ Manage / Remove ${pendingSongs.length} Queued Song${pendingSongs.length > 1 ? 's' : ''}
                        </button>
                    `;
                } else {
                    removeBtnHtml = `
                        <button onclick="openHostSingerManageModal(${s.singer_id}, '${escapeHtml(s.singer_name)}', '${s.singer_avatar || '🌺'}', 0)" 
                                class="btn-ghost" 
                                style="margin-top: 10px; width: 100%; padding: 6px; font-size: 0.75rem; font-weight: 600; color: var(--text-muted); border-color: rgba(255,255,255,0.15); border-radius: 8px; cursor: pointer;">
                            👋 Check Out Singer (Left Party)
                        </button>
                    `;
                }

                const isBreak = s.singer_status === "on_break";
                const statusBadge = isBreak
                    ? `<span style="background: rgba(255,183,3,0.15); color: var(--neon-gold); border: 1px solid var(--neon-gold); font-size: 0.68rem; font-weight: 700; padding: 2px 6px; border-radius: 4px;">⏸️ BREAK</span>`
                    : `<span style="background: rgba(57,255,20,0.15); color: var(--neon-lime); border: 1px solid var(--neon-lime); font-size: 0.68rem; font-weight: 700; padding: 2px 6px; border-radius: 4px;">🟢 ACTIVE</span>`;
                
                const toggleStatusBtn = isBreak
                    ? `<button onclick="toggleHostSingerStatus(${s.singer_id}, 'active')" class="btn-ghost" style="padding: 3px 8px; font-size: 0.72rem; border-color: var(--neon-lime); color: var(--neon-lime); border-radius: 6px; cursor: pointer;">▶️ Resume</button>`
                    : `<button onclick="toggleHostSingerStatus(${s.singer_id}, 'on_break')" class="btn-ghost" style="padding: 3px 8px; font-size: 0.72rem; border-color: var(--neon-gold); color: var(--neon-gold); border-radius: 6px; cursor: pointer;">⏸️ Break</button>`;

                card.innerHTML = `
                    <div style="display: flex; justify-content: space-between; align-items: center; gap: 8px;">
                        <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
                            <span style="font-size: 1.3rem;">${s.singer_avatar || '🌺'}</span>
                            <span style="font-weight: 700; font-size: 0.95rem; color: #fff;">${escapeHtml(s.singer_name)}</span>
                            ${statusBadge}
                        </div>
                        <div style="display: flex; align-items: center; gap: 8px;">
                            ${toggleStatusBtn}
                            <span style="font-size: 0.75rem; color: ${totalActive > 0 ? 'var(--neon-gold)' : 'var(--text-muted)'}; font-weight: 600;">
                                ${totalActive} song${totalActive === 1 ? '' : 's'}
                            </span>
                        </div>
                    </div>
                    ${songsHtml}
                    ${removeBtnHtml}
                `;
                container.appendChild(card);
            });
        })
        .catch(err => {
            container.innerHTML = `<div style="text-align: center; color: #ff5555; font-size: 0.8rem; padding: 10px;">Failed to load roster: ${err.message}</div>`;
        });
}

async function toggleHostSingerStatus(singerId, newStatus) {
    try {
        const res = await fetch(`/api/host/singer/${singerId}/status`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-Host-Token": typeof HOST_TOKEN !== "undefined" ? HOST_TOKEN : ""
            },
            body: JSON.stringify({ status: newStatus })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Failed to update singer status");
        showToast(newStatus === "on_break" ? "⏸️ Singer placed on break (queue held)" : "🟢 Singer active in rotation!");
        loadHostSingerRoster();
        loadLiveQueue();
    } catch (e) {
        showToast(`⚠️ ${e.message}`);
    }
}

let activeManageSinger = null;

function openHostSingerManageModal(singerId, singerName, singerAvatar, pendingCount) {
    activeManageSinger = { singerId, singerName, singerAvatar, pendingCount };
    const modal = document.getElementById("hostSingerManageModal");
    if (!modal) return;

    const avEl = document.getElementById("manageSingerAvatar");
    const nameEl = document.getElementById("manageSingerName");
    const nameTextEl = document.getElementById("manageSingerNameText");
    const countEl = document.getElementById("manageSingerQueueCount");
    const clearBtn = document.getElementById("manageClearQueueOnlyBtn");

    if (avEl) avEl.innerText = singerAvatar || "🌺";
    if (nameEl) nameEl.innerText = singerName;
    if (nameTextEl) nameTextEl.innerText = singerName;
    if (countEl) {
        countEl.innerText = pendingCount > 0 ? `${pendingCount} song${pendingCount === 1 ? '' : 's'} in queue` : "No songs in queue";
    }
    if (clearBtn) {
        clearBtn.style.display = pendingCount > 0 ? "flex" : "none";
    }

    modal.style.display = "flex";
}

function closeHostSingerManageModal() {
    activeManageSinger = null;
    const modal = document.getElementById("hostSingerManageModal");
    if (modal) modal.style.display = "none";
}

function confirmHostSingerAction(checkout) {
    if (!activeManageSinger) return;
    const { singerId, singerName, pendingCount } = activeManageSinger;
    closeHostSingerManageModal();

    fetch(`/api/host/singer/${singerId}/remove-all`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ checkout: !!checkout })
    })
    .then(res => res.json())
    .then(data => {
        if (checkout) {
            showToast(`👋 ${singerName} checked out! Name and icon are now free.`);
        } else {
            showToast(`Cleared ${data.deleted_count || pendingCount} song(s) for ${singerName}. Profile remains active.`);
        }
        loadHostSingerRoster();
        loadLiveQueue();
    })
    .catch(err => {
        alert("Error updating singer: " + err.message);
    });
}

function singerLeaveParty() {
    if (!confirm("Are you leaving the party? This will clear any queued songs and free your nickname and avatar for other guests.")) {
        return;
    }
    const token = currentSinger ? currentSinger.token : (localStorage.getItem("tiki_singer_token") || "");
    fetch("/api/singer/leave", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: token })
    })
    .then(res => res.json())
    .then(() => {
        showToast("You have checked out of the party. Have a great night! 🌺");
        localStorage.removeItem("tiki_singer_token");
        localStorage.removeItem("tiki_singer_name");
        localStorage.removeItem("tiki_singer_avatar");
        currentSinger = null;
        closeOnboardingModal();
        const badge = document.getElementById("headerSingerBadge");
        if (badge) badge.innerHTML = "🌺 Welcome";
        loadLiveQueue();
    })
    .catch(err => {
        alert("Error checking out: " + err.message);
    });
}

function hostPurgeAllSingers() {
    if (!confirm("Are you sure you want to purge ALL registered singers and clear the current queue? This cannot be undone.")) {
        return;
    }
    fetch("/api/host/singers/purge-all", { method: "POST" })
        .then(res => {
            if (!res.ok) throw new Error("Unauthorized or server error");
            return res.json();
        })
        .then(data => {
            showToast(`Party reset! Purged ${data.singers_deleted || 0} singers.`);
            loadHostSingerRoster();
            loadLiveQueue();
        })
        .catch(err => {
            alert("Error purging singers: " + err.message);
        });
}

function loadHostDisplays() {
    const container = document.getElementById("hostDisplaysContainer");
    if (!container) return;

    fetch("/api/host/displays")
        .then(res => {
            if (!res.ok) throw new Error("Unauthorized or server error");
            return res.json();
        })
        .then(displays => {
            container.innerHTML = "";
            if (!displays || displays.length === 0) {
                container.innerHTML = `
                    <div style="text-align: center; color: var(--text-muted); font-size: 0.8rem; padding: 12px; background: rgba(8, 14, 24, 0.5); border-radius: 8px; border: 1px dashed rgba(255,255,255,0.1);">
                        No stage displays currently connected.<br>
                        <span style="font-size: 0.72rem; color: var(--neon-cyan);">Open <code>/stage</code> on your TV or laptop to link a display!</span>
                    </div>
                `;
                return;
            }

            displays.forEach(disp => {
                const card = document.createElement("div");
                card.style.background = "rgba(8, 14, 24, 0.75)";
                card.style.border = !disp.is_muted ? "1px solid rgba(0, 240, 255, 0.45)" : "1px solid rgba(255, 255, 255, 0.08)";
                card.style.borderRadius = "12px";
                card.style.padding = "10px 12px";

                const audioBadge = !disp.is_muted
                    ? `<span style="background: rgba(0,240,255,0.18); color: var(--neon-cyan); border: 1px solid rgba(0,240,255,0.4); font-size: 0.72rem; font-weight: 700; padding: 3px 8px; border-radius: 6px;">🔊 Master Audio</span>`
                    : `<span style="background: rgba(255,85,85,0.12); color: #ff5555; border: 1px solid rgba(255,85,85,0.3); font-size: 0.72rem; font-weight: 600; padding: 3px 8px; border-radius: 6px;">🔇 Muted</span>`;

                const masterBtn = disp.is_muted ? `
                    <button onclick="hostSetDisplayAudio('${escapeHtml(disp.display_id)}', 'set_master')" 
                            class="btn-neon-cyan" 
                            style="padding: 6px 10px; font-size: 0.78rem; font-weight: 700; border-radius: 6px; cursor: pointer;">
                        ⭐ Set Master Audio
                    </button>
                ` : `
                    <span style="font-size: 0.75rem; color: var(--neon-gold); font-weight: 600; display: flex; align-items: center; gap: 4px;">
                        👑 Primary Output
                    </span>
                `;

                const toggleMuteBtn = disp.is_muted ? `
                    <button onclick="hostSetDisplayAudio('${escapeHtml(disp.display_id)}', 'unmute')" 
                            class="btn-ghost" 
                            style="padding: 6px 10px; font-size: 0.78rem; border-radius: 6px; cursor: pointer;">
                        🔊 Unmute
                    </button>
                ` : `
                    <button onclick="hostSetDisplayAudio('${escapeHtml(disp.display_id)}', 'mute')" 
                            class="btn-ghost" 
                            style="padding: 6px 10px; font-size: 0.78rem; color: #ff5555; border-color: rgba(255,85,85,0.3); border-radius: 6px; cursor: pointer;">
                        🔇 Mute
                    </button>
                `;

                card.innerHTML = `
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                        <div style="display: flex; align-items: center; gap: 8px; overflow: hidden;">
                            <span style="font-size: 1.2rem;">🖥️</span>
                            <div style="overflow: hidden;">
                                <div style="font-weight: 700; font-size: 0.88rem; color: #fff; white-space: nowrap; text-overflow: ellipsis; overflow: hidden;">
                                    ${escapeHtml(disp.name || "Stage Display")}
                                </div>
                                <div style="font-size: 0.68rem; color: var(--text-muted);">
                                    ${disp.ip ? `IP: ${escapeHtml(disp.ip)} • ` : ''}ID: ${escapeHtml(disp.display_id)}
                                </div>
                            </div>
                        </div>
                        <div>${audioBadge}</div>
                    </div>
                    <div style="display: flex; gap: 8px; justify-content: flex-end; align-items: center; border-top: 1px solid rgba(255,255,255,0.06); padding-top: 8px;">
                        ${masterBtn}
                        ${toggleMuteBtn}
                    </div>
                `;
                container.appendChild(card);
            });
        })
        .catch(err => {
            container.innerHTML = `<div style="text-align: center; color: #ff5555; font-size: 0.8rem; padding: 10px;">Failed to load displays: ${err.message}</div>`;
        });
}

function hostSetDisplayAudio(displayId, action) {
    fetch(`/api/host/display/${encodeURIComponent(displayId)}/audio`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: action })
    })
    .then(res => {
        if (!res.ok) throw new Error("Unauthorized or server error");
        return res.json();
    })
    .then(() => {
        if (action === "set_master") {
            showToast("Set display as sole Master Audio output! 🔊");
        } else if (action === "mute") {
            showToast("Muted display. 🔇");
        } else {
            showToast("Unmuted display. 🔊");
        }
        loadHostDisplays();
    })
    .catch(err => {
        alert("Error updating display audio: " + err.message);
    });
}

// ==========================================
// 🌴 Local Library Browsing & Smart Sorting
// ==========================================

let currentLibPage = 1;
let currentLibSort = "popularity";
let currentLibArtist = "";
let currentLibSearch = "";
let libSearchDebounceTimer = null;
let cachedLibraryArtists = [];

function formatRelativeTime(timestamp) {
    if (!timestamp) return "Never played";
    const date = new Date(timestamp.replace(" ", "T") + "Z");
    if (isNaN(date.getTime())) return "Recently";
    const diffSec = Math.floor((new Date() - date) / 1000);
    if (diffSec < 60) return "Just now";
    if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
    if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
    const days = Math.floor(diffSec / 86400);
    if (days === 1) return "Yesterday";
    if (days < 30) return `${days}d ago`;
    const months = Math.floor(days / 30);
    return `${months}mo ago`;
}

function setLibrarySort(sort) {
    currentLibSort = sort;
    currentLibPage = 1;
    document.querySelectorAll(".lib-sort-btn").forEach(btn => {
        if (btn.dataset.sort === sort) {
            btn.classList.add("active");
        } else {
            btn.classList.remove("active");
        }
    });
    loadLibraryTracks();
}

function onLibrarySearchInput() {
    const input = document.getElementById("libSearchInput");
    const clearBtn = document.getElementById("libSearchClearBtn");
    if (clearBtn && input) {
        clearBtn.style.display = input.value.length > 0 ? "flex" : "none";
    }
    clearTimeout(libSearchDebounceTimer);
    libSearchDebounceTimer = setTimeout(() => {
        currentLibSearch = input ? input.value.trim() : "";
        currentLibPage = 1;
        loadLibraryTracks();
    }, 280);
}

function clearLibrarySearchInput() {
    const input = document.getElementById("libSearchInput");
    const clearBtn = document.getElementById("libSearchClearBtn");
    if (input) {
        input.value = "";
        input.focus();
    }
    if (clearBtn) {
        clearBtn.style.display = "none";
    }
    clearTimeout(libSearchDebounceTimer);
    currentLibSearch = "";
    currentLibPage = 1;
    loadLibraryTracks();
}

function onLibraryArtistChange() {
    const sel = document.getElementById("libArtistFilter");
    currentLibArtist = sel ? sel.value : "";
    currentLibPage = 1;
    loadLibraryTracks();
}

function changeLibraryPage(delta) {
    currentLibPage += delta;
    if (currentLibPage < 1) currentLibPage = 1;
    loadLibraryTracks();
}

function loadLibraryTracks() {
    const container = document.getElementById("libraryListContainer");
    if (!container) return;

    const url = `/api/library?page=${currentLibPage}&limit=25&sort=${encodeURIComponent(currentLibSort)}&artist=${encodeURIComponent(currentLibArtist)}&q=${encodeURIComponent(currentLibSearch)}&sing_along=${isSingAlongMode}`;

    fetch(url)
        .then(res => res.json())
        .then(data => {
            const tracks = Array.isArray(data.tracks) ? data.tracks : [];
            const total = data.total || 0;
            const totalPages = data.total_pages || 1;
            currentLibPage = data.page || 1;

            // Update badge
            const badge = document.getElementById("libraryTotalBadge");
            if (badge) badge.innerText = `${total} songs`;

            // Populate artist dropdown if changed
            if (Array.isArray(data.artists) && JSON.stringify(data.artists) !== JSON.stringify(cachedLibraryArtists)) {
                cachedLibraryArtists = data.artists;
                const sel = document.getElementById("libArtistFilter");
                if (sel) {
                    const currentVal = sel.value;
                    sel.innerHTML = '<option value="">All Artists</option>';
                    data.artists.forEach(a => {
                        const opt = document.createElement("option");
                        opt.value = a;
                        opt.innerText = a;
                        if (a === currentVal) opt.selected = true;
                        sel.appendChild(opt);
                    });
                }
            }

            container.innerHTML = "";

            if (tracks.length === 0) {
                const isFiltered = !!(currentLibSearch || currentLibArtist);
                container.innerHTML = `
                    <div class="tiki-card-cyan" style="text-align: center; padding: 32px 20px; margin-top: 10px; border-radius: 16px;">
                        <div style="font-size: 2.8rem; margin-bottom: 10px;">🌴🌺🍹</div>
                        <h3 class="font-tiki neon-cyan" style="font-size: 1.25rem; margin-bottom: 6px;">
                            ${isFiltered ? "No Matching Songs Found" : "Your Songbook is Ready!"}
                        </h3>
                        <p style="font-size: 0.85rem; color: #cbd5e1; max-width: 440px; margin: 0 auto 16px; line-height: 1.45;">
                            ${isFiltered 
                                ? "Try clearing your artist filter or searching with a different keyword." 
                                : "Tap below to search millions of karaoke tracks online, or request songs directly to get the party started!"}
                        </p>
                        <button type="button" onclick="switchTab('searchTab'); setTimeout(() => { const inp = document.getElementById('searchInput'); if(inp) inp.focus(); }, 150);" class="btn-neon-cyan" style="padding: 12px 28px; font-weight: 700; border-radius: 24px; font-size: 0.92rem; cursor: pointer;">
                            🔍 Search Songs Online
                        </button>
                    </div>
                `;
                const pag = document.getElementById("libraryPaginationBar");
                if (pag) pag.style.display = "none";
                return;
            }

            tracks.forEach(track => {
                const card = document.createElement("div");
                card.className = "tiki-card";
                card.style.padding = "14px";
                card.style.marginBottom = "10px";
                card.style.display = "flex";
                card.style.justifyContent = "space-between";
                card.style.alignItems = "center";
                card.style.gap = "10px";

                const plays = track.play_count || 0;
                let playBadgeText = plays === 1 ? "🎤 1 play" : `🎤 ${plays} plays`;
                let playBadgeColor = "rgba(0, 240, 255, 0.15)";
                let playTextColor = "var(--neon-cyan)";

                if (plays >= 5) {
                    playBadgeText = `🔥 ${plays} plays`;
                    playBadgeColor = "rgba(255, 42, 133, 0.2)";
                    playTextColor = "var(--neon-pink)";
                } else if (plays === 0) {
                    playBadgeText = "🆕 Unplayed";
                    playBadgeColor = "rgba(255, 255, 255, 0.08)";
                    playTextColor = "var(--text-muted)";
                }

                const lastPlayedText = formatRelativeTime(track.last_played);

                card.innerHTML = `
                    <div style="flex: 1; min-width: 0;">
                        <div style="font-weight: 700; font-size: 1.05rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">
                            ${escapeHtml(track.title)}
                        </div>
                        <div style="color: var(--text-muted); font-size: 0.85rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-top: 2px;">
                            ${escapeHtml(track.artist)}
                        </div>
                        <div style="display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: 6px; font-size: 0.75rem;">
                            <span style="background: ${playBadgeColor}; color: ${playTextColor}; padding: 2px 7px; border-radius: 6px; font-weight: 700;">
                                ${playBadgeText}
                            </span>
                            ${track.is_sing_along ? '<span class="badge-sing-along">🎙️ Sing-Along</span>' : ''}
                            <span style="color: var(--neon-gold);">
                                🕒 ${lastPlayedText}
                            </span>
                            <span style="color: var(--text-muted);">
                                • ${formatDuration(track.duration)}
                            </span>
                        </div>
                    </div>
                    <button class="btn-neon-pink lib-queue-btn" style="padding: 9px 14px; font-size: 0.85rem; white-space: nowrap; flex-shrink: 0;">
                        + Queue
                    </button>
                `;
                card.querySelector(".lib-queue-btn").onclick = () => openQueueModal(track.id, track.title, track.artist, track.video_id, 0, Boolean(track.is_sing_along || isSingAlongMode));
                container.appendChild(card);
            });

            // Pagination Controls
            const pag = document.getElementById("libraryPaginationBar");
            const prevBtn = document.getElementById("libPrevBtn");
            const nextBtn = document.getElementById("libNextBtn");
            const pageInfo = document.getElementById("libPageInfo");

            if (pag) {
                if (totalPages > 1) {
                    pag.style.display = "flex";
                    if (pageInfo) pageInfo.innerText = `Page ${currentLibPage} of ${totalPages} (${total} total)`;
                    if (prevBtn) prevBtn.disabled = currentLibPage <= 1;
                    if (nextBtn) nextBtn.disabled = currentLibPage >= totalPages;
                } else {
                    pag.style.display = "none";
                }
            }
        })
        .catch(err => {
            console.error("[Library] Error loading tracks:", err);
            container.innerHTML = `<div style="text-align: center; color: #ff5555; padding: 20px;">Failed to load library: ${err.message}</div>`;
        });
}

// ==========================================
// 💾 Host Storage Monitoring & Pruning
// ==========================================

let currentCleanupSort = "oldest_played";
let selectedCleanupTrackIds = new Set();
let cleanupCandidatesList = [];

function loadHostStorageStatus() {
    const banner = document.getElementById("hostStorageBanner");
    const badge = document.getElementById("storageBadge");
    const pBar = document.getElementById("storageProgressBar");
    const details = document.getElementById("storageDetails");
    if (!banner || !badge) return;

    fetch("/api/storage/status")
        .then(res => res.json())
        .then(data => {
            const usedPct = data.used_percent || 0;
            const freeGb = data.free_gb || 0;
            const totalGb = data.total_gb || 0;
            const libGb = data.library_gb || 0;
            const tracks = data.downloaded_track_count || 0;

            // Thresholds: green default, orange at >= 75%, red at >= 90%
            let colorHex = "var(--neon-lime)";
            let badgeBg = "rgba(0, 255, 100, 0.15)";
            let badgeText = `${usedPct}% Full`;

            if (usedPct >= 90.0) {
                colorHex = "#ff3366";
                badgeBg = "rgba(255, 51, 102, 0.25)";
                badgeText = `🚨 ${usedPct}% Full (Critical)`;
            } else if (usedPct >= 75.0) {
                colorHex = "#ff9f1c";
                badgeBg = "rgba(255, 159, 28, 0.22)";
                badgeText = `⚠️ ${usedPct}% Full`;
            }

            badge.innerText = badgeText;
            badge.style.color = colorHex;
            badge.style.background = badgeBg;

            if (pBar) {
                pBar.style.width = `${Math.min(100, usedPct)}%`;
                pBar.style.background = colorHex;
            }

            banner.style.borderLeftColor = colorHex;

            if (details) {
                details.innerText = `${freeGb} GB Free of ${totalGb} GB (${libGb} GB Karaoke Library • ${tracks} songs)`;
            }
            loadHostRecordingsStorageStatus();
        })
        .catch(err => {
            console.error("[Storage] Failed to load status:", err);
        });
}

function loadHostRecordingsStorageStatus() {
    const card = document.getElementById("hostRecordingsStorageCard");
    const badge = document.getElementById("recordingsStorageBadge");
    const pathText = document.getElementById("hostRecordingsPathText");
    const stats = document.getElementById("hostRecordingsStats");
    if (!card || !pathText) return;

    fetch("/api/host/storage/recordings")
        .then(res => res.json())
        .then(data => {
            if (data && data.status === "ok") {
                pathText.innerText = data.recordings_dir || "Default";
                if (badge) {
                    badge.innerText = data.is_custom ? "Custom Folder" : "Default Folder";
                    badge.style.color = data.is_custom ? "var(--neon-cyan)" : "var(--neon-pink)";
                    badge.style.background = data.is_custom ? "rgba(0, 240, 255, 0.15)" : "rgba(255, 42, 133, 0.15)";
                }
                if (stats) {
                    const mb = data.mb || 0;
                    const count = data.count || 0;
                    const singers = data.singers_count || 0;
                    stats.innerText = `${count} recordings (${mb} MB) across ${singers} singers`;
                }
            }
        })
        .catch(err => {
            console.error("[Recordings] Failed to load recordings storage:", err);
        });
}

function openRecordingsFolder() {
    fetch("/api/host/storage/recordings/open-folder", { method: "POST" })
        .then(res => res.json())
        .then(data => {
            if (data && data.status === "ok") {
                showToast("📂 Opened recordings folder on host PC!");
            } else {
                showToast("⚠️ Could not open folder: " + (data.detail || "Error"));
            }
        })
        .catch(err => {
            showToast("⚠️ Failed to open folder on host");
        });
}

function checkYtdlpStatus() {
    const badge = document.getElementById("ytdlpBadge");
    const text = document.getElementById("ytdlpStatusText");
    const btn = document.getElementById("updateYtdlpBtn");
    if (!badge || !text) return;

    fetch("/api/host/system/ytdlp-status")
        .then(res => res.json())
        .then(data => {
            if (data.update_available) {
                badge.innerText = `Update Available (v${data.latest_version})`;
                badge.style.color = "var(--neon-gold)";
                badge.style.background = "rgba(255, 183, 3, 0.18)";
                text.innerText = `Installed: v${data.current_version} • Latest: v${data.latest_version}`;
                if (btn) {
                    btn.innerText = "⚡ Update Now";
                    btn.style.color = "var(--neon-gold)";
                    btn.style.borderColor = "var(--neon-gold)";
                }
            } else {
                badge.innerText = `v${data.current_version} (Up to date)`;
                badge.style.color = "var(--neon-lime)";
                badge.style.background = "rgba(0, 255, 100, 0.15)";
                text.innerText = `yt-dlp v${data.current_version} is current and operational.`;
                if (btn) {
                    btn.innerText = "🔄 Re-check / Update";
                    btn.style.color = "var(--neon-cyan)";
                    btn.style.borderColor = "rgba(0, 240, 255, 0.4)";
                }
            }
        })
        .catch(err => {
            console.error("[System] Failed to check yt-dlp status:", err);
            badge.innerText = "Status Unknown";
            badge.style.color = "var(--text-muted)";
        });
}

function checkHostHealth() {
    const ffmpegBanner = document.getElementById("hostFfmpegBanner");
    fetch("/api/host/health")
        .then(res => res.json())
        .then(data => {
            if (ffmpegBanner) {
                ffmpegBanner.style.display = data.ffmpeg_installed ? "none" : "block";
            }
        })
        .catch(err => {
            console.error("[Host Health] Health check error:", err);
        });
}

function dismissQuickstartCard() {
    const card = document.getElementById("hostQuickstartCard");
    if (card) card.style.display = "none";
    try {
        localStorage.setItem("tiki_hide_quickstart", "true");
    } catch (e) {}
}

function loadHostNetworkStatus() {
    const urlEl = document.getElementById("networkCurrentUrl");
    const badgeEl = document.getElementById("networkSslBadge");
    const modeEl = document.getElementById("networkModeLabel");
    if (!urlEl || !badgeEl) return;

    fetch("/api/state")
        .then(res => res.json())
        .then(data => {
            const net = data.network;
            if (net) {
                urlEl.innerText = net.base_url || window.location.origin;
                if (net.is_https) {
                    badgeEl.innerText = "🔒 Secure SSL";
                    badgeEl.style.color = "var(--neon-lime)";
                    badgeEl.style.background = "rgba(0, 255, 100, 0.15)";
                } else {
                    badgeEl.innerText = "⚠️ Unencrypted HTTP";
                    badgeEl.style.color = "var(--neon-gold)";
                    badgeEl.style.background = "rgba(255, 183, 3, 0.15)";
                }
                const modeNames = {
                    "cloudflare_quick": "Cloudflare Quick Tunnel",
                    "cloudflare_named": "Cloudflare Named Tunnel",
                    "tailscale": "Tailscale Funnel",
                    "duckdns": "DuckDNS + Caddy",
                    "bore": "Bore Static Port",
                    "local": "Local Wi-Fi Only"
                };
                if (modeEl) {
                    modeEl.innerText = `Mode: ${modeNames[net.remote_mode] || net.remote_mode}`;
                }
            }
        })
        .catch(err => {
            console.error("[Network] Failed to load network status:", err);
        });
}

async function updateYtdlpEngine() {
    const btn = document.getElementById("updateYtdlpBtn");
    const badge = document.getElementById("ytdlpBadge");
    const text = document.getElementById("ytdlpStatusText");

    if (btn) {
        btn.disabled = true;
        btn.innerText = "⏳ Updating...";
    }
    if (badge) {
        badge.innerText = "Installing...";
        badge.style.color = "var(--neon-cyan)";
    }
    if (text) {
        text.innerText = "Upgrading yt-dlp package from PyPI, please wait...";
    }
    showToast("⚙️ Upgrading yt-dlp Video Engine in background...");

    try {
        const res = await fetch("/api/host/system/update-ytdlp", { method: "POST" });
        const data = await res.json();
        if (res.ok && data.status === "success") {
            showToast(`🎉 yt-dlp updated: v${data.old_version} ➔ v${data.new_version}`);
            checkYtdlpStatus();
        } else {
            showToast(`⚠️ Update error: ${data.detail || data.output || "Failed"}`);
            checkYtdlpStatus();
        }
    } catch (err) {
        console.error("[System] yt-dlp update request failed:", err);
        showToast("❌ Network error while updating yt-dlp engine.");
        checkYtdlpStatus();
    } finally {
        if (btn) btn.disabled = false;
    }
}

function openCleanupModal() {
    const modal = document.getElementById("cleanupModal");
    if (modal) modal.style.display = "flex";
    selectedCleanupTrackIds.clear();
    loadCleanupCandidates();
}

function closeCleanupModal() {
    const modal = document.getElementById("cleanupModal");
    if (modal) modal.style.display = "none";
}

function setCleanupSort(sort) {
    currentCleanupSort = sort;
    const btnOldest = document.getElementById("cleanSortOldest");
    const btnLeast = document.getElementById("cleanSortLeast");

    if (sort === "oldest_played") {
        if (btnOldest) {
            btnOldest.style.borderColor = "var(--neon-cyan)";
            btnOldest.style.color = "var(--neon-cyan)";
        }
        if (btnLeast) {
            btnLeast.style.borderColor = "rgba(255,255,255,0.2)";
            btnLeast.style.color = "#fff";
        }
    } else {
        if (btnLeast) {
            btnLeast.style.borderColor = "var(--neon-cyan)";
            btnLeast.style.color = "var(--neon-cyan)";
        }
        if (btnOldest) {
            btnOldest.style.borderColor = "rgba(255,255,255,0.2)";
            btnOldest.style.color = "#fff";
        }
    }
    loadCleanupCandidates();
}

function loadCleanupCandidates() {
    const list = document.getElementById("cleanupCandidateList");
    if (!list) return;
    list.innerHTML = `<div style="text-align: center; color: var(--text-muted); padding: 20px;">Scanning library files...</div>`;

    fetch(`/api/storage/cleanup-candidates?sort_by=${encodeURIComponent(currentCleanupSort)}&limit=60`)
        .then(res => res.json())
        .then(data => {
            cleanupCandidatesList = Array.isArray(data.candidates) ? data.candidates : [];
            list.innerHTML = "";

            if (cleanupCandidatesList.length === 0) {
                list.innerHTML = `<div style="text-align: center; color: var(--text-muted); padding: 20px;">No downloaded video files found to prune.</div>`;
                updateCleanupStats();
                return;
            }

            cleanupCandidatesList.forEach(item => {
                const row = document.createElement("label");
                row.style.display = "flex";
                row.style.alignItems = "center";
                row.style.gap = "10px";
                row.style.padding = "10px 12px";
                row.style.background = "rgba(8, 14, 24, 0.8)";
                row.style.border = "1px solid rgba(255,255,255,0.1)";
                row.style.borderRadius = "10px";
                row.style.cursor = "pointer";

                const isChecked = selectedCleanupTrackIds.has(item.id);

                row.innerHTML = `
                    <input type="checkbox" value="${item.id}" ${isChecked ? 'checked' : ''} onchange="toggleCleanupTrack(${item.id}, this.checked)" style="transform: scale(1.3); cursor: pointer;">
                    <div style="flex: 1; min-width: 0;">
                        <div style="font-weight: 700; font-size: 0.9rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: #fff;">
                            ${escapeHtml(item.title)}
                        </div>
                        <div style="color: var(--text-muted); font-size: 0.78rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">
                            ${escapeHtml(item.artist)}
                        </div>
                    </div>
                    <div style="text-align: right; flex-shrink: 0; font-size: 0.75rem;">
                        <div style="color: var(--neon-gold); font-weight: 700;">${item.size_mb} MB</div>
                        <div style="color: var(--text-muted);">🎤 ${item.play_count} • ${formatRelativeTime(item.last_played)}</div>
                    </div>
                `;
                list.appendChild(row);
            });

            updateCleanupStats();
        })
        .catch(err => {
            list.innerHTML = `<div style="color: #ff5555; text-align: center; padding: 20px;">Failed to scan files: ${err.message}</div>`;
        });
}

function toggleCleanupTrack(trackId, isChecked) {
    if (isChecked) {
        selectedCleanupTrackIds.add(trackId);
    } else {
        selectedCleanupTrackIds.delete(trackId);
    }
    updateCleanupStats();
}

function selectTopNCleanup(n) {
    cleanupCandidatesList.slice(0, n).forEach(item => {
        selectedCleanupTrackIds.add(item.id);
    });
    // Check checkboxes
    document.querySelectorAll('#cleanupCandidateList input[type="checkbox"]').forEach(cb => {
        if (selectedCleanupTrackIds.has(parseInt(cb.value))) {
            cb.checked = true;
        }
    });
    updateCleanupStats();
}

function toggleAllCleanup() {
    if (selectedCleanupTrackIds.size === cleanupCandidatesList.length) {
        selectedCleanupTrackIds.clear();
    } else {
        cleanupCandidatesList.forEach(item => selectedCleanupTrackIds.add(item.id));
    }
    document.querySelectorAll('#cleanupCandidateList input[type="checkbox"]').forEach(cb => {
        cb.checked = selectedCleanupTrackIds.has(parseInt(cb.value));
    });
    updateCleanupStats();
}

function updateCleanupStats() {
    const statsEl = document.getElementById("cleanupSelectedStats");
    const btn = document.getElementById("pruneExecuteBtn");

    let totalMb = 0;
    cleanupCandidatesList.forEach(item => {
        if (selectedCleanupTrackIds.has(item.id)) {
            totalMb += item.size_mb || 0;
        }
    });
    totalMb = Math.round(totalMb * 10) / 10;

    const count = selectedCleanupTrackIds.size;
    if (statsEl) {
        statsEl.innerText = `Selected: ${count} songs (${totalMb} MB)`;
    }

    if (btn) {
        btn.disabled = count === 0;
        btn.innerText = count > 0 ? `🗑️ Prune ${count} Files (${totalMb} MB)` : `🗑️ Prune Selected Files`;
    }
}

function executePrune() {
    if (selectedCleanupTrackIds.size === 0) return;
    const count = selectedCleanupTrackIds.size;
    if (!confirm(`Are you sure you want to delete ${count} video files from disk? History and songs can be re-downloaded anytime.`)) {
        return;
    }

    const btn = document.getElementById("pruneExecuteBtn");
    if (btn) {
        btn.disabled = true;
        btn.innerText = "⏳ Deleting video files...";
    }

    fetch("/api/storage/prune", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ track_ids: Array.from(selectedCleanupTrackIds) })
    })
    .then(res => {
        if (!res.ok) throw new Error("Unauthorized or server error");
        return res.json();
    })
    .then(data => {
        showToast(`✅ Successfully pruned ${data.pruned_count} video files!`);
        selectedCleanupTrackIds.clear();
        closeCleanupModal();
        loadHostStorageStatus();
        loadLibraryTracks();
    })
    .catch(err => {
        alert("Error pruning files: " + err.message);
        if (btn) {
            btn.disabled = false;
            updateCleanupStats();
        }
    });
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
        showCustomToast(`🌴 Intermission mode: ${labels[mode] || mode}`);
    })
    .catch(err => {
        showCustomToast("⚠️ Failed to update intermission mode: " + err);
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

// -------------------------------------------------------------
// Pre-Party Starter Packs (Offline Party Preparation)
// -------------------------------------------------------------
async function loadPartyPacks() {
    const container = document.getElementById("partyPacksContainer");
    if (!container) return;

    try {
        const res = await fetch("/api/host/party-packs");
        if (!res.ok) return;
        const data = await res.json();
        const packs = data.packs || [];
        renderPartyPacks(packs);
    } catch (e) {
        console.error("[Host] Error loading party packs:", e);
    }
}

function renderPartyPacks(packs) {
    const container = document.getElementById("partyPacksContainer");
    if (!container) return;

    if (!packs || packs.length === 0) {
        container.innerHTML = `<div style="font-size:0.75rem; color:var(--text-muted); text-align:center; padding:10px;">No starter packs available.</div>`;
        return;
    }

    container.innerHTML = packs.map(p => {
        const remaining = p.track_count - p.downloaded_count;
        const isComplete = remaining <= 0;
        const badgeColor = isComplete ? "var(--neon-lime)" : "var(--neon-cyan)";
        const badgeBg = isComplete ? "rgba(0, 255, 100, 0.15)" : "rgba(0, 240, 255, 0.15)";
        const badgeText = isComplete ? `✅ ${p.track_count}/${p.track_count} Ready` : `📥 ${p.downloaded_count}/${p.track_count} Ready`;

        return `
            <div style="background: rgba(16, 28, 45, 0.85); border: 1px solid rgba(0, 240, 255, 0.25); border-radius: 10px; padding: 10px 12px;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                    <div style="font-weight: 700; font-size: 0.82rem; color: #fff;">${escapeHtml(p.name)}</div>
                    <span style="font-size: 0.7rem; font-weight: 700; padding: 2px 6px; border-radius: 6px; color: ${badgeColor}; background: ${badgeBg};">
                        ${badgeText}
                    </span>
                </div>
                <div style="font-size: 0.72rem; color: var(--text-muted); margin-bottom: 8px; line-height: 1.3;">
                    ${escapeHtml(p.description)}
                </div>
                <div style="display: flex; justify-content: flex-end;">
                    ${isComplete ? `
                        <button type="button" class="btn-ghost" disabled style="padding: 4px 10px; font-size: 0.72rem; opacity: 0.6; cursor: default; border-color: var(--neon-lime); color: var(--neon-lime);">
                            ✅ Pack Installed
                        </button>
                    ` : `
                        <button type="button" onclick="downloadPartyPack('${p.id}')" id="btnPack_${p.id}" class="btn-ghost" style="padding: 4px 12px; font-size: 0.72rem; border-color: var(--neon-cyan); color: var(--neon-cyan); font-weight: 700;">
                            ⚡ Download Pack (${remaining} songs)
                        </button>
                    `}
                </div>
            </div>
        `;
    }).join("");
}

async function downloadPartyPack(packId) {
    const btn = document.getElementById(`btnPack_${packId}`);
    if (btn) {
        btn.disabled = true;
        btn.innerText = "⏳ Enqueuing...";
    }

    try {
        const res = await fetch(`/api/host/party-packs/${encodeURIComponent(packId)}/download`, {
            method: "POST"
        });
        if (res.ok) {
            const data = await res.json();
            showToast(`🚀 ${data.enqueued} songs enqueued for offline party! 🌺`);
            loadPartyPacks();
        } else {
            showToast("⚠️ Failed to enqueue party pack");
            if (btn) {
                btn.disabled = false;
                btn.innerText = "⚡ Try Again";
            }
        }
    } catch (e) {
        console.error("[Host] Error downloading pack:", e);
        showToast("⚠️ Network error starting pack download");
        if (btn) {
            btn.disabled = false;
            btn.innerText = "⚡ Try Again";
        }
    }
}





