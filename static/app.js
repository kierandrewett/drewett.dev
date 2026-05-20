(() => {
	const profileAvatarEl = document.getElementById("profile-avatar");
	const profileStatusTextEl = document.getElementById("profile-status-text");
	const activeStatusEndpoint = "/api/active";

	function normaliseProfileStatus(payload) {
		const hasStatus = typeof payload?.status === "string";
		const status = hasStatus ? payload.status.trim().toLowerCase() : "";

		if (["yes", "active", "online", "true"].includes(status)) {
			return "active";
		}

		if (["no", "inactive", "offline", "false"].includes(status)) {
			return "inactive";
		}

		if (hasStatus) {
			return "inactive";
		}

		if (payload && typeof payload.active === "boolean") {
			return payload.active ? "active" : "inactive";
		}

		return "inactive";
	}

	function statusLabelFor(status, since = "") {
		if (status === "active") {
			return since ? `Active since ${since}` : "Active now";
		}

		if (status === "inactive") {
			return since ? `Offline since ${since}` : "Offline";
		}

		return "Offline";
	}

	function setProfileStatus(status, since = "") {
		if (!profileAvatarEl || !profileStatusTextEl) return;

		const label = statusLabelFor(status, since);
		profileAvatarEl.dataset.activeStatus = status;
		profileAvatarEl.title = label;
		profileStatusTextEl.textContent = label;
	}

	async function refreshProfileStatus() {
		try {
			const response = await fetch(activeStatusEndpoint, { cache: "no-store" });
			if (!response.ok) {
				setProfileStatus("inactive");
				return;
			}

			const payload = await response.json();
			const status = normaliseProfileStatus(payload);
			const since = typeof payload.since === "string" ? payload.since : "";
			setProfileStatus(status, since);
		} catch (_) {
			setProfileStatus("inactive");
		}
	}

	function initProfileStatus() {
		if (!profileAvatarEl || !profileStatusTextEl) return;

		setProfileStatus(profileAvatarEl.dataset.activeStatus || "inactive");
		refreshProfileStatus();
		setInterval(refreshProfileStatus, 30000);
	}

	initProfileStatus();

	const titleWrap = document.getElementById("track-title-wrap");
	const titleEl = document.getElementById("track-title");
	const artistEl = document.getElementById("artist-name");
	const albumEl = document.getElementById("album-art");
	const albumGlowAEl = document.getElementById("album-art-glow-a");
	const albumGlowBEl = document.getElementById("album-art-glow-b");
	const albumGlowEls = [albumGlowAEl, albumGlowBEl].filter(Boolean);
	const artIconEl = document.getElementById("art-icon");
	const progressEl = document.getElementById("progress");
	const progressCurrentEl = document.getElementById("progress-current");
	const progressDurationEl = document.getElementById("progress-duration");
	const progressBarEl = document.getElementById("progress-bar-fill");
	if (
		!titleWrap ||
		!titleEl ||
		!artistEl ||
		!albumEl ||
		!artIconEl ||
		!progressEl ||
		!progressCurrentEl ||
		!progressDurationEl ||
		!progressBarEl
	) {
		return;
	}

	let data = null;
	let progress = null;
	let overflow = false;
	let raf = null;
	let progressBarRaf = null;
	let progressBarWidth = 0;
	let activeGlowIndex = 0;
	let hasInitializedGlow = false;
	let currentAlbumArtURL = null;
	let ws = null;
	let wsConnected = false;
	const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
	const fallbackAlbumArtURL = "/images/album_art.svg";
	const initialState = {
		ok: titleWrap.dataset.initialOk === "true",
		trackName: titleWrap.dataset.initialTrack || "",
		artistName: titleWrap.dataset.initialArtist || "",
		albumArt: titleWrap.dataset.initialArt || ""
	};

	function hardReset() {
		titleEl.style.animation = "none";
		titleEl.style.transform = "translateX(0)";
		titleEl.classList.remove("marquee");
		void titleEl.offsetWidth;
		titleEl.style.animation = "";
	}

	function isFallbackGlowImage(backgroundImage) {
		return !backgroundImage || backgroundImage.includes("album_art.svg");
	}

	function setAlbumArtGlow(animate = true) {
		if (albumGlowEls.length === 0) return;
		const nextBackgroundImage =
			albumEl.style.backgroundImage || `url("${fallbackAlbumArtURL}")`;
		const activeGlowEl = albumGlowEls[activeGlowIndex];
		const shouldAnimate =
			animate &&
			hasInitializedGlow &&
			!isFallbackGlowImage(activeGlowEl.style.backgroundImage);

		if (activeGlowEl.style.backgroundImage === nextBackgroundImage) {
			return;
		}

		if (!shouldAnimate || !activeGlowEl.style.backgroundImage) {
			for (const glowEl of albumGlowEls) {
				glowEl.style.transition = "none";
				glowEl.classList.remove("is-visible");
			}
			activeGlowEl.style.backgroundImage = nextBackgroundImage;
			activeGlowEl.classList.add("is-visible");
			hasInitializedGlow = true;
			return;
		}

		const nextGlowIndex = activeGlowIndex === 0 ? 1 : 0;
		const nextGlowEl = albumGlowEls[nextGlowIndex];
		for (const glowEl of albumGlowEls) {
			glowEl.style.transition = "";
		}
		nextGlowEl.style.backgroundImage = nextBackgroundImage;
		nextGlowEl.classList.add("is-visible");
		activeGlowEl.classList.remove("is-visible");
		activeGlowIndex = nextGlowIndex;
	}

	function setAlbumArtLoadedState(isLoaded) {
		albumEl.classList.toggle("loaded", isLoaded);
		for (const glowEl of albumGlowEls) {
			glowEl.classList.toggle("loaded", isLoaded);
		}
	}

	function setArtIcon(mode, text = "") {
		artIconEl.className = "art-icon";
		artIconEl.textContent = text;

		if (mode) {
			artIconEl.classList.add(mode);
		}
	}

	function showFallbackAlbumArt(animateGlow = true) {
		currentAlbumArtURL = fallbackAlbumArtURL;
		albumEl.style.backgroundImage = `url("${fallbackAlbumArtURL}")`;
		setAlbumArtGlow(animateGlow);
		setAlbumArtLoadedState(true);
		setArtIcon(null, "");
	}

	function loadAlbumArt(url, animateGlow = true) {
		if (url === currentAlbumArtURL && url !== null) return;

		setAlbumArtLoadedState(false);
		albumEl.style.backgroundImage = "";

		if (!url) {
			showFallbackAlbumArt(animateGlow);
			return;
		}

		setArtIcon("loading", "");
		const img = new Image();
		img.src = url;
		img.style.display = "none";
		document.body.appendChild(img);

		img.onload = () => {
			currentAlbumArtURL = url;
			img.remove();
			albumEl.style.backgroundImage = `url("${url}")`;
			setAlbumArtGlow(animateGlow);
			setAlbumArtLoadedState(true);
		};

		img.onerror = () => {
			img.remove();
			showFallbackAlbumArt(animateGlow);
		};
	}

	function checkOverflow() {
		const containerWidth = titleWrap.clientWidth;
		const contentWidth = titleEl.scrollWidth;

		titleWrap.style.setProperty("--visible-width", `${containerWidth}px`);
		const newOverflow = contentWidth > containerWidth + 1;

		if (newOverflow !== overflow) {
			overflow = newOverflow;
			titleEl.classList.toggle("marquee", overflow);
			if (!overflow) hardReset();
		}

		if (overflow) updateMarqueeSpeed();
	}

	function updateMarqueeSpeed() {
		const contentWidth = titleEl.scrollWidth;
		const containerWidth = titleWrap.clientWidth;
		const distance = contentWidth - containerWidth;

		if (distance <= 0) {
			titleEl.style.setProperty("--marquee-time", "0s");
			return;
		}

		const duration = distance * 0.25;
		titleEl.style.setProperty("--marquee-time", `${duration}s`);
	}

	function updateMask() {
		const containerWidth = titleWrap.clientWidth;
		const contentWidth = titleEl.scrollWidth;
		const maxFade = 16;
		const fadeRamp = 28;

		if (contentWidth <= containerWidth) {
			titleWrap.style.setProperty("--left-fade", "0px");
			titleWrap.style.setProperty("--right-fade", "0px");
			return;
		}

		let x = 0;
		const matrix = getComputedStyle(titleEl).transform;

		if (matrix && matrix !== "none") {
			const match = matrix.match(/matrix.*\((.+)\)/);
			if (match) {
				const parts = match[1].split(",").map((part) => part.trim());
				if (parts.length >= 6) x = parseFloat(parts[4]);
			}
		}

		const end = -(contentWidth - containerWidth);
		const distance = Math.abs(end);
		const moved = Math.max(0, -x);
		const remaining = Math.max(0, distance - moved);
		const leftStrength = Math.min(1, moved / fadeRamp);
		const rightStrength = Math.min(1, remaining / fadeRamp);

		titleWrap.style.setProperty("--left-fade", `${maxFade * leftStrength}px`);
		titleWrap.style.setProperty("--right-fade", `${maxFade * rightStrength}px`);
	}

	function trackTransform() {
		updateMask();
		raf = requestAnimationFrame(trackTransform);
	}

	function formatTime(ms) {
		const totalSeconds = Math.floor(ms / 1000);
		const minutes = Math.floor(totalSeconds / 60);
		const seconds = totalSeconds % 60;
		return `${minutes}:${String(seconds).padStart(2, "0")}`;
	}

	function easeOutCubic(value) {
		return 1 - Math.pow(1 - value, 3);
	}

	function setProgressBarWidth(width) {
		const clampedWidth = Math.max(0, Math.min(100, width));
		progressBarWidth = clampedWidth;
		progressBarEl.style.width = `${clampedWidth}%`;
	}

	function animateProgressBarWidth(targetWidth, duration = 320) {
		const clampedTarget = Math.max(0, Math.min(100, targetWidth));

		if (prefersReducedMotion.matches) {
			if (progressBarRaf !== null) {
				cancelAnimationFrame(progressBarRaf);
				progressBarRaf = null;
			}
			setProgressBarWidth(clampedTarget);
			return;
		}

		if (progressBarRaf !== null) {
			cancelAnimationFrame(progressBarRaf);
			progressBarRaf = null;
		}

		const startWidth = progressBarWidth;
		const delta = clampedTarget - startWidth;

		if (Math.abs(delta) < 0.05) {
			setProgressBarWidth(clampedTarget);
			return;
		}

		const startTime = performance.now();
		const step = (now) => {
			const elapsed = now - startTime;
			const t = Math.min(1, elapsed / duration);
			const eased = easeOutCubic(t);
			setProgressBarWidth(startWidth + delta * eased);

			if (t < 1) {
				progressBarRaf = requestAnimationFrame(step);
				return;
			}

			progressBarRaf = null;
		};

		progressBarRaf = requestAnimationFrame(step);
	}

	function updateProgress() {
		if (progress && data && data.ok && data.track_name) {
			progressEl.hidden = false;
			progressCurrentEl.textContent = formatTime(progress.position_ms || 0);
			progressDurationEl.textContent = formatTime(progress.duration_ms || 0);
			const width = progress.duration_ms
				? (progress.position_ms / progress.duration_ms) * 100
				: 0;
			animateProgressBarWidth(width);
		} else {
			progressEl.hidden = true;
			if (progressBarRaf !== null) {
				cancelAnimationFrame(progressBarRaf);
				progressBarRaf = null;
			}
			setProgressBarWidth(0);
		}
	}

	function updateTrackDisplay() {
		if (data && data.ok) {
			if (data.track_name) {
				titleEl.textContent = data.track_name;
			} else {
				titleEl.textContent = "Nothing playing";
			}
			artistEl.textContent = data.artist_name || "";
		} else {
			titleEl.textContent = "Connecting...";
			artistEl.textContent = "";
		}

		checkOverflow();
		updateMask();
		updateProgress();
	}

	async function fallbackAPI() {
		if (wsConnected) return;

		try {
			const response = await fetch("/api/lastfm");
			if (!response.ok) return;
			data = await response.json();

			if (data && data.ok) {
				const art =
					data.album_art && !data.album_art.includes("2a96cbd8b46e442fc41c2b86b821562f")
						? data.album_art
						: null;
				loadAlbumArt(art);
				updateTrackDisplay();
			}
		} catch (_) {
			// ignore
		}
	}

	function connectWS() {
		try {
			ws = new WebSocket("wss://lastfm.drewett.dev");

			ws.onopen = () => {
				wsConnected = true;
			};

			ws.onmessage = (event) => {
				let message = null;
				try {
					message = JSON.parse(event.data);
				} catch (_) {
					return;
				}

				if ("playing" in message) {
					if (!message.playing) {
						data = { ok: true };
						loadAlbumArt(null);
						updateTrackDisplay();
					}

					if (message.position_ms && message.duration_ms) {
						progress = {
							position_ms: message.position_ms,
							duration_ms: message.duration_ms
						};
					} else {
						progress = null;
					}

					updateProgress();
					return;
				}

				if ("track" in message) {
					data = {
						ok: true,
						track_name: message.track,
						artist_name: message.artist,
						album: message.album,
						album_art:
							message.album_art ||
							message.track_info?.album?.image?.at(-1)?.url ||
							null
					};
					loadAlbumArt(data.album_art);
					updateTrackDisplay();
				}
			};

			ws.onclose = () => {
				wsConnected = false;
				setTimeout(connectWS, 5000);
			};

			ws.onerror = () => {
				// ignore
			};
		} catch (_) {
			// ignore
		}
	}

	window.addEventListener("resize", () => {
		checkOverflow();
		updateMask();
	});

	loadAlbumArt(null, false);
	if (initialState.ok) {
		data = {
			ok: true,
			track_name: initialState.trackName || null,
			artist_name: initialState.artistName || null,
			album_art: initialState.albumArt || null
		};
		loadAlbumArt(initialState.albumArt || null, false);
	} else if (initialState.albumArt) {
		loadAlbumArt(initialState.albumArt, false);
	}

	updateTrackDisplay();
	connectWS();
	setInterval(fallbackAPI, 5000);
	requestAnimationFrame(() => {
		raf = requestAnimationFrame(trackTransform);
	});
})();

