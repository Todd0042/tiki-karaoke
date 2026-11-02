/**
 * Tiki Karaoke — CDG (Compact Disc + Graphics) Canvas Engine
 * 
 * High-performance, customizable renderer for MP3+CDG and Karaoke CD subcodes.
 * Supports transparent backgrounds, neon Tiki re-theming, glowing wipes, and smooth HD scaling.
 */

class CDGRenderer {
    constructor(canvas, options = {}) {
        this.canvas = canvas;
        this.ctx = canvas.getContext("2d", { willReadFrequently: true });
        
        // CDG Native Specs
        this.WIDTH = 300;
        this.HEIGHT = 216;
        this.VISIBLE_X = 6;
        this.VISIBLE_Y = 12;
        this.VISIBLE_WIDTH = 288;
        this.VISIBLE_HEIGHT = 192;
        this.PACKET_RATE = 300; // 300 packets per second

        // Offscreen pixel buffer (indexed 0-15)
        this.pixelBuffer = new Uint8Array(this.WIDTH * this.HEIGHT);
        this.colorTable = new Array(16).fill(null).map(() => ({ r: 0, g: 0, b: 0, a: 255 }));
        
        // Offscreen canvas for rendering native 300x216 buffer
        this.offscreenCanvas = document.createElement("canvas");
        this.offscreenCanvas.width = this.WIDTH;
        this.offscreenCanvas.height = this.HEIGHT;
        this.offscreenCtx = this.offscreenCanvas.getContext("2d");
        this.imageData = this.offscreenCtx.createImageData(this.WIDTH, this.HEIGHT);

        // Raw packet data
        this.packets = null;
        this.totalPackets = 0;
        this.currentPacketIndex = 0;
        this.duration = 0;

        // Customization Options
        this.theme = options.theme || localStorage.getItem("tiki_cdg_theme") || "neon"; // neon | sunset | cyberpunk | classic
        this.transparentBackground = options.transparentBackground !== undefined 
            ? options.transparentBackground 
            : (localStorage.getItem("tiki_cdg_transparent") !== "false");
        this.glow = options.glow !== undefined 
            ? options.glow 
            : (localStorage.getItem("tiki_cdg_glow") !== "false");
        this.scale = Number(options.scale || localStorage.getItem("tiki_cdg_scale") || 1.15);

        // Predefined Themes for remapping 90s CDG colors to vibrant party visuals
        this.THEMES = {
            neon: {
                name: "🌴 Tiki Neon",
                bgTransparent: true,
                glowColor: "#00f0ff",
                glowBlur: 14,
                remap: {
                    0: { r: 0, g: 0, b: 0, a: 0 },          // Transparent BG
                    1: { r: 255, g: 0, b: 128, a: 255 },     // Base text: Hot Neon Pink
                    2: { r: 0, g: 240, b: 255, a: 255 },     // Active wipe: Electric Cyan
                    3: { r: 57, g: 255, b: 20, a: 255 },     // Accent: Lime Green
                    4: { r: 255, g: 183, b: 3, a: 255 },     // Gold
                    5: { r: 180, g: 100, b: 255, a: 255 }    // Lavender
                }
            },
            sunset: {
                name: "🌅 Tropical Sunset",
                bgTransparent: true,
                glowColor: "#ff7700",
                glowBlur: 12,
                remap: {
                    0: { r: 0, g: 0, b: 0, a: 0 },          // Transparent BG
                    1: { r: 255, g: 183, b: 3, a: 255 },     // Warm Amber Gold
                    2: { r: 255, g: 0, b: 85, a: 255 },      // Fiery Coral Wipe
                    3: { r: 255, g: 220, b: 100, a: 255 },   // Soft Sunlight
                    4: { r: 255, g: 90, b: 95, a: 255 }      // Peach
                }
            },
            cyberpunk: {
                name: "⚡ Cyberpunk",
                bgTransparent: true,
                glowColor: "#00ffea",
                glowBlur: 16,
                remap: {
                    0: { r: 0, g: 0, b: 0, a: 0 },
                    1: { r: 0, g: 240, b: 255, a: 255 },     // Cyan
                    2: { r: 255, g: 255, b: 0, a: 255 },     // Laser Yellow Wipe
                    3: { r: 255, g: 0, b: 100, a: 255 }      // Magenta
                }
            },
            classic: {
                name: "📻 Authentic 90s CD+G",
                bgTransparent: false,
                glowColor: "transparent",
                glowBlur: 0,
                remap: null // Preserves raw disc palette unmodified
            }
        };

        this.resetState();
    }

    resetState() {
        this.pixelBuffer.fill(0);
        this.currentPacketIndex = 0;
        this.colorTable = new Array(16).fill(null).map(() => ({ r: 0, g: 0, b: 0, a: 255 }));
        this.clearCanvas();
    }

    clearCanvas() {
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    }

    /**
     * Loads raw ArrayBuffer containing .cdg binary subcodes.
     */
    load(arrayBuffer) {
        this.packets = new Uint8Array(arrayBuffer);
        this.totalPackets = Math.floor(this.packets.length / 24);
        this.duration = this.totalPackets / this.PACKET_RATE;
        this.resetState();
        console.log(`[CDG] Loaded ${this.totalPackets} packets (${this.duration.toFixed(1)}s total)`);
    }

