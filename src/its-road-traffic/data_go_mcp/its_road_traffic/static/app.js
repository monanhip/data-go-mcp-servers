/* 전국 도로 소통정보 — 모바일/데스크탑 하이브리드 PWA (MapLibre GL) */
(() => {
  "use strict";

  // ------------------------------------------------------------------ 상수
  const KINDS = [
    ["acc", "교통사고"],
    ["ete", "기타돌발"],
    ["dis", "재난"],
    ["wea", "기상"],
    ["cor", "공사"],
    ["etc", "기타"],
  ];
  const KIND_LABEL = Object.fromEntries(KINDS);
  const KIND_GLYPH = { acc: "사", ete: "돌", dis: "재", wea: "기", cor: "공", etc: "·" };
  const DEFAULT_PREFS = {
    kinds: ["acc", "ete", "dis"],
    roadTypes: ["ex", "its"],
    onlyWatched: false,
    watched: [],
    sound: true,
  };
  const KOREA_BOUNDS = [[124.6, 33.0], [130.0, 38.7]]; // [경도, 위도]
  const KOREA_CENTER = [127.8, 36.2];
  const ROAD_RENDER_LIMIT = 300;
  const CCTV_GROUP_OPEN_LIMIT = 30;
  const NEAREST_CCTV_KM = 5;
  const REFRESH_MS = 60_000;
  const CCTV_REFRESH_MS = 15 * 60_000;
  const HLS_JS = {
    src: "https://unpkg.com/hls.js@1.7.3/dist/hls.min.js",
    integrity: "sha384-cciJ0zi8d1uMKC2zJd7jvPY4HQt7W4ByUI/FlMkltvBi31aW61rcpVBhpmW8/NwX",
  };

  // ------------------------------------------------------------------ 상태
  const state = {
    roadType: "ex",
    view: "roads",
    side: "roads",
    sort: "route",
    search: "",
    watchedOnly: false,
    roads: { ex: null, its: null },
    roadsUpdated: {},
    roadsError: {},
    selectedKey: null,
    detail: null,
    events: [],
    eventsUpdated: null,
    feedKinds: new Set(),
    newIds: new Set(),
    alertHistory: [],
    unseenAlerts: 0,
    status: null,
    config: { style: null, styleDark: null, cctvProxy: true, cctvEnabled: true },
    prefs: loadPrefs(),
    push: { supported: false, enabled: false, publicKey: null, subscription: null },
    layers: { traffic: true, events: true, cctv: true },
    cctvGroups: null,
    cctvById: new Map(),
    cctvCountByRoad: new Map(),
    cctvUpdated: null,
    cctvError: null,
    cctvType: "all",
    cctvMode: "list",
    cctvSearch: "",
    openGroups: new Set(),
    nearestCache: new Map(),
  };

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const desktopMQ = window.matchMedia("(min-width: 1024px)");
  const darkMQ = window.matchMedia("(prefers-color-scheme: dark)");
  const isDesktop = () => desktopMQ.matches;

  const el = {
    layout: $("#layout"),
    title: $("#pageTitle"),
    back: $("#backBtn"),
    demoBadge: $("#demoBadge"),
    live: $("#liveStatus"),
    alertsBtn: $("#alertsBtn"),
    alertsBtnBadge: $("#alertsBtnBadge"),
    banner: $("#alertBanner"),
    roadList: $("#roadList"),
    roadSearch: $("#roadSearch"),
    roadsUpdated: $("#roadsUpdated"),
    watchedOnly: $("#watchedOnly"),
    mapPane: $("#mapPane"),
    detailPane: $("#detailPane"),
    detail: $("#roadDetail"),
    feedPane: $("#feedPane"),
    eventList: $("#eventList"),
    eventCount: $("#eventCount"),
    eventsUpdated: $("#eventsUpdated"),
    kindFilter: $("#kindFilter"),
    tabEventBadge: $("#tabEventBadge"),
    cctvPane: $("#cctvPane"),
    cctvList: $("#cctvList"),
    cctvSearch: $("#cctvSearch"),
    cctvSummary: $("#cctvSummary"),
    cctvMapSlot: $(".cctv-map-slot"),
    toasts: $("#toasts"),
    notifyBtn: $("#notifyBtn"),
    notifyStatus: $("#notifyStatus"),
    pushBtn: $("#pushBtn"),
    pushStatus: $("#pushStatus"),
    alertKinds: $("#alertKinds"),
    alertRoadTypes: $("#alertRoadTypes"),
    onlyWatched: $("#onlyWatched"),
    watchedList: $("#watchedList"),
    soundOn: $("#soundOn"),
    demoTools: $("#demoTools"),
    alertHistory: $("#alertHistory"),
    player: $("#player"),
    playerTitle: $("#playerTitle"),
    playerRoad: $("#playerRoad"),
    playerMedia: $("#playerMedia"),
    playerStatus: $("#playerStatus"),
    playerPrev: $("#playerPrev"),
    playerNext: $("#playerNext"),
    playerOpen: $("#playerOpen"),
    playerMap: $("#playerMap"),
  };

  // ------------------------------------------------------------------ 유틸
  function esc(value) {
    return String(value ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[c]);
  }

  function loadPrefs() {
    try {
      const saved = JSON.parse(localStorage.getItem("its.prefs") || "null");
      return saved ? { ...DEFAULT_PREFS, ...saved } : { ...DEFAULT_PREFS };
    } catch {
      return { ...DEFAULT_PREFS };
    }
  }

  function savePrefs() {
    try {
      localStorage.setItem("its.prefs", JSON.stringify(state.prefs));
    } catch { /* 저장 불가(사생활 보호 모드 등)여도 동작은 계속 */ }
    syncPushFilters();
  }

  function fmtTime(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "";
    return d.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" });
  }

  function fmtAgo(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "";
    const min = Math.round((Date.now() - d.getTime()) / 60000);
    if (min < 1) return "방금";
    if (min < 60) return `${min}분 전`;
    if (min < 60 * 24) return `${Math.floor(min / 60)}시간 ${min % 60 ? `${min % 60}분 ` : ""}전`;
    return d.toLocaleDateString("ko-KR", { month: "short", day: "numeric" });
  }

  function fmtDuration(seconds) {
    if (seconds == null) return "-";
    const s = Math.round(seconds);
    if (s < 60) return `${s}초`;
    return `${Math.floor(s / 60)}분 ${s % 60 ? `${s % 60}초` : ""}`.trim();
  }

  function fmtKm(km) {
    return km < 1 ? `${Math.round(km * 1000)}m` : `${km.toFixed(1)}km`;
  }

  function normalize(text) {
    return String(text || "").replace(/\s+/g, "").toLowerCase();
  }

  function distanceKm(lat1, lon1, lat2, lon2) {
    const rad = Math.PI / 180;
    const dLat = (lat2 - lat1) * rad;
    const dLon = (lon2 - lon1) * rad;
    const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(dLon / 2) ** 2;
    return 12742 * Math.asin(Math.sqrt(a));
  }

  async function api(path, options) {
    const res = await fetch(path, { headers: { Accept: "application/json" }, ...options });
    let body = null;
    try { body = await res.json(); } catch { /* 본문 없음 */ }
    if (!res.ok) throw new Error((body && body.error) || `HTTP ${res.status}`);
    return body;
  }

  function debounce(fn, ms) {
    let t;
    return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
  }

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function loadScript({ src, integrity }) {
    return new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = src;
      s.integrity = integrity;
      s.crossOrigin = "anonymous";
      s.onload = resolve;
      s.onerror = () => reject(new Error(`스크립트를 불러오지 못했습니다: ${src}`));
      document.head.appendChild(s);
    });
  }

  function shieldHTML(road) {
    const type = road.road_type === "ex" ? "ex" : "its";
    if (!road.route_no) {
      return `<span class="shield none" aria-hidden="true">${type === "ex" ? "고속" : "도로"}</span>`;
    }
    const label = `${type === "ex" ? "고속도로" : "국도"} ${road.route_no}호선`;
    return `<span class="shield ${type}" title="${esc(label)}">${esc(road.route_no)}</span>`;
  }

  function barHTML(share, cls = "bar") {
    if (!share) return `<span class="${cls}"></span>`;
    return `<span class="${cls}" aria-hidden="true">` +
      `<i class="smooth" style="width:${share.smooth}%"></i>` +
      `<i class="slow" style="width:${share.slow}%"></i>` +
      `<i class="jam" style="width:${share.jam}%"></i></span>`;
  }

  const PLAY_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 4v16l13-8z"/></svg>';

  // ------------------------------------------------------------------ 화면 전환
  const VIEW_TITLES = {
    map: "전국 지도",
    events: "돌발 상황",
    cctv: "CCTV",
    alerts: "알림",
  };

  function setView(view) {
    if (isDesktop()) {
      if (view === "alerts") {
        el.layout.dataset.overlay = "alerts";
      } else {
        el.layout.dataset.overlay = "";
        el.layout.dataset.view = view;
      }
      if (view === "cctv") state.side = "cctv";
      else if (view === "roads" || view === "detail") state.side = "roads";
    } else {
      el.layout.dataset.view = view;
      el.layout.dataset.overlay = "";
    }
    el.layout.dataset.side = state.side;
    $$(".side-switch button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.side === state.side)));
    state.view = view;

    const tab = view === "detail" ? "roads" : view;
    $$(".tabbar button").forEach((b) => {
      if (b.dataset.tab === tab) b.setAttribute("aria-current", "page");
      else b.removeAttribute("aria-current");
    });
    el.back.hidden = isDesktop() || view !== "detail";
    if (isDesktop()) el.title.textContent = "전국 도로 소통정보";
    else if (view === "detail") el.title.textContent = state.detail ? state.detail.name : "노선 상세";
    else el.title.textContent = VIEW_TITLES[view] || "전국 도로 소통정보";

    if (view === "alerts") {
      state.unseenAlerts = 0;
      updateBadges();
    }
    if ((view === "cctv" || view === "map") && !state.cctvGroups) loadCctv();
    placeMap();
  }

  function setCctvMode(mode) {
    state.cctvMode = mode;
    el.cctvPane.dataset.mode = mode;
    $$("[data-cctv-mode]").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.cctvMode === mode)));
    if (mode === "map") setLayer("cctv", true);
    placeMap();
  }

  function placeMap() {
    // 지도는 하나만 만들고, 화면에 따라 컨테이너를 옮긴다.
    let target = el.layout;
    if (!isDesktop()) {
      const detailSlot = $(".map-slot", el.detail);
      if (state.view === "detail" && detailSlot) target = detailSlot;
      else if (state.view === "cctv" && state.cctvMode === "map") target = el.cctvMapSlot;
    }
    if (el.mapPane.parentElement !== target) {
      if (target === el.layout) el.layout.insertBefore(el.mapPane, el.detailPane);
      else target.appendChild(el.mapPane);
    }
    setTimeout(flushMapAction, 60);
  }

  function setRoadType(type) {
    state.roadType = type;
    $$('[data-seg="roads"] button').forEach((b) => b.setAttribute("aria-selected", String(b.dataset.roadType === type)));
    renderRoads();
    if (!state.roads[type]) loadRoads(type);
  }

  function parseHash() {
    const raw = location.hash.replace(/^#/, "");
    if (!raw || raw === "roads") return { view: "roads" };
    if (raw.includes("=")) {
      const params = new URLSearchParams(raw);
      if (params.get("road")) return { view: "detail", road: params.get("road"), event: params.get("event") };
    }
    if (raw === "ex" || raw === "its") return { view: "roads", roadType: raw };
    if (["map", "events", "cctv", "alerts"].includes(raw)) return { view: raw };
    return { view: "roads" };
  }

  function applyRoute() {
    const route = parseHash();
    if (route.roadType) setRoadType(route.roadType);
    if (route.view === "detail") {
      const type = route.road.split(":")[0];
      if ((type === "ex" || type === "its") && type !== state.roadType) setRoadType(type);
      state.focusEventId = route.event || null;
      if (route.road !== state.selectedKey || !state.detail) selectRoad(route.road);
      else if (state.focusEventId) focusEvent(state.focusEventId);
    }
    setView(route.view);
  }

  function navigate(hash, { replace = false } = {}) {
    const target = `#${hash}`;
    if (location.hash === target) { applyRoute(); return; }
    if (replace) { history.replaceState(null, "", target); applyRoute(); }
    else location.hash = hash;
  }

  function roadHash(key, eventId) {
    const params = new URLSearchParams({ road: key });
    if (eventId) params.set("event", eventId);
    return params.toString();
  }

  // ------------------------------------------------------------------ 노선 목록
  async function loadRoads(type, { silent = false } = {}) {
    if (!silent && !state.roads[type]) renderRoads();
    try {
      const data = await api(`api/roads?type=${type}`);
      state.roads[type] = data.items;
      state.roadsUpdated[type] = data.updated_at;
      state.roadsError[type] = null;
    } catch (err) {
      state.roadsError[type] = err.message;
    }
    if (type === state.roadType) renderRoads();
    updateRoadLayer();
  }

  function sortedRoads(items) {
    const list = items.slice();
    if (state.sort === "jam") {
      list.sort((a, b) => (b.grade_share.jam + b.grade_share.slow / 2) - (a.grade_share.jam + a.grade_share.slow / 2)
        || (a.avg_speed ?? 999) - (b.avg_speed ?? 999));
    } else if (state.sort === "events") {
      list.sort((a, b) => b.urgent_count - a.urgent_count || b.event_count - a.event_count);
    }
    return list;
  }

  function filteredRoads() {
    const items = state.roads[state.roadType] || [];
    const q = normalize(state.search);
    return sortedRoads(items.filter((road) => {
      if (state.watchedOnly && !state.prefs.watched.includes(road.key)) return false;
      if (!q) return true;
      const typeLabel = road.road_type === "ex" ? "고속도로" : "국도";
      return normalize(road.name).includes(q) || road.route_no === q ||
        normalize(`${typeLabel}${road.route_no || ""}`).includes(q);
    }));
  }

  function roadItemHTML(road) {
    const watched = state.prefs.watched.includes(road.key);
    const speed = road.avg_speed != null
      ? `<b>${Math.round(road.avg_speed)}</b><small>km/h</small>`
      : `<small>정보없음</small>`;
    const events = road.event_count
      ? `<span class="ev-count ${road.urgent_count ? "urgent" : ""}" title="진행 중 돌발 ${road.event_count}건">⚠ ${road.event_count}</span>`
      : "";
    const cams = state.cctvCountByRoad.get(road.key);
    return `<li class="road" tabindex="0" data-key="${esc(road.key)}" aria-current="${road.key === state.selectedKey}">
      ${shieldHTML(road)}
      <div class="road-main">
        <div class="road-name">${esc(road.name)}</div>
        <div class="road-sub">${barHTML(road.measured_count ? road.grade_share : null)}
          <span>${road.measured_count ? `${road.measured_count}개 구간` : "소통정보 없음"}${cams ? ` · CCTV ${cams}` : ""}</span></div>
      </div>
      <div class="speed">${speed}<br><span class="grade ${road.grade}">${esc(road.grade_label)}</span></div>
      <div class="road-side">
        <button class="star" type="button" aria-pressed="${watched}" aria-label="관심 노선 ${watched ? "해제" : "등록"}" data-watch="${esc(road.key)}">${watched ? "★" : "☆"}</button>
        ${events}
      </div>
    </li>`;
  }

  function renderRoads() {
    const type = state.roadType;
    if (!state.roads[type]) {
      el.roadList.innerHTML = state.roadsError[type]
        ? `<li class="list-empty">${esc(state.roadsError[type])}<br><button class="btn" type="button" data-retry>다시 시도</button></li>`
        : '<li class="skeleton"></li>'.repeat(6);
      el.roadsUpdated.textContent = "";
      return;
    }
    const roads = filteredRoads();
    if (!roads.length) {
      el.roadList.innerHTML = `<li class="list-empty">${state.watchedOnly
        ? "관심 노선이 없습니다. 노선의 ☆를 눌러 등록하세요."
        : "검색 결과가 없습니다."}</li>`;
    } else {
      const extra = roads.length > ROAD_RENDER_LIMIT
        ? `<li class="list-empty">외 ${roads.length - ROAD_RENDER_LIMIT}개 노선 — 검색으로 찾아보세요</li>`
        : "";
      el.roadList.innerHTML = roads.slice(0, ROAD_RENDER_LIMIT).map(roadItemHTML).join("") + extra;
    }
    const updated = state.roadsUpdated[type];
    el.roadsUpdated.textContent = updated ? `소통정보 ${fmtTime(updated)} 기준 · 노선 ${roads.length}개` : "";
  }

  function toggleWatch(key) {
    const watched = new Set(state.prefs.watched);
    if (watched.has(key)) watched.delete(key); else watched.add(key);
    state.prefs.watched = Array.from(watched);
    savePrefs();
    renderRoads();
    renderDetail();
    renderAlertSettings();
  }

  // ------------------------------------------------------------------ 노선 상세
  async function selectRoad(key) {
    state.selectedKey = key;
    $$(".road", el.roadList).forEach((li) => li.setAttribute("aria-current", String(li.dataset.key === key)));
    if (!state.detail || state.detail.key !== key) {
      state.detail = null;
      el.detail.className = "";
      el.detail.innerHTML = '<div class="skeleton" style="margin:16px"></div>'.repeat(3);
    }
    await loadDetail(key);
  }

  async function loadDetail(key) {
    try {
      const detail = await api(`api/roads/${encodeURIComponent(key)}`);
      if (state.selectedKey !== key) return;
      state.detail = detail;
    } catch (err) {
      if (state.selectedKey !== key) return;
      state.detail = null;
      el.detail.className = "empty-state";
      el.detail.innerHTML = `<p>${esc(err.message)}</p>`;
      return;
    }
    renderDetail();
    drawSelectedRoad();
    if (state.focusEventId) focusEvent(state.focusEventId);
  }

  function camItemHTML(cam, extra = "") {
    return `<li class="cam" tabindex="0" data-cam="${esc(cam.id)}">
      <span class="play">${PLAY_ICON}</span>
      <div><div class="c-name">${esc(cam.location || cam.name)}</div>${extra}</div>
      <span class="c-dist">${cam.media === "image" ? "정지영상" : "실시간"}</span>
    </li>`;
  }

  function renderDetail() {
    const d = state.detail;
    if (!d) return;
    const watched = state.prefs.watched.includes(d.key);
    const typeLabel = d.road_type === "ex" ? "고속도로" : "국도";
    const avg = d.avg_speed != null ? `${Math.round(d.avg_speed)}<small> km/h</small>` : "-";
    const min = d.min_speed != null ? `${Math.round(d.min_speed)}<small> km/h</small>` : "-";
    const urgent = d.events.filter((e) => e.urgent).length;
    const cams = d.cctv || [];

    const directions = d.directions.length
      ? d.directions.map((dir) => `
        <div class="dir-card">
          <div class="dir-top">
            <strong>${esc(dir.direction)}</strong>
            <span><b>${dir.avg_speed != null ? Math.round(dir.avg_speed) : "-"}</b> km/h
              <span class="grade ${dir.grade}">${esc(dir.grade_label)}</span></span>
          </div>
          ${barHTML(dir.grade_share, "dir-bar")}
          <div class="dir-legend">
            <span>원활 ${dir.grade_share.smooth}%</span><span>서행 ${dir.grade_share.slow}%</span><span>정체 ${dir.grade_share.jam}%</span>
          </div>
          ${dir.slowest.length ? `<ul class="slowest" aria-label="가장 느린 구간">
            ${dir.slowest.map((s) => `<li><span>구간 ${esc(s.link_no || s.link_id)}</span>
              <span>통행 ${fmtDuration(s.travel_time)} · <span class="sp ${s.grade}">${Math.round(s.speed)} km/h</span></span></li>`).join("")}
          </ul>` : ""}
        </div>`).join("")
      : '<p class="muted" style="padding:0 16px 12px">이 노선의 소통정보가 없습니다.</p>';

    el.detail.className = "";
    el.detail.innerHTML = `
      <div class="detail-head">
        ${shieldHTML(d)}
        <div>
          <h2>${esc(d.name)}</h2>
          <p class="muted">${typeLabel}${d.updated_at ? ` · ${fmtTime(d.updated_at)} 기준` : ""}</p>
        </div>
        <button class="watch-btn" type="button" data-watch="${esc(d.key)}" aria-pressed="${watched}">${watched ? "★ 알림 받는 중" : "☆ 관심 노선"}</button>
      </div>
      <div class="detail-stats">
        <div class="stat"><div class="k">평균 속도</div><div class="v">${avg}</div></div>
        <div class="stat"><div class="k">최저 속도</div><div class="v">${min}</div></div>
        <div class="stat"><div class="k">돌발</div><div class="v" style="color:${urgent ? "var(--k-acc)" : "inherit"}">${d.events.length}<small> 건</small></div></div>
      </div>
      <div class="map-slot"></div>
      <div class="section-title">진행 중인 돌발 (${d.events.length})</div>
      <ul class="event-list">${d.events.length ? d.events.map((e) => eventHTML(e)).join("") : '<li class="list-empty">진행 중인 돌발이 없습니다.</li>'}</ul>
      <div class="section-title">CCTV (${cams.length})</div>
      ${cams.length
        ? `<div class="card" style="padding:4px"><ul class="cams" data-cam-context="detail">${cams.map((c) => camItemHTML(c)).join("")}</ul></div>`
        : '<p class="muted" style="padding:0 16px 12px">이 노선의 CCTV가 없습니다.</p>'}
      <div class="section-title">방향별 소통</div>
      ${directions}`;
    if (!isDesktop() && state.view === "detail") el.title.textContent = d.name;
    placeMap();
  }

  // ------------------------------------------------------------------ 돌발
  async function loadEvents() {
    try {
      const data = await api("api/events");
      state.events = data.items;
      state.eventsUpdated = data.updated_at;
    } catch (err) {
      el.eventsUpdated.textContent = `불러오기 실패: ${err.message}`;
      return;
    }
    renderEvents();
    renderMarkers();
    updateBadges();
  }

  function nearestCamera(e) {
    if (e.lat == null || e.lon == null || !state.cctvById.size) return null;
    if (state.nearestCache.has(e.id)) return state.nearestCache.get(e.id);
    let best = null;
    for (const cam of state.cctvById.values()) {
      if (cam.lat == null || cam.lon == null) continue;
      const km = distanceKm(e.lat, e.lon, cam.lat, cam.lon);
      if (km <= NEAREST_CCTV_KM && (!best || km < best.km)) best = { cam, km };
    }
    state.nearestCache.set(e.id, best);
    return best;
  }

  function eventHTML(e, { compact = false } = {}) {
    const title = `${esc(e.road_name)}<small>${esc(e.direction_label || "")}</small>`;
    const near = compact ? null : nearestCamera(e);
    const meta = [
      e.started_at ? `${fmtAgo(e.started_at)} 발생` : "",
      e.lanes_blocked ? `${esc(e.lanes_blocked)}차로 차단` : "",
      !compact && e.ends_at ? `종료 예정 ${fmtTime(e.ends_at)}` : "",
    ].filter(Boolean).map((m) => `<span>${m}</span>`).join("");
    const camBtn = near
      ? `<button class="cam-btn" type="button" data-play="${esc(near.cam.id)}" title="${esc(near.cam.name)}">▶ CCTV ${fmtKm(near.km)}</button>`
      : "";
    return `<li class="event kind-${e.kind} ${state.newIds.has(e.id) ? "is-new" : ""}" data-event="${esc(e.id)}" data-road="${esc(e.road_key)}" tabindex="0">
      <span class="event-kind">${esc(e.kind_label)}</span>
      <div class="event-title">${title}</div>
      <div class="event-msg">${esc(e.message || e.detail)}</div>
      <div class="event-meta">${meta}${camBtn}</div>
    </li>`;
  }

  function visibleEvents() {
    if (!state.feedKinds.size) return state.events;
    return state.events.filter((e) => state.feedKinds.has(e.kind));
  }

  function renderEvents() {
    const counts = {};
    state.events.forEach((e) => { counts[e.kind] = (counts[e.kind] || 0) + 1; });
    el.kindFilter.innerHTML =
      `<button type="button" data-kind="" aria-pressed="${!state.feedKinds.size}">전체<span class="n">${state.events.length}</span></button>` +
      KINDS.filter(([k]) => counts[k]).map(([k, label]) =>
        `<button type="button" data-kind="${k}" aria-pressed="${state.feedKinds.has(k)}">${label}<span class="n">${counts[k]}</span></button>`).join("");

    const events = visibleEvents().slice().sort((a, b) => (b.urgent - a.urgent) || String(b.started_at).localeCompare(String(a.started_at)));
    el.eventCount.textContent = events.length ? `${events.length}건` : "";
    el.eventList.innerHTML = events.length
      ? events.map((e) => eventHTML(e)).join("")
      : '<li class="list-empty">진행 중인 돌발이 없습니다.</li>';
    el.eventsUpdated.textContent = state.eventsUpdated ? `${fmtTime(state.eventsUpdated)} 기준` : "";
  }

  function updateBadges() {
    const urgent = state.events.filter((e) => e.urgent).length;
    el.tabEventBadge.hidden = !urgent;
    el.tabEventBadge.textContent = urgent > 99 ? "99+" : String(urgent);
    el.alertsBtnBadge.hidden = !state.unseenAlerts;
  }

  // ------------------------------------------------------------------ CCTV 목록
  async function loadCctv() {
    if (!state.config.cctvEnabled) return;
    try {
      const data = await api("api/cctv?type=all");
      state.cctvGroups = data.groups;
      state.cctvUpdated = data.updated_at;
      state.cctvError = null;
      state.cctvById = new Map();
      state.cctvCountByRoad = new Map();
      data.groups.forEach((g) => {
        state.cctvCountByRoad.set(g.key, g.count);
        g.items.forEach((cam) => state.cctvById.set(cam.id, cam));
      });
      state.nearestCache.clear();
    } catch (err) {
      state.cctvError = err.message;
    }
    renderCctv();
    renderRoads();
    renderEvents();
    updateCctvLayer();
  }

  function filteredCctvGroups() {
    if (!state.cctvGroups) return [];
    const q = normalize(state.cctvSearch);
    const groups = [];
    for (const g of state.cctvGroups) {
      if (state.cctvType !== "all" && g.road_type !== state.cctvType) continue;
      if (!q) { groups.push({ group: g, items: g.items }); continue; }
      const roadHit = normalize(g.name).includes(q) || g.route_no === q;
      const items = roadHit ? g.items : g.items.filter((c) => normalize(c.name).includes(q));
      if (items.length) groups.push({ group: g, items });
    }
    return groups;
  }

  function renderCctv() {
    if (!state.config.cctvEnabled) {
      el.cctvList.innerHTML = '<li class="list-empty">이 서버는 CCTV를 제공하지 않습니다.</li>';
      return;
    }
    if (!state.cctvGroups) {
      el.cctvList.innerHTML = state.cctvError
        ? `<li class="list-empty">${esc(state.cctvError)}<br><button class="btn" type="button" data-cctv-retry>다시 시도</button></li>`
        : '<li class="skeleton"></li>'.repeat(6);
      return;
    }
    const groups = filteredCctvGroups();
    const searching = !!state.cctvSearch.trim();
    const total = groups.reduce((n, g) => n + g.items.length, 0);
    el.cctvSummary.textContent = `CCTV ${total.toLocaleString()}대 · 노선 ${groups.length}개` +
      (state.cctvUpdated ? ` · ${fmtTime(state.cctvUpdated)} 기준` : "");
    if (!groups.length) {
      el.cctvList.innerHTML = '<li class="list-empty">검색 결과가 없습니다.</li>';
      return;
    }
    el.cctvList.innerHTML = groups.map(({ group, items }, index) => {
      const open = state.openGroups.has(group.key) || (searching && index < CCTV_GROUP_OPEN_LIMIT);
      return `<li class="cctv-group ${open ? "open" : ""}" data-group="${esc(group.key)}">
        <div class="cctv-group-head" role="button" tabindex="0" aria-expanded="${open}" data-toggle-group="${esc(group.key)}">
          ${shieldHTML(group)}
          <span class="g-name">${esc(group.name)}</span>
          <button class="g-map" type="button" data-group-map="${esc(group.key)}" title="지도에서 이 노선 CCTV 보기">지도</button>
          <span class="g-count">${items.length}대
            <svg class="chev" viewBox="0 0 24 24" aria-hidden="true"><path d="m9 6 6 6-6 6"/></svg></span>
        </div>
        ${open ? `<ul class="cams" data-cam-context="group:${esc(group.key)}">${items.map((c) => camItemHTML(c)).join("")}</ul>` : ""}
      </li>`;
    }).join("");
  }

  function camerasForContext(context, id) {
    if (context === "detail" && state.detail) return state.detail.cctv || [];
    if (context && context.startsWith("group:")) {
      const key = context.slice(6);
      const found = filteredCctvGroups().find((g) => g.group.key === key);
      if (found) return found.items;
    }
    const cam = state.cctvById.get(id);
    if (!cam) return [];
    const group = (state.cctvGroups || []).find((g) => g.key === cam.road_key);
    return group ? group.items : [cam];
  }

  function showGroupOnMap(key) {
    const group = (state.cctvGroups || []).find((g) => g.key === key);
    if (!group) return;
    setLayer("cctv", true);
    if (!isDesktop()) setCctvMode("map");
    const pts = group.items.filter((c) => c.lat != null).map((c) => [c.lon, c.lat]);
    fitPoints(pts, 13);
  }

  // ------------------------------------------------------------------ 지도 (MapLibre GL)
  let map = null;
  let mapReady = false;
  let styleLoadedOnce = false;
  let usedFallbackStyle = false;
  let pendingMapAction = null;
  let lastFitKey = null;
  let popup = null;
  const eventMarkers = new Map();

  function fallbackStyle(dark) {
    // 벡터 스타일을 못 불러오면 OSM 래스터 타일로 대체
    return {
      version: 8,
      glyphs: "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf",
      sources: {
        osm: {
          type: "raster",
          tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
          tileSize: 256,
          maxzoom: 19,
          attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        },
      },
      layers: [
        { id: "bg", type: "background", paint: { "background-color": dark ? "#0f172a" : "#e5e7eb" } },
        { id: "osm", type: "raster", source: "osm", paint: dark ? { "raster-brightness-max": 0.45, "raster-saturation": -0.6 } : {} },
      ],
    };
  }

  function mapVisible() {
    const c = map && map.getContainer();
    return !!c && c.offsetWidth > 0 && c.offsetHeight > 0;
  }

  // 숨겨진(크기 0) 지도에서 fitBounds/flyTo를 하면 좌표가 틀어지므로 보일 때까지 미룬다.
  function withMap(action) {
    if (!map) return;
    if (mapVisible()) { map.resize(); action(); }
    else pendingMapAction = action;
  }

  function flushMapAction() {
    if (!map || !mapVisible()) return;
    map.resize();
    if (pendingMapAction) { const action = pendingMapAction; pendingMapAction = null; action(); }
  }

  function fitPoints(points, maxZoom = 12) {
    if (!points.length) return;
    withMap(() => {
      if (points.length === 1) {
        map.flyTo({ center: points[0], zoom: Math.max(map.getZoom(), maxZoom), duration: 600 });
        return;
      }
      const bounds = points.reduce((b, p) => b.extend(p), new maplibregl.LngLatBounds(points[0], points[0]));
      map.fitBounds(bounds, { padding: 48, maxZoom, duration: 600 });
    });
  }

  function initMap() {
    const container = $("#map");
    if (!window.maplibregl) {
      container.innerHTML = '<div class="map-fallback">지도를 불러오지 못했습니다. 네트워크 연결을 확인하세요.</div>';
      return;
    }
    const dark = darkMQ.matches;
    const style = (dark ? state.config.styleDark : state.config.style) || fallbackStyle(dark);
    map = new maplibregl.Map({
      container,
      style,
      center: KOREA_CENTER,
      zoom: 5.6,
      minZoom: 4,
      maxZoom: 18,
      attributionControl: { compact: true },
      dragRotate: false,
      pitchWithRotate: false,
    });
    map.touchZoomRotate.disableRotation();
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.addControl(new maplibregl.GeolocateControl({ positionOptions: { enableHighAccuracy: false } }), "top-right");
    map.on("style.load", () => { styleLoadedOnce = true; addOverlays(); });
    map.on("error", (ev) => {
      // 스타일(벡터 타일) 서버에 접근하지 못하면 한 번만 래스터 지도로 바꾼다
      if (!styleLoadedOnce && !usedFallbackStyle) {
        usedFallbackStyle = true;
        console.warn("지도 스타일을 불러오지 못해 기본 지도로 전환합니다", ev && ev.error);
        map.setStyle(fallbackStyle(dark));
      }
    });
    popup = new maplibregl.Popup({ closeButton: true, maxWidth: "300px", offset: 14 });
    bindMapEvents();
    withMap(() => map.fitBounds(KOREA_BOUNDS, { padding: 20, duration: 0 }));
  }

  function addOverlays() {
    const camColor = cssVar("--cam") || "#4f46e5";
    const casing = darkMQ.matches ? "#000000" : "#0f172a";
    if (!map.getSource("roads")) map.addSource("roads", { type: "geojson", data: emptyFC() });
    if (!map.getSource("selected")) map.addSource("selected", { type: "geojson", data: emptyFC() });
    if (!map.getSource("cctv")) {
      map.addSource("cctv", { type: "geojson", data: emptyFC(), cluster: true, clusterRadius: 44, clusterMaxZoom: 11 });
    }
    const addLayer = (layer) => { if (!map.getLayer(layer.id)) map.addLayer(layer); };
    const lineLayout = { "line-join": "round", "line-cap": "round" };
    addLayer({
      id: "roads-casing", type: "line", source: "roads", layout: lineLayout,
      paint: { "line-color": casing, "line-opacity": 0.18, "line-width": ["interpolate", ["linear"], ["zoom"], 5, 5, 12, 11] },
    });
    addLayer({
      id: "roads-line", type: "line", source: "roads", layout: lineLayout,
      paint: { "line-color": ["get", "color"], "line-opacity": 0.92, "line-width": ["interpolate", ["linear"], ["zoom"], 5, 2.5, 12, 7] },
    });
    addLayer({
      id: "selected-casing", type: "line", source: "selected", layout: lineLayout,
      paint: { "line-color": cssVar("--accent") || "#2563eb", "line-width": ["interpolate", ["linear"], ["zoom"], 5, 9, 12, 16], "line-opacity": 0.35 },
    });
    addLayer({
      id: "selected-line", type: "line", source: "selected", layout: lineLayout,
      paint: { "line-color": ["get", "color"], "line-width": ["interpolate", ["linear"], ["zoom"], 5, 5, 12, 10] },
    });
    addLayer({
      id: "cctv-clusters", type: "circle", source: "cctv", filter: ["has", "point_count"],
      paint: {
        "circle-color": camColor, "circle-opacity": 0.88,
        "circle-radius": ["step", ["get", "point_count"], 13, 20, 17, 100, 22, 500, 28],
        "circle-stroke-color": "#ffffff", "circle-stroke-width": 2,
      },
    });
    addLayer({
      id: "cctv-count", type: "symbol", source: "cctv", filter: ["has", "point_count"],
      layout: { "text-field": ["get", "point_count_abbreviated"], "text-font": ["Noto Sans Bold"], "text-size": 12, "text-allow-overlap": true },
      paint: { "text-color": "#ffffff" },
    });
    addLayer({
      id: "cctv-points", type: "circle", source: "cctv", filter: ["!", ["has", "point_count"]],
      paint: {
        "circle-color": camColor,
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 6, 4.5, 12, 7, 16, 9],
        "circle-stroke-color": "#ffffff", "circle-stroke-width": 2,
      },
    });
    mapReady = true;
    updateRoadLayer();
    drawSelectedRoad();
    updateCctvLayer();
    applyLayerVisibility();
  }

  function emptyFC() {
    return { type: "FeatureCollection", features: [] };
  }

  function gradeColor(grade) {
    return cssVar(`--${["smooth", "slow", "jam"].includes(grade) ? grade : "unknown"}`) || "#94a3b8";
  }

  function roadFeature(road) {
    return {
      type: "Feature",
      geometry: { type: "LineString", coordinates: road.path.map(([lat, lon]) => [lon, lat]) },
      properties: {
        key: road.key,
        name: road.name,
        grade: road.grade,
        color: gradeColor(road.grade),
        speed: road.avg_speed,
        grade_label: road.grade_label,
        events: road.event_count,
        road_type: road.road_type,
      },
    };
  }

  function updateRoadLayer() {
    if (!mapReady) return;
    const roads = [...(state.roads.ex || []), ...(state.roads.its || [])].filter((r) => r.path && r.path.length > 1);
    map.getSource("roads").setData({ type: "FeatureCollection", features: roads.map(roadFeature) });
  }

  function updateCctvLayer() {
    if (!mapReady) return;
    const features = [];
    for (const cam of state.cctvById.values()) {
      if (cam.lat == null || cam.lon == null) continue;
      features.push({
        type: "Feature",
        geometry: { type: "Point", coordinates: [cam.lon, cam.lat] },
        properties: { id: cam.id },
      });
    }
    map.getSource("cctv").setData({ type: "FeatureCollection", features });
  }

  function drawSelectedRoad({ fit = true } = {}) {
    if (!mapReady) return;
    const d = state.detail;
    const source = map.getSource("selected");
    if (!d || !d.path || d.path.length < 2) {
      source.setData(emptyFC());
    } else {
      source.setData({ type: "FeatureCollection", features: [roadFeature(d)] });
    }
    if (!d || !fit) return;
    // 주기적 새로고침 때 사용자가 옮긴 지도를 되돌리지 않도록 노선이 바뀔 때만 맞춘다
    if (lastFitKey !== d.key && !state.focusEventId) {
      if (popup) popup.remove();
      const pts = (d.path || []).map(([lat, lon]) => [lon, lat])
        .concat(d.events.filter((e) => e.lat != null).map((e) => [e.lon, e.lat]));
      fitPoints(pts, 12);
    }
    lastFitKey = d.key;
  }

  function setLayer(name, on) {
    state.layers[name] = on;
    $$(`[data-layer="${name}"]`).forEach((b) => b.setAttribute("aria-pressed", String(on)));
    applyLayerVisibility();
  }

  function applyLayerVisibility() {
    const vis = (on) => (on ? "visible" : "none");
    if (mapReady) {
      ["roads-casing", "roads-line"].forEach((id) => map.setLayoutProperty(id, "visibility", vis(state.layers.traffic)));
      ["cctv-clusters", "cctv-count", "cctv-points"].forEach((id) => map.setLayoutProperty(id, "visibility", vis(state.layers.cctv)));
    }
    eventMarkers.forEach(({ marker }) => { marker.getElement().style.display = state.layers.events ? "" : "none"; });
  }

  function renderMarkers() {
    if (!map) return;
    eventMarkers.forEach(({ marker }) => marker.remove());
    eventMarkers.clear();
    visibleEvents().forEach((e) => {
      if (e.lat == null || e.lon == null) return;
      const node = document.createElement("div");
      node.className = `mk kind-${e.kind} ${e.urgent ? "urgent" : ""}`;
      node.textContent = KIND_GLYPH[e.kind] || "·";
      node.title = `${e.kind_label} ${e.road_name}`;
      node.addEventListener("click", (ev) => { ev.stopPropagation(); openEventPopup(e); });
      const marker = new maplibregl.Marker({ element: node }).setLngLat([e.lon, e.lat]).addTo(map);
      if (e.urgent) node.style.zIndex = "2";
      if (!state.layers.events) node.style.display = "none";
      eventMarkers.set(e.id, { marker, event: e });
    });
  }

  function openEventPopup(e) {
    const near = nearestCamera(e);
    popup.setLngLat([e.lon, e.lat]).setHTML(`
      <div class="popup-title">[${esc(e.kind_label)}] ${esc(e.road_name)} ${esc(e.direction_label || "")}</div>
      <div>${esc(e.message || e.detail)}</div>
      <div class="muted">${e.started_at ? `${fmtAgo(e.started_at)} 발생` : ""}${e.lanes_blocked ? ` · ${esc(e.lanes_blocked)}차로 차단` : ""}</div>
      <div class="popup-actions">
        <a href="#${esc(roadHash(e.road_key, e.id))}">노선 보기</a>
        ${near ? `<button class="cam-btn" type="button" data-play="${esc(near.cam.id)}">▶ 가까운 CCTV ${fmtKm(near.km)}</button>` : ""}
      </div>`).addTo(map);
  }

  function openCameraPopup(cam) {
    popup.setLngLat([cam.lon, cam.lat]).setHTML(`
      <div class="popup-title">${esc(cam.location || cam.name)}</div>
      <div class="muted">${esc(cam.road_name)}</div>
      <div class="popup-actions">
        <button class="cam-btn" type="button" data-play="${esc(cam.id)}">▶ 영상 보기</button>
        ${cam.road_name !== "노선 미분류" ? `<a href="#${esc(roadHash(cam.road_key))}">노선 보기</a>` : ""}
      </div>`).addTo(map);
  }

  function bindMapEvents() {
    map.on("click", "roads-line", (ev) => {
      const p = ev.features[0].properties;
      popup.setLngLat(ev.lngLat).setHTML(`
        <div class="popup-title">${esc(p.name)}</div>
        <div>${p.speed != null && p.speed !== "null" ? `평균 ${Math.round(p.speed)} km/h · ` : ""}<span class="grade ${esc(p.grade)}">${esc(p.grade_label)}</span></div>
        ${Number(p.events) ? `<div class="muted">진행 중 돌발 ${esc(p.events)}건</div>` : ""}
        <div class="popup-actions"><a href="#${esc(roadHash(p.key))}">상세 보기</a></div>`).addTo(map);
    });
    map.on("click", "cctv-clusters", async (ev) => {
      const feature = ev.features[0];
      try {
        const zoom = await map.getSource("cctv").getClusterExpansionZoom(feature.properties.cluster_id);
        map.easeTo({ center: feature.geometry.coordinates, zoom: zoom + 0.5 });
      } catch { /* 무시 */ }
    });
    map.on("click", "cctv-points", (ev) => {
      const cam = state.cctvById.get(ev.features[0].properties.id);
      if (cam) openCameraPopup(cam);
    });
    ["roads-line", "cctv-clusters", "cctv-points"].forEach((id) => {
      map.on("mouseenter", id, () => { map.getCanvas().style.cursor = "pointer"; });
      map.on("mouseleave", id, () => { map.getCanvas().style.cursor = ""; });
    });
  }

  function focusEvent(id) {
    state.focusEventId = null;
    const event = state.events.find((e) => e.id === id) || (state.detail && state.detail.events.find((e) => e.id === id));
    $$(`.event[data-event="${CSS.escape(id)}"]`).forEach((li) => {
      li.classList.remove("is-new"); void li.offsetWidth; li.classList.add("is-new");
    });
    if (!map || !event || event.lat == null) return;
    setLayer("events", true);
    withMap(() => {
      map.flyTo({ center: [event.lon, event.lat], zoom: Math.max(map.getZoom(), 11), duration: 700 });
      openEventPopup(event);
    });
  }

  function focusCamera(id) {
    const cam = state.cctvById.get(id) || (state.detail && (state.detail.cctv || []).find((c) => c.id === id));
    if (!map || !cam || cam.lat == null) return;
    setLayer("cctv", true);
    withMap(() => {
      map.flyTo({ center: [cam.lon, cam.lat], zoom: Math.max(map.getZoom(), 13), duration: 700 });
      openCameraPopup(cam);
    });
  }

  // ------------------------------------------------------------------ CCTV 플레이어
  const player = { list: [], index: 0, hls: null, timer: null, token: 0 };

  function proxyUrl(url) {
    return `api/cctv/proxy?u=${encodeURIComponent(new URL(url, location.href).href)}`;
  }

  function openPlayer(id, list) {
    const cams = list && list.length ? list : camerasForContext(null, id);
    const index = Math.max(0, cams.findIndex((c) => c.id === id));
    if (!cams.length) return;
    player.list = cams;
    player.index = index;
    if (!el.player.open) el.player.showModal();
    playCurrent();
  }

  function stopPlayback() {
    player.token += 1;
    clearInterval(player.timer);
    player.timer = null;
    if (player.hls) { player.hls.destroy(); player.hls = null; }
    $$("video", el.playerMedia).forEach((v) => { v.pause(); v.removeAttribute("src"); v.load(); });
    el.playerMedia.innerHTML = "";
  }

  function setPlayerStatus(text, error = false) {
    el.playerStatus.textContent = text;
    el.playerStatus.classList.toggle("error", error);
  }

  async function ensureHls() {
    if (!window.Hls) await loadScript(HLS_JS);
    return window.Hls;
  }

  function playCurrent() {
    stopPlayback();
    const token = player.token;
    const cam = player.list[player.index];
    el.playerTitle.textContent = cam.location || cam.name;
    el.playerRoad.textContent = `${cam.road_name} · ${player.index + 1}/${player.list.length}`;
    el.playerPrev.disabled = player.index === 0;
    el.playerNext.disabled = player.index >= player.list.length - 1;
    el.playerOpen.href = new URL(cam.url, location.href).href;

    if (cam.media === "image") {
      const img = document.createElement("img");
      img.alt = cam.name;
      const refresh = () => {
        const url = new URL(cam.url, location.href);
        url.searchParams.set("_t", Date.now());
        img.src = url.href;
      };
      img.onload = () => { if (token === player.token) setPlayerStatus(`정지영상 · ${new Date().toLocaleTimeString("ko-KR")} 갱신 (5초마다)`); };
      img.onerror = () => { if (token === player.token) setPlayerStatus("영상을 불러오지 못했습니다.", true); };
      el.playerMedia.appendChild(img);
      refresh();
      player.timer = setInterval(refresh, 5000);
      return;
    }

    const video = document.createElement("video");
    Object.assign(video, { controls: true, autoplay: true, muted: true, playsInline: true });
    video.setAttribute("playsinline", "");
    el.playerMedia.appendChild(video);
    video.addEventListener("playing", () => { if (token === player.token) setPlayerStatus("실시간 영상"); });

    const direct = new URL(cam.url, location.href).href;
    const mixed = location.protocol === "https:" && direct.startsWith("http:");
    const canProxy = state.config.cctvProxy && /^https?:/.test(direct) && new URL(direct).origin !== location.origin;
    const attempts = mixed && canProxy ? [proxyUrl(direct)] : canProxy ? [direct, proxyUrl(direct)] : [direct];

    const fail = () => {
      if (token !== player.token) return;
      setPlayerStatus("영상을 재생할 수 없습니다. 영상 주소가 만료되었을 수 있으니 잠시 후 다시 시도하거나 '새 창'으로 열어 보세요.", true);
    };

    const tryNext = (i) => {
      if (token !== player.token) return;
      if (i >= attempts.length) { fail(); return; }
      const src = attempts[i];
      setPlayerStatus(i === 0 ? "영상 연결 중…" : "서버를 거쳐 다시 연결하는 중…");
      if (cam.media === "video") {
        video.onerror = () => tryNext(i + 1);
        video.src = src;
        return;
      }
      if (video.canPlayType("application/vnd.apple.mpegurl")) {
        video.onerror = () => tryNext(i + 1);
        video.src = src;
        return;
      }
      ensureHls().then((Hls) => {
        if (token !== player.token) return;
        if (!Hls || !Hls.isSupported()) { setPlayerStatus("이 브라우저는 실시간 영상(HLS)을 지원하지 않습니다.", true); return; }
        if (player.hls) player.hls.destroy();
        const hls = new Hls({ liveDurationInfinity: true, manifestLoadingMaxRetry: 1 });
        player.hls = hls;
        hls.on(Hls.Events.ERROR, (_, data) => {
          if (data.fatal) { hls.destroy(); if (player.hls === hls) player.hls = null; tryNext(i + 1); }
        });
        hls.loadSource(src);
        hls.attachMedia(video);
      }).catch(() => fail());
    };
    tryNext(0);
  }

  function closePlayer() {
    stopPlayback();
    if (el.player.open) el.player.close();
  }

  function stepPlayer(delta) {
    const next = player.index + delta;
    if (next < 0 || next >= player.list.length) return;
    player.index = next;
    playCurrent();
  }

  function showPlayerOnMap() {
    const cam = player.list[player.index];
    closePlayer();
    if (!cam) return;
    if (!isDesktop()) {
      if (state.view === "cctv") setCctvMode("map");
      else if (state.view !== "detail") navigate("map");
    }
    focusCamera(cam.id);
  }

  // ------------------------------------------------------------------ 실시간 알림
  let source = null;
  let fallbackTimer = null;

  function setLive(stateName, text) {
    el.live.dataset.state = stateName;
    $("span", el.live).textContent = text;
  }

  function connectStream() {
    if (!window.EventSource) { startFallbackPolling(); return; }
    source = new EventSource("api/stream");
    source.addEventListener("hello", () => {
      setLive("open", "실시간");
      stopFallbackPolling();
    });
    source.addEventListener("incident", (msg) => {
      try { handleIncident(JSON.parse(msg.data).event); } catch { /* 무시 */ }
    });
    source.addEventListener("update", debounce(() => {
      loadEvents();
      refreshTraffic();
    }, 300));
    source.onerror = () => {
      setLive("error", "재연결 중");
      startFallbackPolling();
    };
  }

  function startFallbackPolling() {
    if (fallbackTimer) return;
    fallbackTimer = setInterval(() => { loadEvents(); refreshTraffic(); }, REFRESH_MS);
  }

  function stopFallbackPolling() {
    clearInterval(fallbackTimer);
    fallbackTimer = null;
  }

  const refreshTraffic = debounce(() => {
    loadRoads("ex", { silent: true });
    loadRoads("its", { silent: true });
    if (state.selectedKey) loadDetail(state.selectedKey);
  }, 1000);

  function matchesPrefs(e) {
    const p = state.prefs;
    if (!p.kinds.includes(e.kind)) return false;
    if (!p.roadTypes.includes(e.road_type)) return false;
    if (p.onlyWatched && !p.watched.includes(e.road_key)) return false;
    return true;
  }

  function handleIncident(e) {
    state.newIds.add(e.id);
    state.alertHistory = [e, ...state.alertHistory.filter((a) => a.id !== e.id)].slice(0, 50);
    renderAlertHistory();
    if (!matchesPrefs(e)) return;
    if (state.view !== "alerts") state.unseenAlerts += 1;
    updateBadges();
    showToast(e);
    showBanner(e);
    if (state.prefs.sound) { beep(e.urgent); navigator.vibrate?.(e.urgent ? [250, 120, 250] : 150); }
    if (document.visibilityState !== "visible" || isDesktop()) systemNotify(e);
  }

  function notificationContent(e) {
    return {
      title: `[${e.kind_label}] ${e.road_name} ${e.direction_label || ""}`.trim(),
      body: e.message || e.detail || "돌발상황이 발생했습니다.",
      tag: `its-${e.id}`,
      url: `./#${roadHash(e.road_key, e.id)}`,
    };
  }

  async function systemNotify(e) {
    if (!("Notification" in window) || Notification.permission !== "granted") return;
    const n = notificationContent(e);
    const options = { body: n.body, tag: n.tag, icon: "static/icon-192.png", badge: "static/icon-192.png", data: { url: n.url } };
    try {
      const reg = await navigator.serviceWorker?.getRegistration();
      if (reg) { await reg.showNotification(n.title, options); return; }
      new Notification(n.title, options).onclick = () => { window.focus(); navigate(roadHash(e.road_key, e.id)); };
    } catch { /* 알림 표시 실패는 무시 (토스트로 이미 표시됨) */ }
  }

  function showToast(e) {
    const n = notificationContent(e);
    const toast = document.createElement("div");
    toast.className = `toast kind-${e.kind}`;
    toast.setAttribute("role", "alert");
    toast.innerHTML = `<span class="event-kind">${esc(e.kind_label)}</span>
      <div><div class="t-title">${esc(`${e.road_name} ${e.direction_label || ""}`)}</div><div class="t-body">${esc(n.body)}</div></div>
      <button class="t-close" type="button" aria-label="닫기">×</button>`;
    toast.addEventListener("click", (ev) => {
      toast.remove();
      if (!ev.target.closest(".t-close")) navigate(roadHash(e.road_key, e.id));
    });
    el.toasts.prepend(toast);
    while (el.toasts.children.length > 3) el.toasts.lastElementChild.remove();
    setTimeout(() => toast.remove(), e.urgent ? 15000 : 8000);
  }

  function showInfo(text) {
    const toast = document.createElement("div");
    toast.className = "toast info";
    toast.innerHTML = `<span></span><div class="t-body">${esc(text)}</div><button class="t-close" type="button" aria-label="닫기">×</button>`;
    toast.addEventListener("click", () => toast.remove());
    el.toasts.prepend(toast);
    setTimeout(() => toast.remove(), 5000);
  }

  function showBanner(e) {
    if (!e.urgent) return;
    el.banner.hidden = false;
    el.banner.style.background = `var(--k-${e.kind})`;
    el.banner.innerHTML = `<span>⚠</span><span class="ab-text">${esc(notificationContent(e).title)} — ${esc(e.message)}</span><span class="ab-close" aria-label="닫기">×</span>`;
    el.banner.onclick = (ev) => {
      el.banner.hidden = true;
      if (!ev.target.closest(".ab-close")) navigate(roadHash(e.road_key, e.id));
    };
    clearTimeout(showBanner.timer);
    showBanner.timer = setTimeout(() => { el.banner.hidden = true; }, 20000);
  }

  let audioCtx = null;
  function unlockAudio() {
    if (audioCtx || !(window.AudioContext || window.webkitAudioContext)) return;
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  }

  function beep(urgent) {
    if (!audioCtx) return;
    audioCtx.resume?.();
    const tones = urgent ? [880, 660, 880] : [740];
    tones.forEach((freq, i) => {
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      const t = audioCtx.currentTime + i * 0.18;
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0.0001, t);
      gain.gain.exponentialRampToValueAtTime(0.25, t + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.15);
      osc.connect(gain).connect(audioCtx.destination);
      osc.start(t);
      osc.stop(t + 0.16);
    });
  }

  // ------------------------------------------------------------------ 알림 설정
  function renderAlertSettings() {
    const p = state.prefs;
    el.alertKinds.innerHTML = KINDS.map(([k, label]) =>
      `<button type="button" data-alert-kind="${k}" aria-pressed="${p.kinds.includes(k)}">${label}</button>`).join("");
    $$("button", el.alertRoadTypes).forEach((b) => b.setAttribute("aria-pressed", String(p.roadTypes.includes(b.dataset.roadType))));
    el.onlyWatched.checked = p.onlyWatched;
    el.soundOn.checked = p.sound;
    el.watchedList.innerHTML = p.watched.length
      ? p.watched.map((k) => `<span>★ ${esc(k.split(":").slice(1).join(":"))}</span>`).join("")
      : (p.onlyWatched ? "<span>관심 노선이 없어 알림이 오지 않습니다</span>" : "");
    renderNotifyStatus();
  }

  function renderAlertHistory() {
    el.alertHistory.innerHTML = state.alertHistory.length
      ? state.alertHistory.slice(0, 20).map((e) => eventHTML(e, { compact: true })).join("")
      : '<li class="list-empty">서버가 시작된 뒤 새로 발생한 사고·돌발이 여기에 표시됩니다.</li>';
  }

  function renderNotifyStatus() {
    if (!("Notification" in window)) {
      el.notifyStatus.textContent = /iPhone|iPad/.test(navigator.userAgent)
        ? "iOS는 Safari 공유 → '홈 화면에 추가'로 설치한 뒤 알림을 켤 수 있습니다."
        : "이 브라우저는 시스템 알림을 지원하지 않습니다. 화면 안 알림만 표시됩니다.";
      el.notifyBtn.disabled = true;
      el.notifyBtn.textContent = "미지원";
    } else if (Notification.permission === "granted") {
      el.notifyStatus.textContent = "이 기기에서 알림이 켜져 있습니다.";
      el.notifyBtn.disabled = true;
      el.notifyBtn.textContent = "켜짐";
      el.notifyBtn.className = "btn on";
    } else if (Notification.permission === "denied") {
      el.notifyStatus.textContent = "알림이 차단되어 있습니다. 브라우저 사이트 설정에서 허용해 주세요.";
      el.notifyBtn.disabled = true;
      el.notifyBtn.textContent = "차단됨";
    } else {
      el.notifyStatus.textContent = "사고·돌발 발생 시 시스템 알림을 받으려면 권한을 허용하세요.";
      el.notifyBtn.disabled = false;
      el.notifyBtn.textContent = "알림 켜기";
      el.notifyBtn.className = "btn primary";
    }

    const push = state.push;
    if (!push.supported) {
      el.pushStatus.textContent = "이 브라우저는 푸시를 지원하지 않습니다. 앱이 열려 있을 때만 알림이 옵니다.";
      el.pushBtn.disabled = true;
    } else if (!push.enabled) {
      el.pushStatus.textContent = "서버에 Web Push가 설정되지 않았습니다. 앱이 열려 있을 때만 알림이 옵니다.";
      el.pushBtn.disabled = true;
    } else if (push.subscription) {
      el.pushStatus.textContent = "앱을 닫아도 설정한 조건의 사고·돌발을 푸시로 받습니다.";
      el.pushBtn.disabled = false;
      el.pushBtn.textContent = "푸시 끄기";
      el.pushBtn.className = "btn on";
    } else {
      el.pushStatus.textContent = "앱(브라우저)을 닫아도 휴대폰으로 알림을 받습니다.";
      el.pushBtn.disabled = false;
      el.pushBtn.textContent = "푸시 켜기";
      el.pushBtn.className = "btn";
    }
  }

  async function requestNotify() {
    unlockAudio();
    if (!("Notification" in window)) return false;
    const result = await Notification.requestPermission();
    renderNotifyStatus();
    if (result === "granted") showInfo("알림이 켜졌습니다.");
    return result === "granted";
  }

  function pushFilters() {
    const p = state.prefs;
    let roads = [];
    if (p.onlyWatched) roads = p.watched.length ? p.watched : ["__none__"];
    return { kinds: p.kinds, road_types: p.roadTypes, roads };
  }

  function urlB64ToUint8Array(base64) {
    const padding = "=".repeat((4 - (base64.length % 4)) % 4);
    const raw = atob((base64 + padding).replace(/-/g, "+").replace(/_/g, "/"));
    return Uint8Array.from(raw, (c) => c.charCodeAt(0));
  }

  async function initPush() {
    state.push.supported = "serviceWorker" in navigator && "PushManager" in window;
    try {
      const cfg = await api("api/push/config");
      state.push.enabled = cfg.enabled && !!cfg.public_key;
      state.push.publicKey = cfg.public_key;
    } catch { state.push.enabled = false; }
    if (state.push.supported && state.push.enabled) {
      try {
        const reg = await navigator.serviceWorker.ready;
        state.push.subscription = await reg.pushManager.getSubscription();
        if (state.push.subscription) await syncPushFilters();
      } catch { /* 무시 */ }
    }
    renderNotifyStatus();
  }

  async function togglePush() {
    const push = state.push;
    el.pushBtn.disabled = true;
    try {
      const reg = await navigator.serviceWorker.ready;
      if (push.subscription) {
        const endpoint = push.subscription.endpoint;
        await push.subscription.unsubscribe();
        push.subscription = null;
        await api("api/push/unsubscribe", {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ endpoint }),
        });
        showInfo("푸시 알림을 껐습니다.");
      } else {
        if (Notification.permission !== "granted" && !(await requestNotify())) return;
        push.subscription = await reg.pushManager.subscribe({
          userVisibleOnly: true, applicationServerKey: urlB64ToUint8Array(push.publicKey),
        });
        await syncPushFilters();
        showInfo("푸시 알림을 켰습니다. 앱을 닫아도 알림이 옵니다.");
      }
    } catch (err) {
      showInfo(`푸시 설정 실패: ${err.message}`);
    } finally {
      renderNotifyStatus();
    }
  }

  const syncPushFilters = debounce(async () => {
    const sub = state.push.subscription;
    if (!sub) return;
    try {
      await api("api/push/subscribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ subscription: sub.toJSON(), filters: pushFilters() }),
      });
    } catch (err) {
      showInfo(`푸시 조건 저장 실패: ${err.message}`);
    }
  }, 400);

  async function triggerDemo(kind) {
    try {
      const data = await api("api/demo/incident", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind, road_key: state.selectedKey }),
      });
      const added = data.new_events || [];
      if (added.length && !added.some(matchesPrefs)) {
        showInfo(`${KIND_LABEL[kind]}은(는) 알림 조건에 해당하지 않아 알림이 표시되지 않습니다.`);
      }
    } catch (err) {
      showInfo(err.message);
    }
  }

  // ------------------------------------------------------------------ 이벤트 연결
  function onCamListClick(ev) {
    const item = ev.target.closest(".cam");
    if (!item) return false;
    const list = item.closest("[data-cam-context]");
    openPlayer(item.dataset.cam, camerasForContext(list && list.dataset.camContext, item.dataset.cam));
    return true;
  }

  function bind() {
    document.addEventListener("pointerdown", unlockAudio, { once: true });

    $$(".tabbar button").forEach((b) => b.addEventListener("click", () => {
      navigate(b.dataset.tab === "roads" ? state.roadType : b.dataset.tab);
    }));
    $$('[data-seg="roads"] button').forEach((b) => b.addEventListener("click", () => {
      if (isDesktop()) setRoadType(b.dataset.roadType);
      else navigate(b.dataset.roadType, { replace: true });
    }));
    $$(".side-switch button").forEach((b) => b.addEventListener("click", () => {
      navigate(b.dataset.side === "cctv" ? "cctv" : state.roadType);
    }));
    el.back.addEventListener("click", () => {
      if (history.length > 1 && state.cameFromList) history.back();
      else navigate(state.roadType, { replace: true });
    });
    el.alertsBtn.addEventListener("click", () => {
      if (el.layout.dataset.overlay === "alerts") setView(state.selectedKey ? "detail" : "roads");
      else setView("alerts");
    });
    $("#alertsClose").addEventListener("click", () => setView(state.selectedKey ? "detail" : "roads"));
    document.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape" && el.layout.dataset.overlay === "alerts") setView(state.selectedKey ? "detail" : "roads");
    });

    // 노선 목록
    el.roadSearch.addEventListener("input", debounce(() => { state.search = el.roadSearch.value; renderRoads(); }, 120));
    $$(".sort [data-sort]").forEach((b) => b.addEventListener("click", () => {
      state.sort = b.dataset.sort;
      $$(".sort [data-sort]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      renderRoads();
    }));
    el.watchedOnly.addEventListener("click", () => {
      state.watchedOnly = !state.watchedOnly;
      el.watchedOnly.setAttribute("aria-pressed", String(state.watchedOnly));
      renderRoads();
    });
    el.roadList.addEventListener("click", (ev) => {
      const star = ev.target.closest("[data-watch]");
      if (star) { ev.stopPropagation(); toggleWatch(star.dataset.watch); return; }
      if (ev.target.closest("[data-retry]")) { loadRoads(state.roadType); return; }
      const li = ev.target.closest(".road");
      if (!li) return;
      state.cameFromList = true;
      navigate(roadHash(li.dataset.key));
    });

    // 노선 상세
    el.detail.addEventListener("click", (ev) => {
      const play = ev.target.closest("[data-play]");
      if (play) { openPlayer(play.dataset.play); return; }
      const watch = ev.target.closest("[data-watch]");
      if (watch) { toggleWatch(watch.dataset.watch); return; }
      if (onCamListClick(ev)) return;
      const item = ev.target.closest(".event");
      if (item) focusEvent(item.dataset.event);
    });

    // 돌발 피드
    el.kindFilter.addEventListener("click", (ev) => {
      const b = ev.target.closest("[data-kind]");
      if (!b) return;
      if (!b.dataset.kind) state.feedKinds.clear();
      else if (state.feedKinds.has(b.dataset.kind)) state.feedKinds.delete(b.dataset.kind);
      else state.feedKinds.add(b.dataset.kind);
      renderEvents();
      renderMarkers();
    });
    el.eventList.addEventListener("click", (ev) => {
      const play = ev.target.closest("[data-play]");
      if (play) { openPlayer(play.dataset.play); return; }
      const item = ev.target.closest(".event");
      if (!item) return;
      if (isDesktop()) {
        // 데스크탑: 해당 노선 상세를 열고 지도에서 돌발 위치로 이동
        if (item.dataset.road !== state.selectedKey) navigate(roadHash(item.dataset.road, item.dataset.event));
        else focusEvent(item.dataset.event);
      } else {
        // 모바일: 전국 지도로 이동해 위치를 보여준다
        navigate("map");
        focusEvent(item.dataset.event);
      }
    });
    el.alertHistory.addEventListener("click", (ev) => {
      const item = ev.target.closest(".event");
      if (item) navigate(roadHash(item.dataset.road, item.dataset.event));
    });

    // CCTV
    $$("[data-cctv-type]").forEach((b) => b.addEventListener("click", () => {
      state.cctvType = b.dataset.cctvType;
      $$("[data-cctv-type]").forEach((x) => x.setAttribute("aria-selected", String(x === b)));
      renderCctv();
    }));
    $$("[data-cctv-mode]").forEach((b) => b.addEventListener("click", () => setCctvMode(b.dataset.cctvMode)));
    el.cctvSearch.addEventListener("input", debounce(() => { state.cctvSearch = el.cctvSearch.value; renderCctv(); }, 150));
    el.cctvList.addEventListener("click", (ev) => {
      if (ev.target.closest("[data-cctv-retry]")) { loadCctv(); return; }
      const mapBtn = ev.target.closest("[data-group-map]");
      if (mapBtn) { ev.stopPropagation(); showGroupOnMap(mapBtn.dataset.groupMap); return; }
      if (onCamListClick(ev)) return;
      const head = ev.target.closest("[data-toggle-group]");
      if (head) {
        const key = head.dataset.toggleGroup;
        const li = head.closest(".cctv-group");
        if (li.classList.contains("open")) state.openGroups.delete(key); else state.openGroups.add(key);
        if (li.classList.contains("open") && state.cctvSearch) {
          // 검색 중 자동으로 펼쳐진 묶음은 접을 수 있게 목록에서 뺀다
          li.classList.remove("open");
          li.querySelector(".cams")?.remove();
          head.setAttribute("aria-expanded", "false");
          return;
        }
        renderCctv();
      }
    });
    [el.roadList, el.cctvList, el.detail, el.eventList].forEach((list) => list.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" && ev.target.matches(".road, .cam, .event, [data-toggle-group]")) ev.target.click();
    }));

    // 지도
    $$("[data-layer]").forEach((b) => b.addEventListener("click", () => setLayer(b.dataset.layer, b.getAttribute("aria-pressed") !== "true")));
    $("#fitAllBtn").addEventListener("click", () => withMap(() => map.fitBounds(KOREA_BOUNDS, { padding: 20 })));
    el.mapPane.addEventListener("click", (ev) => {
      const play = ev.target.closest("[data-play]");
      if (play) openPlayer(play.dataset.play);
    });

    // 플레이어
    $("#playerClose").addEventListener("click", closePlayer);
    el.player.addEventListener("close", stopPlayback);
    el.player.addEventListener("click", (ev) => { if (ev.target === el.player) closePlayer(); });
    el.player.addEventListener("keydown", (ev) => {
      if (ev.key === "ArrowLeft") stepPlayer(-1);
      if (ev.key === "ArrowRight") stepPlayer(1);
    });
    el.playerPrev.addEventListener("click", () => stepPlayer(-1));
    el.playerNext.addEventListener("click", () => stepPlayer(1));
    el.playerMap.addEventListener("click", showPlayerOnMap);

    // 알림 설정
    el.notifyBtn.addEventListener("click", requestNotify);
    el.pushBtn.addEventListener("click", togglePush);
    el.alertKinds.addEventListener("click", (ev) => {
      const b = ev.target.closest("[data-alert-kind]");
      if (!b) return;
      const kinds = new Set(state.prefs.kinds);
      if (kinds.has(b.dataset.alertKind)) kinds.delete(b.dataset.alertKind); else kinds.add(b.dataset.alertKind);
      state.prefs.kinds = KINDS.map(([k]) => k).filter((k) => kinds.has(k));
      savePrefs();
      renderAlertSettings();
    });
    el.alertRoadTypes.addEventListener("click", (ev) => {
      const b = ev.target.closest("[data-road-type]");
      if (!b) return;
      const types = new Set(state.prefs.roadTypes);
      if (types.has(b.dataset.roadType)) types.delete(b.dataset.roadType); else types.add(b.dataset.roadType);
      state.prefs.roadTypes = ["ex", "its"].filter((t) => types.has(t));
      savePrefs();
      renderAlertSettings();
    });
    el.onlyWatched.addEventListener("change", () => { state.prefs.onlyWatched = el.onlyWatched.checked; savePrefs(); renderAlertSettings(); });
    el.soundOn.addEventListener("change", () => { state.prefs.sound = el.soundOn.checked; savePrefs(); if (state.prefs.sound) { unlockAudio(); beep(false); } });
    $$("[data-demo-kind]").forEach((b) => b.addEventListener("click", () => triggerDemo(b.dataset.demoKind)));

    window.addEventListener("hashchange", applyRoute);
    desktopMQ.addEventListener("change", () => setView(state.view === "alerts" ? "roads" : state.view));
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") { loadEvents(); refreshTraffic(); }
    });
    setInterval(() => { renderEvents(); renderAlertHistory(); }, REFRESH_MS); // "n분 전" 갱신
    setInterval(refreshTraffic, REFRESH_MS * 5);
    setInterval(() => { if (state.cctvGroups) loadCctv(); }, CCTV_REFRESH_MS); // 영상 URL 토큰 갱신

    navigator.serviceWorker?.addEventListener("message", (ev) => {
      if (ev.data && ev.data.type === "navigate" && ev.data.url) {
        const hash = new URL(ev.data.url, location.href).hash.replace(/^#/, "");
        if (hash) navigate(hash);
      }
    });
  }

  // ------------------------------------------------------------------ 시작
  async function loadStatus() {
    try {
      state.status = await api("api/status");
      el.demoBadge.hidden = !state.status.demo;
      el.demoTools.hidden = !state.status.demo;
      const mapCfg = state.status.map || {};
      const cctvCfg = state.status.cctv || {};
      state.config.style = mapCfg.style || null;
      state.config.styleDark = mapCfg.style_dark || mapCfg.style || null;
      state.config.cctvProxy = cctvCfg.proxy !== false;
      state.config.cctvEnabled = cctvCfg.enabled !== false;
    } catch { /* 상태 조회 실패는 무시 */ }
  }

  async function loadAlertHistory() {
    try {
      state.alertHistory = (await api("api/alerts")).items;
    } catch { state.alertHistory = []; }
    renderAlertHistory();
  }

  async function registerServiceWorker() {
    if (!("serviceWorker" in navigator)) return;
    try { await navigator.serviceWorker.register("sw.js"); } catch { /* HTTP(비보안) 환경 등 */ }
  }

  async function start() {
    bind();
    el.cctvPane.dataset.mode = state.cctvMode;
    renderAlertSettings();
    renderRoads();
    renderCctv();
    applyRoute();
    await loadStatus();
    initMap();
    loadRoads("ex");
    loadRoads("its");
    loadEvents();
    loadCctv();
    loadAlertHistory();
    connectStream();
    registerServiceWorker().then(initPush);
  }

  start();
})();