(() => {
	const noteBody = document.getElementById("note-body");
	if (!noteBody) return;

	const STORAGE_KEY = "reading-prefs/v1";
	const RSVP_KEY = "reading-rsvp/v1";

	const defaults = {
		theme: "auto",
		font: "serif",
		size: 1,
		leading: 1.85,
		tracking: 0,
		width: 42
	};

	const rsvpDefaults = { wpm: 300 };

	function loadJSON(key, fallback) {
		try {
			const raw = localStorage.getItem(key);
			if (!raw) return { ...fallback };
			return { ...fallback, ...JSON.parse(raw) };
		} catch (_) {
			return { ...fallback };
		}
	}

	function saveJSON(key, value) {
		try {
			localStorage.setItem(key, JSON.stringify(value));
		} catch (_) {
			// ignore quota / disabled storage
		}
	}

	const prefs = loadJSON(STORAGE_KEY, defaults);
	const rsvpPrefs = loadJSON(RSVP_KEY, rsvpDefaults);

	const sizeEl = document.getElementById("reading-size");
	const sizeValueEl = document.getElementById("reading-size-value");
	const leadingEl = document.getElementById("reading-leading");
	const leadingValueEl = document.getElementById("reading-leading-value");
	const trackingEl = document.getElementById("reading-tracking");
	const trackingValueEl = document.getElementById("reading-tracking-value");
	const widthEl = document.getElementById("reading-width");
	const widthValueEl = document.getElementById("reading-width-value");
	const themeSegment = document.querySelector('[data-setting="theme"]');
	const fontSegment = document.querySelector('[data-setting="font"]');
	const settingsEl = document.getElementById("reading-settings");
	const settingsToggle = document.getElementById("reading-rail-settings");
	const settingsResetEl = document.getElementById("reading-settings-reset");

	function applyPrefs() {
		noteBody.dataset.font = prefs.font;
		noteBody.style.setProperty("--reading-size", `${prefs.size}rem`);
		noteBody.style.setProperty("--reading-leading", String(prefs.leading));
		noteBody.style.setProperty("--reading-tracking", `${prefs.tracking}em`);
		noteBody.style.setProperty("--reading-width", `${prefs.width}rem`);

		if (prefs.theme === "auto") {
			document.documentElement.removeAttribute("data-reading-theme");
		} else {
			document.documentElement.setAttribute("data-reading-theme", prefs.theme);
		}
	}

	function syncControls() {
		if (sizeEl) sizeEl.value = String(prefs.size);
		if (sizeValueEl) sizeValueEl.textContent = `${prefs.size.toFixed(2)}×`;
		if (leadingEl) leadingEl.value = String(prefs.leading);
		if (leadingValueEl) leadingValueEl.textContent = prefs.leading.toFixed(2);
		if (trackingEl) trackingEl.value = String(prefs.tracking);
		if (trackingValueEl) trackingValueEl.textContent = `${prefs.tracking.toFixed(3)}em`;
		if (widthEl) widthEl.value = String(prefs.width);
		if (widthValueEl) widthValueEl.textContent = `${prefs.width}rem`;
		syncSegment(themeSegment, prefs.theme);
		syncSegment(fontSegment, prefs.font);
	}

	function syncSegment(segmentEl, value) {
		if (!segmentEl) return;
		for (const button of segmentEl.querySelectorAll("button")) {
			const isActive = button.dataset.value === value;
			button.setAttribute("aria-checked", isActive ? "true" : "false");
		}
	}

	function persist() {
		saveJSON(STORAGE_KEY, prefs);
	}

	function bindSegment(segmentEl, key) {
		if (!segmentEl) return;
		segmentEl.addEventListener("click", (event) => {
			const button = event.target.closest("button[data-value]");
			if (!button) return;
			prefs[key] = button.dataset.value;
			syncControls();
			applyPrefs();
			persist();
		});
	}

	function bindRange(el, key, parse) {
		if (!el) return;
		el.addEventListener("input", () => {
			prefs[key] = parse(el.value);
			syncControls();
			applyPrefs();
			persist();
		});
	}

	bindSegment(themeSegment, "theme");
	bindSegment(fontSegment, "font");
	bindRange(sizeEl, "size", parseFloat);
	bindRange(leadingEl, "leading", parseFloat);
	bindRange(trackingEl, "tracking", parseFloat);
	bindRange(widthEl, "width", (v) => parseInt(v, 10));

	if (settingsResetEl) {
		settingsResetEl.addEventListener("click", () => {
			Object.assign(prefs, defaults);
			syncControls();
			applyPrefs();
			persist();
		});
	}

	function positionSettings() {
		if (!settingsEl || !settingsToggle) return;
		const railRect = settingsToggle.getBoundingClientRect();
		const panelWidth = settingsEl.offsetWidth;
		const panelHeight = settingsEl.offsetHeight;
		const gap = 12;

		let left = railRect.right + gap;
		if (left + panelWidth + 12 > window.innerWidth) {
			left = Math.max(12, railRect.left - panelWidth - gap);
		}
		if (left + panelWidth + 12 > window.innerWidth) {
			left = Math.max(12, window.innerWidth - panelWidth - 12);
		}

		let top = railRect.top + railRect.height / 2 - panelHeight / 2;
		top = Math.max(12, Math.min(top, window.innerHeight - panelHeight - 12));

		settingsEl.style.left = `${left}px`;
		settingsEl.style.top = `${top}px`;
	}

	function openSettings() {
		if (!settingsEl || !settingsToggle) return;
		settingsEl.hidden = false;
		settingsToggle.setAttribute("aria-expanded", "true");
		positionSettings();
	}

	function closeSettings() {
		if (!settingsEl || !settingsToggle) return;
		settingsEl.hidden = true;
		settingsToggle.setAttribute("aria-expanded", "false");
	}

	if (settingsToggle && settingsEl) {
		settingsToggle.addEventListener("click", () => {
			if (settingsEl.hidden) openSettings();
			else closeSettings();
		});

		document.addEventListener("click", (event) => {
			if (settingsEl.hidden) return;
			if (settingsEl.contains(event.target)) return;
			if (settingsToggle.contains(event.target)) return;
			closeSettings();
		});

		document.addEventListener("keydown", (event) => {
			if (event.key === "Escape" && !settingsEl.hidden) closeSettings();
		});

		window.addEventListener("resize", () => {
			if (!settingsEl.hidden) positionSettings();
		});

		window.addEventListener("scroll", () => {
			if (!settingsEl.hidden) positionSettings();
		}, { passive: true });
	}

	syncControls();
	applyPrefs();

	const rsvpOverlay = document.getElementById("rsvp-overlay");
	const rsvpToggle = document.getElementById("reading-rail-rsvp");
	const rsvpStageEl = document.getElementById("rsvp-stage");
	const rsvpPreEl = document.getElementById("rsvp-word-pre");
	const rsvpPivotEl = document.getElementById("rsvp-word-pivot");
	const rsvpPostEl = document.getElementById("rsvp-word-post");
	const rsvpWordEl = document.getElementById("rsvp-word");
	const rsvpPlayEl = document.getElementById("rsvp-play");
	const rsvpRestartEl = document.getElementById("rsvp-restart");
	const rsvpBackEl = document.getElementById("rsvp-back");
	const rsvpFwdEl = document.getElementById("rsvp-fwd");
	const rsvpCloseEl = document.getElementById("rsvp-close");
	const rsvpBackdropEl = document.getElementById("rsvp-backdrop");
	const rsvpWpmEl = document.getElementById("rsvp-wpm");
	const rsvpWpmValueEl = document.getElementById("rsvp-wpm-value");
	const rsvpProgressEl = document.getElementById("rsvp-progress-fill");
	const rsvpPositionEl = document.getElementById("rsvp-position");
	const rsvpEtaEl = document.getElementById("rsvp-eta");
	const rsvpPlayIcon = rsvpPlayEl?.querySelector('[data-icon="play"]') || null;
	const rsvpPauseIcon = rsvpPlayEl?.querySelector('[data-icon="pause"]') || null;

	let words = [];
	let cursor = 0;
	let playing = false;
	let timer = null;

	function tokenize(text) {
		return text
			.replace(/\s+/g, " ")
			.trim()
			.split(" ")
			.filter(Boolean);
	}

	function pivotIndex(word) {
		const len = word.length;
		if (len <= 1) return 0;
		if (len <= 5) return 1;
		if (len <= 9) return 2;
		if (len <= 13) return 3;
		return 4;
	}

	function renderWord() {
		if (!rsvpPreEl || !rsvpPivotEl || !rsvpPostEl) return;
		const word = words[cursor] || "";
		if (!word) {
			rsvpPreEl.textContent = "";
			rsvpPivotEl.textContent = "";
			rsvpPostEl.textContent = "";
		} else {
			const i = pivotIndex(word);
			rsvpPreEl.textContent = word.slice(0, i);
			rsvpPivotEl.textContent = word.charAt(i) || "";
			rsvpPostEl.textContent = word.slice(i + 1);
		}
		alignPivot();
		updateMeta();
	}

	function alignPivot() {
		if (!rsvpStageEl || !rsvpWordEl || !rsvpPreEl || !rsvpPivotEl) return;
		const stageWidth = rsvpStageEl.clientWidth;
		const preWidth = rsvpPreEl.getBoundingClientRect().width;
		const pivotWidth = rsvpPivotEl.getBoundingClientRect().width;
		const left = Math.round(stageWidth / 2 - preWidth - pivotWidth / 2);
		rsvpWordEl.style.left = `${left}px`;
	}

	function updateMeta() {
		const total = words.length;
		const position = Math.min(cursor + (total ? 1 : 0), total);
		if (rsvpPositionEl) rsvpPositionEl.textContent = `${position} / ${total}`;
		if (rsvpProgressEl) {
			const pct = total ? (cursor / total) * 100 : 0;
			rsvpProgressEl.style.width = `${pct}%`;
		}
		if (rsvpEtaEl) {
			const remaining = Math.max(0, total - cursor);
			const seconds = Math.round((remaining / rsvpPrefs.wpm) * 60);
			const m = Math.floor(seconds / 60);
			const s = seconds % 60;
			rsvpEtaEl.textContent = `${m}:${String(s).padStart(2, "0")} left`;
		}
	}

	function setPlayingState(isPlaying) {
		playing = isPlaying;
		if (rsvpPlayEl) rsvpPlayEl.setAttribute("aria-label", isPlaying ? "Pause" : "Play");
		toggleHidden(rsvpPlayIcon, isPlaying);
		toggleHidden(rsvpPauseIcon, !isPlaying);
	}

	function toggleHidden(el, isHidden) {
		if (!el) return;
		if (isHidden) el.setAttribute("hidden", "");
		else el.removeAttribute("hidden");
	}

	function intervalFor(word) {
		const base = 60000 / rsvpPrefs.wpm;
		if (!word) return base;
		let multiplier = 1;
		if (word.length >= 8) multiplier += 0.25;
		if (/[.!?]$/.test(word)) multiplier += 0.9;
		else if (/[,;:]$/.test(word)) multiplier += 0.4;
		return base * multiplier;
	}

	function tick() {
		if (!playing) return;
		if (cursor >= words.length) {
			pause();
			return;
		}
		renderWord();
		const word = words[cursor];
		cursor += 1;
		timer = setTimeout(tick, intervalFor(word));
	}

	function play() {
		if (!words.length) return;
		if (cursor >= words.length) cursor = 0;
		setPlayingState(true);
		tick();
	}

	function pause() {
		setPlayingState(false);
		if (timer) {
			clearTimeout(timer);
			timer = null;
		}
		updateMeta();
	}

	function restart() {
		pause();
		cursor = 0;
		renderWord();
	}

	function seek(delta) {
		const wasPlaying = playing;
		pause();
		cursor = Math.max(0, Math.min(words.length, cursor + delta));
		renderWord();
		if (wasPlaying) play();
	}

	function openRsvp() {
		if (!rsvpOverlay) return;
		words = tokenize(noteBody.innerText || noteBody.textContent || "");
		cursor = 0;
		rsvpOverlay.hidden = false;
		requestAnimationFrame(renderWord);
		setPlayingState(false);
	}

	function closeRsvp() {
		if (!rsvpOverlay) return;
		pause();
		rsvpOverlay.hidden = true;
	}

	if (rsvpWpmEl && rsvpWpmValueEl) {
		rsvpWpmEl.value = String(rsvpPrefs.wpm);
		rsvpWpmValueEl.textContent = `${rsvpPrefs.wpm} wpm`;
		rsvpWpmEl.addEventListener("input", () => {
			rsvpPrefs.wpm = parseInt(rsvpWpmEl.value, 10);
			rsvpWpmValueEl.textContent = `${rsvpPrefs.wpm} wpm`;
			saveJSON(RSVP_KEY, rsvpPrefs);
			updateMeta();
		});
	}

	if (rsvpToggle) rsvpToggle.addEventListener("click", openRsvp);
	if (rsvpCloseEl) rsvpCloseEl.addEventListener("click", closeRsvp);
	if (rsvpBackdropEl) rsvpBackdropEl.addEventListener("click", closeRsvp);
	if (rsvpPlayEl) rsvpPlayEl.addEventListener("click", () => (playing ? pause() : play()));
	if (rsvpRestartEl) rsvpRestartEl.addEventListener("click", restart);
	if (rsvpBackEl) rsvpBackEl.addEventListener("click", () => seek(-10));
	if (rsvpFwdEl) rsvpFwdEl.addEventListener("click", () => seek(10));

	window.addEventListener("resize", () => {
		if (rsvpOverlay && !rsvpOverlay.hidden) alignPivot();
	});

	document.addEventListener("keydown", (event) => {
		if (!rsvpOverlay || rsvpOverlay.hidden) return;
		if (event.key === "Escape") {
			event.preventDefault();
			closeRsvp();
		} else if (event.key === " ") {
			event.preventDefault();
			playing ? pause() : play();
		} else if (event.key === "ArrowLeft") {
			event.preventDefault();
			seek(-10);
		} else if (event.key === "ArrowRight") {
			event.preventDefault();
			seek(10);
		}
	});
})();

