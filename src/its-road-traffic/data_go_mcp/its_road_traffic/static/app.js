/* 전국 도로 소통정보 — 모바일/데스크탑 하이브리드 PWA */
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
  const KOREA_BOUNDS = [[33.0, 124.6], [38.7, 130.0]];
  const ROAD_RENDER_LIMIT = 300;
  const REFRESH_MS = 60_000;

  // ------------------------------------------------------------------ 상태
  const state = {
    roadType: "ex",
    view: "roads",
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
    prefs: loadPrefs(),
    push: { supported: false, enabled: false, publicKey: null, subscription: null },
    focusEventId: null,
  };

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const desktopMQ = window.matchMedia("(min-width: 1024px)");
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

  function normalize(text) {
    return String(text || "").replace(/\s+/g, "").toLowerCase();
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

  // ------------------------------------------------------------------ 화면 전환
  function setView(view) {
    if (isDesktop()) {
      el.layout.dataset.view = view === "alerts" ? el.layout.dataset.view : view;
      el.layout.dataset.overlay = view === "alerts" ? "alerts" : "";
    } else {
      el.layout.dataset.view = view;
      el.layout.dataset.overlay = "";
    }
    state.view = view;
    const tab = view === "roads" || view === "detail" ? state.roadType : view;
    $$(".tabbar button").forEach((b) => {
      if (b.dataset.tab === tab) b.setAttribute("aria-current", "page");
      else b.removeAttribute("aria-current");
    });
    el.back.hidden = isDesktop() || view !== "detail";
    el.title.textContent = !isDesktop() && view === "detail" && state.detail
      ? state.detail.name
      : "전국 도로 소통정보";
    if (view === "alerts") {
      state.unseenAlerts = 0;
      updateBadges();
    }
    placeMap();
  }

  function placeMap() {
    // 모바일 상세 화면에서는 지도를 상세 안으로 옮기고, 그 외에는 원래 자리로 돌린다.
    const slot = $(".map-slot", el.detail);
    if (!isDesktop() && state.view === "detail" && slot) {
      if (el.mapPane.parentElement !== slot) slot.appendChild(el.mapPane);
    } else if (el.mapPane.parentElement !== el.layout) {
      el.layout.insertBefore(el.mapPane, el.detailPane);
    }
    if (map) setTimeout(flushMapAction, 50);
  }

  function setRoadType(type) {
    state.roadType = type;
    $$(".seg button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.roadType === type)));
    renderRoads();
    if (!state.roads[type]) loadRoads(type);
  }

  function parseHash() {
    const raw = location.hash.replace(/^#/, "");
    if (!raw) return { view: "roads" };
    if (raw.includes("=")) {
      const params = new URLSearchParams(raw);
      if (params.get("road")) return { view: "detail", road: params.get("road"), event: params.get("event") };
    }
    if (raw === "ex" || raw === "its") return { view: "roads", roadType: raw };
    if (raw === "events" || raw === "alerts") return { view: raw };
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
    return `<li class="road" tabindex="0" data-key="${esc(road.key)}" aria-current="${road.key === state.selectedKey}">
      ${shieldHTML(road)}
      <div class="road-main">
        <div class="road-name">${esc(road.name)}</div>
        <div class="road-sub">${barHTML(road.measured_count ? road.grade_share : null)}
          <span>${road.measured_count ? `${road.measured_count}개 구간` : "소통정보 없음"}</span></div>
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
    drawRoadPath();
    if (state.focusEventId) focusEvent(state.focusEventId);
  }

  function renderDetail() {
    const d = state.detail;
    if (!d) return;
    const watched = state.prefs.watched.includes(d.key);
    const typeLabel = d.road_type === "ex" ? "고속도로" : "국도";
    const avg = d.avg_speed != null ? `${Math.round(d.avg_speed)}<small> km/h</small>` : "-";
    const min = d.min_speed != null ? `${Math.round(d.min_speed)}<small> km/h</small>` : "-";
    const urgent = d.events.filter((e) => e.urgent).length;

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
      <div class="section-title">방향별 소통</div>
      ${directions}
      <div class="section-title">진행 중인 돌발 (${d.events.length})</div>
      <ul class="event-list">${d.events.length ? d.events.map((e) => eventHTML(e)).join("") : '<li class="list-empty">진행 중인 돌발이 없습니다.</li>'}</ul>`;
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

  function eventHTML(e, { compact = false } = {}) {
    const title = `${esc(e.road_name)}<small>${esc(e.direction_label || "")}</small>`;
    const meta = [
      e.started_at ? `${fmtAgo(e.started_at)} 발생` : "",
      e.lanes_blocked ? `${esc(e.lanes_blocked)}차로 차단` : "",
      !compact && e.ends_at ? `종료 예정 ${fmtTime(e.ends_at)}` : "",
    ].filter(Boolean).map((m) => `<span>${m}</span>`).join("");
    return `<li class="event kind-${e.kind} ${state.newIds.has(e.id) ? "is-new" : ""}" data-event="${esc(e.id)}" data-road="${esc(e.road_key)}" tabindex="0">
      <span class="event-kind">${esc(e.kind_label)}</span>
      <div class="event-title">${title}</div>
      <div class="event-msg">${esc(e.message || e.detail)}</div>
      <div class="event-meta">${meta}</div>
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

  // ------------------------------------------------------------------ 지도
  let map = null;
  let markerLayer = null;
  let pathLayer = null;
  const markers = new Map();
  let pendingMapAction = null;
  let lastFitKey = null;

  function mapVisible() {
    const c = map && map.getContainer();
    return !!c && c.offsetWidth > 0 && c.offsetHeight > 0;
  }

  // 숨겨진(크기 0) 지도에서 fitBounds/flyTo를 하면 좌표가 NaN이 되므로 보일 때까지 미룬다.
  function withMap(action) {
    if (!map) return;
    if (mapVisible()) { map.invalidateSize(); action(); }
    else pendingMapAction = action;
  }

  function flushMapAction() {
    if (!map || !mapVisible()) return;
    map.invalidateSize();
    if (pendingMapAction) { const action = pendingMapAction; pendingMapAction = null; action(); }
  }

  function initMap() {
    const container = $("#map");
    if (!window.L) {
      container.innerHTML = '<div class="map-fallback">지도를 불러오지 못했습니다. 네트워크 연결을 확인하세요.</div>';
      return;
    }
    map = L.map(container, { zoomControl: true, preferCanvas: true, attributionControl: true });
    map.setView([36.4, 127.9], 7);
    withMap(() => map.fitBounds(KOREA_BOUNDS));
    const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    L.tileLayer(`https://{s}.basemaps.cartocdn.com/${dark ? "dark_all" : "light_all"}/{z}/{x}/{y}{r}.png`, {
      subdomains: "abcd",
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
    }).addTo(map);
    pathLayer = L.layerGroup().addTo(map);
    markerLayer = L.layerGroup().addTo(map);
  }

  function popupHTML(e) {
    return `<div class="popup-title">[${esc(e.kind_label)}] ${esc(e.road_name)} ${esc(e.direction_label || "")}</div>
      <div>${esc(e.message || e.detail)}</div>
      <div class="muted">${e.started_at ? `${fmtAgo(e.started_at)} 발생` : ""}</div>
      <a href="#${esc(roadHash(e.road_key, e.id))}">노선 보기 →</a>`;
  }

  function renderMarkers() {
    if (!map) return;
    markerLayer.clearLayers();
    markers.clear();
    visibleEvents().forEach((e) => {
      if (e.lat == null || e.lon == null) return;
      const icon = L.divIcon({
        className: "",
        html: `<div class="mk kind-${e.kind} ${e.urgent ? "urgent" : ""}">${KIND_GLYPH[e.kind] || "·"}</div>`,
        iconSize: e.urgent ? [30, 30] : [26, 26],
        iconAnchor: e.urgent ? [15, 15] : [13, 13],
      });
      const marker = L.marker([e.lat, e.lon], {
        icon,
        title: `${e.kind_label} ${e.road_name}`,
        zIndexOffset: e.urgent ? 1000 : 0,
      }).bindPopup(popupHTML(e));
      marker.addTo(markerLayer);
      markers.set(e.id, marker);
    });
  }

  function drawRoadPath() {
    if (!map) return;
    pathLayer.clearLayers();
    const d = state.detail;
    if (!d) return;
    const color = cssVar(`--${d.grade === "unknown" ? "unknown" : d.grade}`) || "#2563eb";
    const points = d.path && d.path.length ? d.path : [];
    if (points.length > 1) {
      L.polyline(points, { color: "#000", weight: 10, opacity: 0.15 }).addTo(pathLayer);
      L.polyline(points, { color, weight: 6, opacity: 0.9 }).addTo(pathLayer);
    }
    const eventPoints = d.events.filter((e) => e.lat != null).map((e) => [e.lat, e.lon]);
    const all = points.concat(eventPoints);
    // 주기적 새로고침 때 사용자가 옮긴 지도를 되돌리지 않도록 노선이 바뀔 때만 맞춘다
    if (all.length && lastFitKey !== d.key && !state.focusEventId) {
      withMap(() => map.fitBounds(L.latLngBounds(all).pad(0.15), { maxZoom: 12 }));
    }
    lastFitKey = d.key;
  }

  function focusEvent(id) {
    state.focusEventId = null;
    const event = state.events.find((e) => e.id === id) || (state.detail && state.detail.events.find((e) => e.id === id));
    $$(`.event[data-event="${CSS.escape(id)}"]`).forEach((li) => {
      li.classList.remove("is-new"); void li.offsetWidth; li.classList.add("is-new");
    });
    if (!map || !event || event.lat == null) return;
    withMap(() => {
      map.flyTo([event.lat, event.lon], Math.max(map.getZoom(), 12), { duration: 0.6 });
      const marker = markers.get(id);
      if (marker) setTimeout(() => marker.openPopup(), 650);
    });
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
    loadRoads(state.roadType, { silent: true });
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
  function bind() {
    document.addEventListener("pointerdown", unlockAudio, { once: true });

    $$(".tabbar button").forEach((b) => b.addEventListener("click", () => navigate(b.dataset.tab)));
    $$(".seg button").forEach((b) => b.addEventListener("click", () => setRoadType(b.dataset.roadType)));
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
    el.roadList.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" && ev.target.classList.contains("road")) ev.target.click();
    });

    el.detail.addEventListener("click", (ev) => {
      const watch = ev.target.closest("[data-watch]");
      if (watch) { toggleWatch(watch.dataset.watch); return; }
      const item = ev.target.closest(".event");
      if (item) focusEvent(item.dataset.event);
    });

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
      const item = ev.target.closest(".event");
      if (!item) return;
      if (isDesktop()) {
        // 데스크탑: 해당 노선 상세를 열고 지도에서 돌발 위치로 이동
        if (item.dataset.road !== state.selectedKey) navigate(roadHash(item.dataset.road, item.dataset.event));
        else focusEvent(item.dataset.event);
      } else {
        focusEvent(item.dataset.event);
      }
    });
    el.alertHistory.addEventListener("click", (ev) => {
      const item = ev.target.closest(".event");
      if (item) navigate(roadHash(item.dataset.road, item.dataset.event));
    });

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
    $("#fitAllBtn").addEventListener("click", () => withMap(() => map.fitBounds(KOREA_BOUNDS)));

    window.addEventListener("hashchange", applyRoute);
    desktopMQ.addEventListener("change", () => setView(state.view === "alerts" ? "roads" : state.view));
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") { loadEvents(); refreshTraffic(); }
    });
    setInterval(() => { renderEvents(); renderAlertHistory(); }, REFRESH_MS); // "n분 전" 갱신
    setInterval(refreshTraffic, REFRESH_MS * 5);

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

  function start() {
    bind();
    initMap();
    renderAlertSettings();
    renderRoads();
    applyRoute();
    loadStatus();
    loadRoads(state.roadType);
    loadEvents();
    loadAlertHistory();
    connectStream();
    registerServiceWorker().then(initPush);
  }

  start();
})();