    setTheme(themeName) {
        if (this.THEMES[themeName]) {
            this.theme = themeName;
            localStorage.setItem("tiki_cdg_theme", themeName);
            this.redrawCurrentFrame();
        }
    }

    setTransparent(enabled) {
        this.transparentBackground = !!enabled;
        localStorage.setItem("tiki_cdg_transparent", String(this.transparentBackground));
        this.redrawCurrentFrame();
    }

    setGlow(enabled) {
        this.glow = !!enabled;
        localStorage.setItem("tiki_cdg_glow", String(this.glow));
        this.redrawCurrentFrame();
    }

    setScale(scaleNum) {
        this.scale = Math.max(0.8, Math.min(2.0, Number(scaleNum) || 1.15));
        localStorage.setItem("tiki_cdg_scale", String(this.scale));
        this.redrawCurrentFrame();
    }

    /**
     * Synchronizes CDG state to the given audio playback time in seconds.
     */
    sync(timeSeconds) {
        if (!this.packets || this.totalPackets === 0) return;

        const targetPacket = Math.min(this.totalPackets, Math.max(0, Math.floor(timeSeconds * this.PACKET_RATE)));

        // If seeking backward: rewind to start
        if (targetPacket < this.currentPacketIndex) {
            this.resetState();
        }

        // Process forward packets
        let needsRedraw = false;
        while (this.currentPacketIndex < targetPacket) {
            const offset = this.currentPacketIndex * 24;
            const cmd = this.packets[offset] & 0x3F;
            if (cmd === 9) { // CDG command identifier
                this.executePacket(offset);
                needsRedraw = true;
            }
            this.currentPacketIndex++;
        }

        if (needsRedraw) {
            this.draw();
        }
    }

    /**
     * Execute a single 24-byte CDG subcode packet.
     */
    executePacket(offset) {
        const inst = this.packets[offset + 1] & 0x3F;
        const data = this.packets.subarray(offset + 4, offset + 20);

        switch (inst) {
            case 1: // MemoryPreset: Clear screen with color
                {
                    const color = data[0] & 0x0F;
                    const repeat = data[1] & 0x0F;
                    if (repeat === 0) {
                        this.pixelBuffer.fill(color);
                    }
                }
                break;

            case 2: // BorderPreset: Set border color
                {
                    const color = data[0] & 0x0F;
                    // Top & bottom borders (12 rows each)
                    for (let y = 0; y < this.VISIBLE_Y; y++) {
                        this.pixelBuffer.fill(color, y * this.WIDTH, (y + 1) * this.WIDTH);
                    }
                    for (let y = this.HEIGHT - this.VISIBLE_Y; y < this.HEIGHT; y++) {
                        this.pixelBuffer.fill(color, y * this.WIDTH, (y + 1) * this.WIDTH);
                    }
                    // Left & right borders (6 cols each)
                    for (let y = this.VISIBLE_Y; y < this.HEIGHT - this.VISIBLE_Y; y++) {
                        const rowOffset = y * this.WIDTH;
                        this.pixelBuffer.fill(color, rowOffset, rowOffset + this.VISIBLE_X);
                        this.pixelBuffer.fill(color, rowOffset + this.WIDTH - this.VISIBLE_X, rowOffset + this.WIDTH);
                    }
                }
                break;

            case 6: // TileBlock: Normal 6x12 tile draw
                this.drawTile(data, false);
                break;

            case 38: // TileBlockXOR: XOR 6x12 tile draw
                this.drawTile(data, true);
                break;

            case 20: // ScrollPreset
                this.scroll(data, false);
                break;

            case 24: // ScrollCopy
                this.scroll(data, true);
                break;

            case 28: // DefineColorTableLower (0..7)
                this.defineColors(data, 0);
                break;

            case 30: // DefineColorTableUpper (8..15)
                this.defineColors(data, 8);
                break;
        }
    }

    drawTile(data, isXor) {
        const color0 = data[0] & 0x0F;
        const color1 = data[1] & 0x0F;
        const row = data[2] & 0x1F;
        const col = data[3] & 0x3F;

        if (row >= 18 || col >= 50) return; // Bounds guard

        const startX = col * 6;
        const startY = row * 12;

        for (let r = 0; r < 12; r++) {
            const byte = data[4 + r] & 0x3F;
            const y = startY + r;
            if (y >= this.HEIGHT) break;
            const rowOffset = y * this.WIDTH;

            for (let c = 0; c < 6; c++) {
                const x = startX + c;
                if (x >= this.WIDTH) break;

                const bit = (byte >> (5 - c)) & 1;
                const targetColor = bit ? color1 : color0;
                const idx = rowOffset + x;

                if (isXor) {
                    this.pixelBuffer[idx] ^= targetColor;
                } else {
                    this.pixelBuffer[idx] = targetColor;
                }
            }
        }
    }