(() => {
	const noteBody = document.getElementById("note-body");
	if (!noteBody) return;

	const citationsByRef = new Map();

	for (const a of noteBody.querySelectorAll('a[href^="#ref-"]')) {
		const refId = a.getAttribute("href").slice(1);
		if (!citationsByRef.has(refId)) citationsByRef.set(refId, []);
		const list = citationsByRef.get(refId);
		if (!a.id) a.id = `cite-${refId}-${list.length}`;
		list.push(a);
	}

	for (const [refId, citations] of citationsByRef) {
		const refAnchor = noteBody.querySelector(`a[id="${refId}"]`);
		if (!refAnchor) continue;

		const frag = document.createDocumentFragment();
		if (citations.length === 1) {
			const back = document.createElement("a");
			back.href = `#${citations[0].id}`;
			back.className = "ref-backlink";
			back.textContent = "^";
			back.setAttribute("aria-label", "Jump back to citation");
			frag.append(back, " ");
		} else {
			const caret = document.createElement("span");
			caret.className = "ref-backlink";
			caret.textContent = "^";
			caret.setAttribute("aria-hidden", "true");
			frag.append(caret, " ");
			citations.forEach((c, i) => {
				const link = document.createElement("a");
				link.href = `#${c.id}`;
				link.className = "ref-backlink-letter";
				link.textContent = String.fromCharCode(97 + i);
				link.setAttribute("aria-label", `Jump back to citation ${i + 1}`);
				frag.appendChild(link);
				if (i < citations.length - 1) frag.append(" ");
			});
			frag.append(" ");
		}

		refAnchor.after(frag);
	}
})();

