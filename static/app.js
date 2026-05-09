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
			return "unknown";
		}

		if (payload && typeof payload.active === "boolean") {
			return payload.active ? "active" : "inactive";
		}

		return "unknown";
	}

	function statusLabelFor(status, since = "") {
		if (status === "active") {
			return since ? `Active since ${since}` : "Active now";
		}

		if (status === "inactive") {
			return since ? `Inactive since ${since}` : "Inactive";
		}

		return "Status unavailable";
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
				setProfileStatus("unknown");
				return;
			}

			const payload = await response.json();
			const status = normaliseProfileStatus(payload);
			const since = typeof payload.since === "string" ? payload.since : "";
			setProfileStatus(status, since);
		} catch (_) {
			setProfileStatus("unknown");
		}
	}

	function initProfileStatus() {
		if (!profileAvatarEl || !profileStatusTextEl) return;

		setProfileStatus(profileAvatarEl.dataset.activeStatus || "unknown");
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