    scroll(data, isCopy) {
        const color = data[0] & 0x0F;
        const hCmd = (data[1] >> 4) & 0x03;
        const vCmd = (data[2] >> 4) & 0x03;

        let dx = 0;
        let dy = 0;
        if (hCmd === 1) dx = 6;
        else if (hCmd === 2) dx = -6;

        if (vCmd === 1) dy = 12;
        else if (vCmd === 2) dy = -12;

        if (dx === 0 && dy === 0) return;

        const temp = new Uint8Array(this.pixelBuffer);
        for (let y = 0; y < this.HEIGHT; y++) {
            for (let x = 0; x < this.WIDTH; x++) {
                const srcX = x - dx;
                const srcY = y - dy;
                const destIdx = y * this.WIDTH + x;

                if (srcX >= 0 && srcX < this.WIDTH && srcY >= 0 && srcY < this.HEIGHT) {
                    this.pixelBuffer[destIdx] = temp[srcY * this.WIDTH + srcX];
                } else if (isCopy) {
                    const wrapX = (srcX + this.WIDTH) % this.WIDTH;
                    const wrapY = (srcY + this.HEIGHT) % this.HEIGHT;
                    this.pixelBuffer[destIdx] = temp[wrapY * this.WIDTH + wrapX];
                } else {
                    this.pixelBuffer[destIdx] = color;
                }
            }
        }
    }

    defineColors(data, startIndex) {
        for (let i = 0; i < 8; i++) {
            const b0 = data[i * 2] & 0x3F;
            const b1 = data[i * 2 + 1] & 0x3F;

            // 4-bit per channel RGB unpacked to 8-bit (0..255)
            const r = ((b0 >> 2) & 0x0F) * 17;
            const g = (((b0 & 0x03) << 2) | ((b1 >> 4) & 0x03)) * 17;
            const b = (b1 & 0x0F) * 17;

            this.colorTable[startIndex + i] = { r, g, b, a: 255 };
        }
    }

    /**
     * Resolves the display color for a palette index taking into account the active theme.
     */
    resolveColor(colorIndex) {
        const currentTheme = this.THEMES[this.theme] || this.THEMES.neon;
        
        // If theme has explicit remapping
        if (currentTheme.remap && currentTheme.remap[colorIndex]) {
            const remapped = currentTheme.remap[colorIndex];
            // If transparent BG is enabled, color 0 is always transparent
            if (colorIndex === 0 && (this.transparentBackground || currentTheme.bgTransparent)) {
                return { r: 0, g: 0, b: 0, a: 0 };
            }
            return remapped;
        }

        const nativeCol = this.colorTable[colorIndex] || { r: 0, g: 0, b: 0, a: 255 };
        if (colorIndex === 0 && this.transparentBackground) {
            return { r: 0, g: 0, b: 0, a: 0 };
        }
        return nativeCol;
    }

    /**
     * Renders the internal 300x216 buffer to the display canvas with scaling and glow filters.
     */
    draw() {
        const imgData = this.imageData.data;
        let bufIdx = 0;
        let imgIdx = 0;

        for (let y = 0; y < this.HEIGHT; y++) {
            for (let x = 0; x < this.WIDTH; x++) {
                const colorIdx = this.pixelBuffer[bufIdx++];
                const col = this.resolveColor(colorIdx);

                imgData[imgIdx] = col.r;
                imgData[imgIdx + 1] = col.g;
                imgData[imgIdx + 2] = col.b;
                imgData[imgIdx + 3] = col.a;
                imgIdx += 4;
            }
        }

        // Put image to offscreen canvas
        this.offscreenCtx.putImageData(this.imageData, 0, 0);

        // Clear display canvas
        const targetW = this.canvas.width;
        const targetH = this.canvas.height;
        this.ctx.clearRect(0, 0, targetW, targetH);

        // Calculate aspect-preserving centered crop to visible 288x192 area
        const scaleFactor = Math.min(
            (targetW / this.VISIBLE_WIDTH) * this.scale,
            (targetH / this.VISIBLE_HEIGHT) * this.scale
        );
        const drawW = this.VISIBLE_WIDTH * scaleFactor;
        const drawH = this.VISIBLE_HEIGHT * scaleFactor;
        const destX = (targetW - drawW) / 2;
        const destY = (targetH - drawH) / 2;

        this.ctx.save();
        this.ctx.imageSmoothingEnabled = true;
        this.ctx.imageSmoothingQuality = "high";

        // Apply neon stage glow if enabled
        const themeConfig = this.THEMES[this.theme] || this.THEMES.neon;
        if (this.glow && themeConfig.glowBlur > 0) {
            this.ctx.shadowColor = themeConfig.glowColor;
            this.ctx.shadowBlur = themeConfig.glowBlur;
        }

        // Draw cropped visible region from offscreen canvas
        this.ctx.drawImage(
            this.offscreenCanvas,
            this.VISIBLE_X, this.VISIBLE_Y, this.VISIBLE_WIDTH, this.VISIBLE_HEIGHT,
            destX, destY, drawW, drawH
        );

        this.ctx.restore();
    }

    redrawCurrentFrame() {
        if (this.packets && this.totalPackets > 0) {
            this.draw();
        }
    }
}

// Export for browser global
window.CDGRenderer = CDGRenderer;