(() => {
	const noteBody = document.getElementById("note-body");
	const toc = document.getElementById("note-toc");
	const tocList = document.getElementById("note-toc-list");
	const spineActive = document.getElementById("note-toc-spine-active");
	if (!noteBody || !toc || !tocList || !spineActive) return;

	const headings = Array.from(noteBody.querySelectorAll("h2, h3"));
	if (headings.length < 2) return;

	const slugSeen = new Set();
	function slugify(text) {
		const base = text
			.toLowerCase()
			.replace(/[^a-z0-9]+/g, "-")
			.replace(/^-|-$/g, "") || "section";
		let slug = base;
		let i = 1;
		while (slugSeen.has(slug)) slug = `${base}-${i++}`;
		slugSeen.add(slug);
		return slug;
	}

	const entries = headings.map((h) => {
		const id = h.id || slugify(h.textContent || "");
		h.id = id;
		h.style.scrollMarginTop = "4rem";
		const li = document.createElement("li");
		if (h.tagName === "H3") li.className = "is-h3";
		const a = document.createElement("a");
		a.href = `#${id}`;
		a.textContent = h.textContent || "";
		a.dataset.tocLink = id;
		li.appendChild(a);
		tocList.appendChild(li);
		return { id, heading: h, link: a };
	});

	toc.hidden = false;

	let activeId = null;

	function setActive(id) {
		if (id === activeId) return;
		activeId = id;
		let activeLink = null;
		for (const entry of entries) {
			const on = entry.id === id;
			entry.link.classList.toggle("is-active", on);
			if (on) activeLink = entry.link;
		}
		if (!activeLink) return;

		const tocBody = toc.querySelector(".note-toc-body");
		const bodyRect = tocBody.getBoundingClientRect();
		const linkRect = activeLink.getBoundingClientRect();
		const y = linkRect.top - bodyRect.top + tocBody.scrollTop;
		const h = linkRect.height;
		spineActive.setAttribute("y", String(Math.max(0, Math.round(y))));
		spineActive.setAttribute("height", String(Math.max(0, Math.round(h))));
	}

	function updateActiveOnScroll() {
		const offset = 120;
		let active = entries[0];
		for (const entry of entries) {
			const top = entry.heading.getBoundingClientRect().top;
			if (top <= offset) active = entry;
			else break;
		}
		setActive(active.id);
	}

	window.addEventListener("scroll", updateActiveOnScroll, { passive: true });
	window.addEventListener("resize", updateActiveOnScroll);
	requestAnimationFrame(() => {
		requestAnimationFrame(updateActiveOnScroll);
	});
})();
