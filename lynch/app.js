/* Flood forecast page of one place, laid out like a weather site.
   Reads data/latest.json, data/points/*.json, data/rain.json, data/basin.geojson,
   data/rivers.geojson (+ rivers_q.json on the first river click) and maps/<area>/.
   Everything shown is also in words or a table: levels carry a shape and a word,
   the chart has a table view, values on hover are also readable with the keyboard. */
(function () {
  "use strict";
  var NO_RAIN = "No more rain";
  var DASH = ["", "7 4", "2 3", "10 3 2 3"];
  var LEVEL = { danger: "Above danger level", warning: "Above warning level",
                normal: "Below warning level" };
  var HOUR = 3600e3;
  var S = { data: null, utc: false, sel: null, range: "all", hidden: {}, flood: null,
            pts: {}, rain: null, rivers: null, riversQ: null, places: null, tableOpen: false };
  var map, layers = {}, floodImg = null;

  /* ── Small helpers ──────────────────────────────────────────────────── */
  function $(id) { return document.getElementById(id); }
  function el(tag, attrs, text) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }
  function sv(tag, attrs) {
    var e = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    return e;
  }
  function getJSON(url) {
    return fetch(url + "?t=" + Date.now()).then(function (r) {
      if (!r.ok) throw new Error(url + ": " + r.status);
      return r.json();
    });
  }
  function T(iso) { return Date.parse(iso.length === 17 ? iso.replace("Z", ":00Z") : iso); }
  function tz() { return S.utc ? "UTC" : (S.data.display_timezone || "UTC"); }
  function fmt(ms, o) {
    var opt = { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: tz() };
    if (o && o.day) { opt.weekday = "short"; opt.day = "numeric"; opt.month = "short"; }
    if (o && o.dayOnly) { opt = { weekday: "short", day: "numeric", timeZone: tz() }; }
    try { return new Intl.DateTimeFormat("en-GB", opt).format(new Date(ms)); }
    catch (e) { return new Date(ms).toISOString().slice(0, 16).replace("T", " "); }
  }
  function latLon(lat, lon, d) {               // 32.2845° N, 90.2149° W
    return Math.abs(lat).toFixed(d) + "° " + (lat < 0 ? "S" : "N") + ", " +
           Math.abs(lon).toFixed(d) + "° " + (lon < 0 ? "W" : "E");
  }
  function short(ms) { return fmt(ms, { dayOnly: true }).split(" ")[0] + " " + fmt(ms); }
  function dayKey(ms) {
    return new Intl.DateTimeFormat("en-CA", { timeZone: tz(), year: "numeric", month: "2-digit",
                                               day: "2-digit" }).format(new Date(ms));
  }
  function tzLabel() {
    if (S.utc) return "UTC";
    try {
      var p = new Intl.DateTimeFormat("en-GB", { timeZone: tz(), timeZoneName: "short" })
        .formatToParts(new Date()).filter(function (x) { return x.type === "timeZoneName"; })[0];
      return p ? p.value : tz();
    } catch (e) { return tz(); }
  }
  function q(x) {
    if (x === null || x === undefined || !isFinite(x)) return "–";
    if (x >= 100) return Math.round(x).toLocaleString("en-GB");
    if (x >= 10) return x.toFixed(1);
    return x.toFixed(x >= 1 ? 1 : 2);
  }
  function css(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }
  function levelOf(v, p) {
    if (!p || (p.warning_m3s == null && p.danger_m3s == null) || v == null) return null;
    if (p.danger_m3s != null && v >= p.danger_m3s) return "danger";
    if (p.warning_m3s != null && v >= p.warning_m3s) return "warning";
    return "normal";
  }
  function shape(level, size) {
    var s = size || 12, g = sv("svg", { width: s, height: s, viewBox: "0 0 12 12",
                                         "aria-hidden": "true" });
    var fill = level === "danger" ? "var(--danger)" : level === "warning" ? "var(--warning)" :
               level === "normal" ? "var(--normal)" : "var(--muted)";
    var m;
    if (level === "danger") m = sv("path", { d: "M6 0.6 11.4 6 6 11.4 0.6 6Z" });
    else if (level === "warning") m = sv("path", { d: "M6 0.8 11.5 11 0.5 11Z" });
    else m = sv("circle", { cx: 6, cy: 6, r: 4.6 });
    m.setAttribute("style", "fill:" + fill + ";stroke:var(--panel);stroke-width:1");
    g.appendChild(m);
    return g;
  }
  function badge(level, text) {
    var b = el("span", { "class": "badge " + (level || "none") });
    if (level) b.appendChild(shape(level));
    b.appendChild(document.createTextNode(text || (level ? LEVEL[level] : "No levels set")));
    return b;
  }
  function versions() { return S.data.versions.map(function (v) { return v.name; }); }
  function forecasts() { return versions().filter(function (v) { return v !== NO_RAIN; }); }
  function styleOf(name) {
    if (name === NO_RAIN) return { c: "var(--s5)", dash: "1 4", w: 2 };
    var i = forecasts().indexOf(name);
    if (i < 0) i = 0;
    return i < 4 ? { c: "var(--s" + (i + 1) + ")", dash: DASH[i], w: i === 0 ? 2.5 : 2 }
                 : { c: "var(--s5)", dash: DASH[i % 4] || "4 2", w: 1.5 };
  }
  function lineKey(name, w) {
    var s = styleOf(name), k = sv("svg", { width: w || 22, height: 10, "aria-hidden": "true" });
    var ln = sv("line", { x1: 1, y1: 5, x2: (w || 22) - 1, y2: 5, "stroke-linecap": "round" });
    ln.setAttribute("style", "stroke:" + s.c + ";stroke-width:" + s.w +
                    (s.dash ? ";stroke-dasharray:" + s.dash : ""));
    k.appendChild(ln);
    return k;
  }
  function niceMax(v) {
    if (!(v > 0)) return 1;
    var p = Math.pow(10, Math.floor(Math.log10(v))), f = v / p;
    return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * p;
  }
  function ticks(max, n) {
    var step = niceMax(max / (n || 4)), out = [];
    for (var t = 0; t <= max + 1e-9; t += step) out.push(t);
    return out;
  }
  function compact(v) {
    return v >= 1000 ? (v / 1000).toFixed(v % 1000 ? 1 : 0) + "K" : String(+v.toFixed(2));
  }
  function hashId(id) { return id.replace(/_/g, "-"); }
  // Rain before now: the rain stations, or (none listed) the main forecast model's own
  function byStations() { return !S.data.past_rain || S.data.past_rain === "stations"; }
  function pastRain() {
    return byStations() ? "measured rain" : S.data.past_rain === "zero" ? "no past rain" :
           "past rain (" + (S.data.past_forecast || S.data.main) + ")";
  }
  function nearestReading(M, t) {             // a gauge reading within 10 minutes of t
    var best = null, bd = 10 * 60e3 + 1;
    for (var i = 0; i < M.times.length; i++) {
      var dd = Math.abs(T(M.times[i]) - t);
      if (dd < bd && M.values[i] != null) { bd = dd; best = M.values[i]; }
    }
    return best;
  }
  function metres(v) { return v == null ? "–" : v.toFixed(2) + " m"; }
  function change(g) {
    if (!g || g.change_1h_m == null) return "";
    var c = g.change_1h_m;
    return Math.abs(c) < 0.01 ? "steady over the last hour" :
           (c > 0 ? "rising " : "falling ") + Math.abs(c).toFixed(2) + " m in the last hour";
  }

  /* ── The selected thing: a forecast point or any river cell ─────────── */
  function focus() {
    var sel = S.sel, d = S.data;
    if (sel && sel.kind === "river" && S.riversQ) {
      var rq = S.riversQ, series = {};
      series[d.main] = rq.q[sel.k];
      return { name: "River at " + latLon(sel.lat, sel.lon, 3),
               sub: "Drains " + Math.round(sel.a) + " km². Only the main forecast (" + d.main +
                    ") is kept for every river; hourly values.",
               times: rq.times.map(T), series: series, obs: T(rq.observed_until),
               point: null, river: true };
    }
    var p = d.points.filter(function (x) { return sel && x.id === sel.id; })[0] || d.points[0];
    var pj = p && S.pts[p.id];
    if (!pj) return null;
    var lv = [];
    if (p.warning_m3s != null) lv.push("warning level " + q(p.warning_m3s) + " m³/s");
    if (p.danger_m3s != null) lv.push("danger level " + q(p.danger_m3s) + " m³/s");
    return { name: p.name, id: p.id,
             sub: (lv.length ? "Levels: " + lv.join(", ") + "." : "No warning levels set.") +
                  (p.lat != null ? " " + latLon(p.lat, p.lon, 4) + "." : ""),
             times: pj.times.map(T), series: pj.series, obs: T(pj.observed_until), point: p,
             measured: pj.measured && pj.measured.values.length ? pj.measured : null,
             gauge: p.gauge || null };
  }
  function peakAfter(times, vals, from, to) {
    var best = null, bt = null;
    for (var i = 0; i < times.length; i++) {
      if (times[i] <= from || (to && times[i] > to) || vals[i] == null) continue;
      if (best === null || vals[i] > best) { best = vals[i]; bt = times[i]; }
    }
    return best === null ? null : { q: best, t: bt };
  }
  function valueAt(times, vals, t) {
    var v = null;
    for (var i = 0; i < times.length && times[i] <= t; i++) if (vals[i] != null) v = vals[i];
    return v;
  }

  /* ── Top band, warnings ─────────────────────────────────────────────── */
  function header() {
    var d = S.data, cyc = T(d.cycle_utc);
    var wrap = $("place-pick-wrap");
    wrap.innerHTML = "";
    var others = (S.places || []).filter(function (p) { return p.has_forecast !== undefined; });
    if (others.length > 1) {
      var lab = el("label", { "for": "place-pick", "class": "vh" }, "Place");
      var sel = el("select", { id: "place-pick" });
      others.forEach(function (p) {
        var o = el("option", { value: p.id }, p.short || p.name);
        if (p.id === S.placeId) o.selected = true;
        sel.appendChild(o);
      });
      sel.addEventListener("change", function () { location.href = "../" + sel.value + "/"; });
      wrap.appendChild(lab); wrap.appendChild(sel);
    } else {
      wrap.appendChild(el("h1", { id: "title" }, d.name || d.title));
    }
    document.title = (d.name || d.title) + " — flood forecast";
    var up = $("updated"); up.innerHTML = "";
    var late = Date.now() - cyc > 2.5 * (d.every_minutes || 30) * 60e3;
    var a = el("span"); a.appendChild(document.createTextNode("Updated "));
    a.appendChild(el("strong", {}, fmt(cyc, { day: true }) + " " + tzLabel()));
    up.appendChild(a);
    up.appendChild(el("span", {}, (byStations() ? "Rain measured to " : "Past rain: " + d.main +
                                   "'s own, to ") + fmt(T(d.t_obs_utc)) + " · forecast " +
                      d.forecast_hours + " h ahead"));
    if (d.next_update_utc && !late) up.appendChild(el("span", {}, "Next update about " +
                                                      fmt(T(d.next_update_utc))));
    if (late) up.appendChild(el("span", { "class": "pill late" }, "Late: not updated since " +
                                fmt(cyc, { day: true })));
    up.appendChild(el("span", { "class": "pill exp" }, "Experimental"));
    var home = $("home");
    home.href = S.places ? "../index.html" : "#";
    home.setAttribute("aria-label", S.places ? "All places" : "Flood forecast");
  }

  function alerts() {
    var box = $("alerts"), d = S.data, fc = forecasts(), any = false;
    box.innerHTML = "";
    d.points.forEach(function (p) {
      var hits = [], worst = null, top = null;
      fc.forEach(function (v) {
        var pk = p.peaks[v];
        if (!pk) return;
        if (pk.level === "warning" || pk.level === "danger") hits.push(v);
        if (pk.level === "danger") worst = "danger";
        else if (pk.level === "warning" && worst !== "danger") worst = "warning";
        if (!top || pk.q > top.q) top = { v: v, q: pk.q, t: T(pk.utc) };
      });
      if (!worst) return;
      any = true;
      var b = el("button", { type: "button", "class": "alert " + worst });
      b.appendChild(shape(worst, 16));
      var w = el("span"); w.appendChild(el("span", { "class": "what" }, p.name + ": " +
                                            (worst === "danger" ? "danger" : "warning")));
      w.appendChild(el("span", { "class": "why" }, " · " + hits.length + " of " + fc.length +
        " forecasts above warning · up to " + q(top.q) + " m³/s, " + short(top.t)));
      b.appendChild(w);
      b.addEventListener("click", function () { select({ kind: "point", id: p.id }, true); });
      box.appendChild(b);
    });
    if (!any) {
      var levels = d.points.some(function (p) { return p.warning_m3s != null || p.danger_m3s != null; });
      var c = el("div", { "class": "alert calm" });
      c.appendChild(shape(levels ? "normal" : null, 16));
      c.appendChild(el("span", { "class": "what" }, levels ?
        "No forecast reaches a warning level in the next " + d.forecast_hours + " hours." :
        "No warning levels are set for this place yet."));
      box.appendChild(c);
    }
    if (d.notice) box.appendChild(el("span", { "class": "notice-line" }, d.notice));
  }

  /* ── Left column: points and stations ───────────────────────────────── */
  function sparkline(pj, p) {
    var w = 240, h = 24, g = sv("svg", { "class": "spark", viewBox: "0 0 " + w + " " + h,
                                          preserveAspectRatio: "none", "aria-hidden": "true" });
    var vals = pj.series[S.data.main] || [], ts = pj.times.map(T);
    if (!vals.length) return g;
    var t0 = ts[0], t1 = ts[ts.length - 1], hi = 0;
    vals.forEach(function (v) { if (v > hi) hi = v; });
    if (p.warning_m3s != null) hi = Math.max(hi, p.warning_m3s);
    hi = hi || 1;
    var X = function (t) { return (t - t0) / (t1 - t0) * w; };
    var Y = function (v) { return h - 2 - v / hi * (h - 4); };
    var dd = "", area = "M0 " + h;
    vals.forEach(function (v, i) { if (v == null) return;
      dd += (dd ? "L" : "M") + X(ts[i]).toFixed(1) + " " + Y(v).toFixed(1);
      area += "L" + X(ts[i]).toFixed(1) + " " + Y(v).toFixed(1); });
    area += "L" + w + " " + h + "Z";
    var a = sv("path", { d: area }); a.setAttribute("style", "fill:var(--s1);opacity:0.12");
    var l = sv("path", { d: dd, fill: "none", "vector-effect": "non-scaling-stroke" });
    l.setAttribute("style", "stroke:var(--s1);stroke-width:1.5");
    g.appendChild(a); g.appendChild(l);
    if (p.warning_m3s != null) {
      var wl = sv("line", { x1: 0, x2: w, y1: Y(p.warning_m3s), y2: Y(p.warning_m3s),
                            "vector-effect": "non-scaling-stroke" });
      wl.setAttribute("style", "stroke:var(--warning);stroke-width:1;stroke-dasharray:3 3");
      g.appendChild(wl);
    }
    var nx = X(T(pj.observed_until));
    var nl = sv("line", { x1: nx, x2: nx, y1: 0, y2: h, "vector-effect": "non-scaling-stroke" });
    nl.setAttribute("style", "stroke:var(--fg-2);stroke-width:1");
    g.appendChild(nl);
    return g;
  }
  function pointList() {
    var ol = $("point-list"), d = S.data;
    ol.innerHTML = "";
    d.points.forEach(function (p) {
      var li = el("li"), b = el("button", { type: "button", "class": "pcard",
        "aria-current": S.sel && S.sel.kind === "point" && S.sel.id === p.id ? "true" : "false" });
      var pk = p.peaks[d.main];
      b.appendChild(el("span", { "class": "n" }, p.name));
      b.appendChild(badge(pk ? pk.level : null, pk && pk.level ? undefined : (pk && !pk.level ? "No levels" : "–")));
      var line = el("span", { "class": "q" });
      line.appendChild(document.createTextNode("Now "));
      line.appendChild(el("b", {}, q(p.now_q)));
      line.appendChild(document.createTextNode(" → highest "));
      line.appendChild(el("b", {}, pk ? q(pk.q) : "–"));
      line.appendChild(document.createTextNode(" m³/s" + (pk ? " · " + short(T(pk.utc)) : "")));
      b.appendChild(line);
      if (p.gauge && p.gauge.now_m != null) {
        var gl = el("span", { "class": "q" });
        gl.appendChild(document.createTextNode("Gauge "));
        gl.appendChild(el("b", {}, metres(p.gauge.now_m)));
        var ch = change(p.gauge);
        if (ch) gl.appendChild(document.createTextNode(" · " + ch.replace(" over the last hour", "")
                                                       .replace(" in the last hour", " in 1 h")));
        b.appendChild(gl);
      }
      if (S.pts[p.id]) b.appendChild(sparkline(S.pts[p.id], p));
      b.addEventListener("click", function () { select({ kind: "point", id: p.id }, true); });
      li.appendChild(b); ol.appendChild(li);
    });
  }
  function gaugeCards(ul) {
    (S.data.gauges || []).forEach(function (g) {
      var p = S.data.points.filter(function (x) { return x.name === g.point; })[0];
      var sm = (p && p.gauge) || {};
      var li = el("li", { "class": "scard" });
      li.appendChild(el("span", { "class": "gq", "aria-hidden": "true" }));
      li.appendChild(el("span", { "class": "n" }, g.name));
      var last = g.last_report_utc ? T(g.last_report_utc.replace(" ", "T") + "Z") : null;
      var mins = last ? Math.round((T(S.data.cycle_utc) - last) / 60e3) : null;
      var bad = !g.ok || mins === null || mins > 60;
      li.appendChild(el("span", { "class": "fresh" + (bad ? " bad" : "") },
        !g.ok ? "Not read" : mins === null ? "No reading" : mins <= 60 ? "Reporting" :
        "Silent " + Math.round(mins / 60) + " h"));
      var bits = ["river gauge (water level), shown under " + g.point];
      if (sm.now_m != null) bits.push(metres(sm.now_m) + (change(sm) ? ", " + change(sm) : ""));
      if (last) bits.push("last reading " + fmt(last));
      if (!g.ok && g.error) bits.push(g.error);
      li.appendChild(el("span", { "class": "st" }, bits.join(" · ")));
      ul.appendChild(li);
    });
  }
  function stationList() {
    var ul = $("station-list"), d = S.data;
    ul.innerHTML = "";
    if (!d.stations.length) ul.appendChild(el("li", { "class": "scard" }, "No rain stations " +
      (byStations() ? "" : "are used here: the rain before now is " + d.main + "'s own estimate.")));
    gaugeCards(ul);
    d.stations.forEach(function (s) {
      var li = el("li", { "class": "scard" });
      li.appendChild(el("span", { "class": "sq", "aria-hidden": "true" }));
      li.appendChild(el("span", { "class": "n" }, s.name));
      var last = s.last_report_utc ? T(s.last_report_utc.replace(" ", "T") + "Z") : null;
      var mins = last ? Math.round((T(S.data.cycle_utc) - last) / 60e3) : null;
      var bad = !s.ok || mins === null || mins > 60;
      li.appendChild(el("span", { "class": "fresh" + (bad ? " bad" : "") },
        !s.ok ? "Not read" : mins === null ? "No report" : mins <= 60 ? "Reporting" : "Silent " +
        Math.round(mins / 60) + " h"));
      var bits = [];
      if (last) bits.push("last report " + fmt(last));
      if (s.rain_1h_mm != null) bits.push("rain 1 h " + s.rain_1h_mm + " mm, 24 h " + s.rain_24h_mm + " mm");
      if (s.soil_10cm_pct != null) bits.push("soil moisture " + s.soil_10cm_pct + " % at 10 cm" +
        (s.soil_30cm_pct != null ? ", " + s.soil_30cm_pct + " % at 30 cm" : ""));
      if (!s.ok && s.error) bits.push(s.error);
      li.appendChild(el("span", { "class": "st" }, bits.join(" · ")));
      ul.appendChild(li);
    });
  }

  /* ── Map ────────────────────────────────────────────────────────────── */
  var BASES = {
    streets: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png", 19,
              "© <a href=\"https://www.openstreetmap.org/copyright\">OpenStreetMap</a> contributors"],
    terrain: ["https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", 17,
              "© OpenStreetMap contributors, SRTM · style © <a href=\"https://opentopomap.org\">OpenTopoMap</a> (CC-BY-SA)"],
    satellite: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
                19, "Imagery © Esri, Maxar, Earthstar Geographics"]
  };
  function initMap() {
    if (typeof L === "undefined") { $("map").textContent = "The map could not load."; return; }
    // the mouse wheel over the map always zooms it (the page scrolls beside the map)
    map = L.map("map", { zoomSnap: 0.25, zoomDelta: 0.5, wheelPxPerZoomLevel: 90,
                         scrollWheelZoom: true, preferCanvas: false });
    setBase("streets");
    layers.canvas = L.canvas({ tolerance: 7 });
    layers.group = L.featureGroup().addTo(map);
    getJSON("data/basin.geojson").then(function (g) {
      layers.basin = L.geoJSON(g, { interactive: false,
        style: { color: css("--fg"), weight: 2, fill: false, opacity: 0.8 } }).addTo(map);
      map.fitBounds(layers.basin.getBounds(), { padding: [12, 12] });
    }).catch(function () { map.setView([27.6, 85.5], 10); });
    if (S.data.rivers && S.data.rivers.ok) {
      getJSON("data/rivers.geojson").then(function (g) { S.rivers = g; drawRivers(); })
        .catch(function () { $("show-rivers").parentNode.hidden = true; });
    } else { $("show-rivers").parentNode.hidden = true; }
    map.on("click", readDepth);
    $("base-pick").addEventListener("change", function () { setBase(this.value); });
    $("show-rivers").addEventListener("change", drawRivers);
    $("opacity").addEventListener("input", function () {
      if (layers.flood) layers.flood.setOpacity(+this.value); });
    $("flood-pick").addEventListener("change", function () { showFlood(this.value, true); });
  }
  function setBase(name) {
    if (layers.base) map.removeLayer(layers.base);
    var b = BASES[name];
    layers.base = L.tileLayer(b[0], { maxZoom: b[1], attribution: b[2] }).addTo(map);
    layers.base.bringToBack();
  }
  function drawRivers() {
    if (layers.rivers) { map.removeLayer(layers.rivers); layers.rivers = null; }
    if (!S.rivers || !$("show-rivers").checked) { mapFoot(); return; }
    var colour = css("--river");
    layers.rivers = L.geoJSON(S.rivers, { renderer: layers.canvas,
      style: function (f) {
        var w = Math.max(1.2, Math.min(8, 1 + 1.5 * Math.log10(1 + f.properties.q)));
        return { color: colour, weight: w, opacity: S.flood ? 0.5 : 0.9, lineCap: "round" };
      },
      onEachFeature: function (f, lyr) {
        lyr.on("click", function (e) {
          L.DomEvent.stopPropagation(e);
          var c = f.geometry.coordinates, best = 0, bd = Infinity;
          c.forEach(function (xy, i) {
            if (i >= f.properties.k.length) return;
            var dd = Math.pow(xy[0] - e.latlng.lng, 2) + Math.pow(xy[1] - e.latlng.lat, 2);
            if (dd < bd) { bd = dd; best = i; }
          });
          pickRiver(f.properties.k[best], c[best][1], c[best][0], f.properties.a[best], e.latlng);
        });
      } }).addTo(map);
    layers.rivers.bringToBack();
    if (layers.basin) layers.basin.bringToBack();
    mapFoot();
  }
  function pickRiver(k, lat, lon, a, latlng) {
    var go = function () {
      select({ kind: "river", k: k, lat: lat, lon: lon, a: a }, false);
      $("readout").textContent = "River selected (drains " + Math.round(a) + " km²): its forecast is " +
        "shown on the right and below. " + (readDepth({ latlng: latlng }, true) || "");
    };
    if (S.riversQ) go();
    else {
      $("readout").textContent = "Loading the river flows…";
      getJSON("data/rivers_q.json").then(function (r) { S.riversQ = r; go(); })
        .catch(function () { $("readout").textContent = "The river flows could not load."; });
    }
  }
  function markerIcon(p, selected) {
    var pk = p.peaks[S.data.main], lvl = pk ? pk.level : null;
    var div = el("div", { "class": "pin" + (selected ? " sel" : "") });
    div.appendChild(shape(lvl, selected ? 18 : 15));
    div.appendChild(el("span", { "class": "lab" }, p.name + " · " + (pk ? q(pk.q) : "–") + " m³/s"));
    return L.divIcon({ className: "", html: div.outerHTML, iconSize: null, iconAnchor: [8, 8] });
  }
  function drawMarkers() {
    if (!map) return;
    layers.group.clearLayers();
    S.data.points.forEach(function (p) {
      if (p.lat == null) return;
      var selected = S.sel && S.sel.kind === "point" && S.sel.id === p.id;
      var m = L.marker([p.lat, p.lon], { icon: markerIcon(p, selected), keyboard: true,
        title: p.name + ": open its forecast", zIndexOffset: selected ? 1000 : 0, riseOnHover: true });
      m.on("click", function () { select({ kind: "point", id: p.id }, false); });
      m.addTo(layers.group);
    });
    S.data.stations.forEach(function (s) {
      if (s.lat == null) return;
      L.marker([s.lat, s.lon], { icon: L.divIcon({ className: "", html: '<div class="stn"></div>',
        iconSize: [11, 11] }), title: "Rain station " + s.name, keyboard: true })
        .bindPopup("<b>Rain station</b><br>" + s.name.replace(/</g, "&lt;"))
        .addTo(layers.group);
    });
    (S.data.gauges || []).forEach(function (g) {
      if (g.lat == null) return;
      L.marker([g.lat, g.lon], { icon: L.divIcon({ className: "", html: '<div class="gq"></div>',
        iconSize: [12, 12] }), title: "River gauge " + g.name, keyboard: true })
        .bindPopup("<b>River gauge</b> (water level)<br>" + g.name.replace(/</g, "&lt;"))
        .addTo(layers.group);
    });
    S.data.areas.forEach(function (a) {
      if (!a.box) return;
      L.rectangle([[a.box[1], a.box[0]], [a.box[3], a.box[2]]], { color: css("--fg-2"), weight: 1,
        dashArray: "4 4", fill: false, interactive: false }).addTo(layers.group);
    });
    if (S.sel && S.sel.kind === "river") {
      L.circleMarker([S.sel.lat, S.sel.lon], { radius: 7, color: css("--panel"), weight: 2,
        fillColor: css("--accent"), fillOpacity: 1, interactive: false }).addTo(layers.group);
    }
  }
  function showFlood(id, zoom) {
    S.flood = id || null;
    $("flood-pick").value = id || "";
    if (layers.flood) { map.removeLayer(layers.flood); layers.flood = null; }
    floodImg = null;
    var a = S.data.areas.filter(function (x) { return x.id === id && x.ok; })[0];
    sideList();
    if (S.rivers) drawRivers(); else mapFoot();
    if (!a || !map) return;
    layers.flood = L.imageOverlay(a.png + "?t=" + S.data.cycle_utc, a.bounds,
      { opacity: +$("opacity").value, alt: "Deepest expected water, " + a.name }).addTo(map);
    if (zoom) map.fitBounds(a.bounds, { maxZoom: a.basin ? 12 : 15 });
    var img = new Image();
    img.onload = function () {
      var c = document.createElement("canvas");
      c.width = img.naturalWidth; c.height = img.naturalHeight;
      var cx = c.getContext("2d"); cx.drawImage(img, 0, 0);
      try { floodImg = { a: a, data: cx.getImageData(0, 0, c.width, c.height), w: c.width, h: c.height }; }
      catch (e) { floodImg = null; }
    };
    img.src = a.png + "?t=" + S.data.cycle_utc;
  }
  function mercY(lat) { var r = lat * Math.PI / 180; return Math.log(Math.tan(Math.PI / 4 + r / 2)); }
  function readDepth(e, quiet) {
    var out = quiet ? { textContent: "" } : $("readout");
    if (!floodImg) { if (!quiet) out.textContent = S.flood ? "" :
      "Pick a flood map above to read the water depth by clicking on the map."; return ""; }
    var a = floodImg.a, b = a.bounds, lat = e.latlng.lat, lon = e.latlng.lng;
    var fx = (lon - b[0][1]) / (b[1][1] - b[0][1]);
    var fy = (mercY(b[1][0]) - mercY(lat)) / (mercY(b[1][0]) - mercY(b[0][0]));
    var where = latLon(lat, lon, 4);
    if (fx < 0 || fx > 1 || fy < 0 || fy > 1) {
      out.textContent = where + " is outside the " + a.name + " flood map."; return out.textContent; }
    var x = Math.min(floodImg.w - 1, Math.floor(fx * floodImg.w));
    var y = Math.min(floodImg.h - 1, Math.floor(fy * floodImg.h));
    var i = (y * floodImg.w + x) * 4, px = floodImg.data.data;
    var cls = null;
    if (px[i + 3] > 0) {
      a.classes.forEach(function (c) {
        var r = parseInt(c.colour.slice(1, 3), 16), g = parseInt(c.colour.slice(3, 5), 16),
            bb = parseInt(c.colour.slice(5, 7), 16);
        if (Math.abs(r - px[i]) + Math.abs(g - px[i + 1]) + Math.abs(bb - px[i + 2]) < 12) cls = c;
      });
    }
    out.textContent = "At " + where + ": " + (cls ? "deepest expected water " + cls.from_m +
      (cls.to_m ? "–" + cls.to_m : "+") + " m" : "no flooding expected (under " +
      a.classes[0].from_m + " m)") + " on the " + a.name + " map.";
    return out.textContent;
  }
  function mapFoot() {
    var f = $("map-foot"); f.innerHTML = "";
    var shapes = el("span");
    [["normal", "below warning"], ["warning", "above warning"], ["danger", "above danger"]].forEach(
      function (x) { var s = el("span"); s.appendChild(shape(x[0], 11));
        s.appendChild(document.createTextNode(" " + x[1] + "  ")); shapes.appendChild(s); });
    f.appendChild(shapes);
    var st = el("span"); st.appendChild(el("span", { "class": "stn", "aria-hidden": "true" }));
    st.appendChild(document.createTextNode("rain station"));
    if (S.data.stations.length) f.appendChild(st);
    if ((S.data.gauges || []).some(function (g) { return g.lat != null; })) {
      var gq = el("span"); gq.appendChild(el("span", { "class": "gq", "aria-hidden": "true" }));
      gq.appendChild(document.createTextNode("river gauge")); f.appendChild(gq);
    }
    if (layers.rivers) f.appendChild(el("span", {}, "River width: highest flow (" + S.data.main +
                                        ") · click a river for its forecast"));
    var a = S.data.areas.filter(function (x) { return x.id === S.flood; })[0];
    if (a && a.ok) {
      var lg = el("span"); lg.appendChild(document.createTextNode("Deepest water: "));
      a.classes.forEach(function (c) {
        var s = el("span"); s.appendChild(el("span", { "class": "swatch",
          style: "background:" + c.colour }));
        s.appendChild(document.createTextNode(c.from_m + (c.to_m ? "–" + c.to_m : "+") + " m  "));
        lg.appendChild(s);
      });
      f.appendChild(lg);
    }
  }

  /* ── Selected point: tiles, spread, days ────────────────────────────── */
  function focusPanel(F) {
    $("focus-name").textContent = F.name;
    $("focus-sub").textContent = F.sub;
    var main = S.data.main, ts = F.times, vals = F.series[main] || [];
    var now = valueAt(ts, vals, F.obs), hourAgo = valueAt(ts, vals, F.obs - HOUR);
    var nq = $("now-q"); nq.innerHTML = "";
    nq.appendChild(document.createTextNode(q(now))); nq.appendChild(el("small", {}, "m³/s"));
    var trend = now == null || hourAgo == null ? "" : now > hourAgo * 1.05 + 0.01 ? "Rising" :
                now < hourAgo * 0.95 - 0.01 ? "Falling" : "Steady";
    $("now-sub").textContent = (trend ? trend + " over the last hour. " : "") + "Modelled at " +
                               fmt(F.obs) + " from " + pastRain() + ".";
    var gt = $("gauge-tile"), G = F.gauge;
    gt.hidden = !G;
    if (G) {
      $("gauge-lbl").textContent = "Measured now at the gauge (" + G.name + ")";
      var gm = $("gauge-m"); gm.innerHTML = "";
      gm.appendChild(document.createTextNode(G.now_m == null ? "–" : G.now_m.toFixed(2)));
      gm.appendChild(el("small", {}, "m"));
      var bits = [];
      if (change(G)) bits.push(change(G).charAt(0).toUpperCase() + change(G).slice(1));
      if (G.above_low_m != null) bits.push(G.above_low_m.toFixed(2) + " m above its lowest since " +
                                           fmt(T(G.low_since_utc), { dayOnly: true }));
      if (G.utc) bits.push("read " + fmt(T(G.utc)));
      if (!G.ok) bits.push("not read this time");
      $("gauge-sub").textContent = bits.join(" · ") + ". The gauge's own reading, not the depth.";
    }
    var pk = peakAfter(ts, vals, F.obs);
    var pq = $("peak-q"); pq.innerHTML = "";
    pq.appendChild(document.createTextNode(pk ? q(pk.q) : "–")); pq.appendChild(el("small", {}, "m³/s"));
    $("peak-lbl").textContent = "Highest expected (" + main + ")";
    var ps = $("peak-sub"); ps.innerHTML = "";
    if (pk) {
      ps.appendChild(document.createTextNode("Around " + fmt(pk.t, { day: true }) + " "));
      var lv = levelOf(pk.q, F.point);
      if (lv) ps.appendChild(badge(lv));
    }
    spread(F);
    days(F);
  }
  function spread(F) {
    var box = $("spread"); box.innerHTML = "";
    var names = Object.keys(F.series).filter(function (v) { return v !== NO_RAIN; });
    var rows = names.map(function (v) {
      return { v: v, pk: peakAfter(F.times, F.series[v], F.obs) }; }).filter(function (r) { return r.pk; });
    if (!rows.length) { box.textContent = "No forecast values."; return; }
    var p = F.point || {}, hi = 0;
    rows.forEach(function (r) { hi = Math.max(hi, r.pk.q); });
    if (p.danger_m3s != null) hi = Math.max(hi, p.danger_m3s * 1.1);
    else if (p.warning_m3s != null) hi = Math.max(hi, p.warning_m3s * 1.2);
    hi = niceMax(hi * 1.05);
    var W = box.clientWidth || 360, lw = 82, rw = 118, rh = 19, top = 14, H = top + rows.length * rh + 4;
    var g = sv("svg", { width: W, height: H, viewBox: "0 0 " + W + " " + H });
    var X = function (v) { return lw + v / hi * (W - lw - rw); };
    function band(from, to, c, label, end) {
      var r = sv("rect", { x: X(from), y: top - 2, width: Math.max(0, X(to) - X(from)), height: H - top });
      r.setAttribute("style", "fill:" + c + ";opacity:0.16"); g.appendChild(r);
      var t = sv("text", { x: end ? X(to) - 3 : X(from) + 3, y: 10, "font-size": 10,
                           "text-anchor": end ? "end" : "start" });
      t.setAttribute("style", "fill:var(--fg-2)"); t.textContent = label; g.appendChild(t);
    }
    if (p.warning_m3s != null) band(p.warning_m3s, p.danger_m3s != null ? p.danger_m3s : hi,
                                    "var(--warning)", "▲ " + q(p.warning_m3s), p.danger_m3s != null);
    if (p.danger_m3s != null) band(p.danger_m3s, hi, "var(--danger)", "◆ " + q(p.danger_m3s));
    rows.forEach(function (r, i) {
      var y = top + i * rh + rh / 2, s = styleOf(r.v);
      var gl = sv("line", { x1: lw, x2: W - rw, y1: y, y2: y });
      gl.setAttribute("style", "stroke:var(--grid);stroke-width:1"); g.appendChild(gl);
      var nm = sv("text", { x: 0, y: y + 4, "font-size": 12, "font-weight": r.v === S.data.main ? 700 : 400 });
      nm.setAttribute("style", "fill:var(--fg)"); nm.textContent = r.v; g.appendChild(nm);
      var c = sv("circle", { cx: X(r.pk.q), cy: y, r: 5.5 });
      c.setAttribute("style", "fill:" + s.c + ";stroke:var(--panel);stroke-width:2"); g.appendChild(c);
      var lv = levelOf(r.pk.q, F.point);
      var val = sv("text", { x: W - rw + 8, y: y + 4, "font-size": 12 });
      val.setAttribute("style", "fill:var(--fg)");
      val.textContent = q(r.pk.q) + " · " + short(r.pk.t) +
                        (lv === "danger" ? " ◆" : lv === "warning" ? " ▲" : "");
      g.appendChild(val);
    });
    box.appendChild(g);
    box.setAttribute("aria-label", rows.map(function (r) { return r.v + " " + q(r.pk.q) + " m³/s"; }).join(", "));
  }
  function days(F) {
    var box = $("days"); box.innerHTML = "";
    var rainT = S.rain.times.map(T), mainRain = S.rain.series[S.data.main] || [];
    var keys = [], seen = {};
    F.times.forEach(function (t) { if (t > F.obs) { var k = dayKey(t); if (!seen[k]) {
      seen[k] = 1; keys.push({ k: k, t: t }); } } });
    var fc = Object.keys(F.series).filter(function (v) { return v !== NO_RAIN; });
    keys.forEach(function (d, n) {
      var rain = 0;
      rainT.forEach(function (t, i) { if (t > F.obs && dayKey(t - 1) === d.k) rain += mainRain[i] || 0; });
      var pkMain = null, nWarn = 0;
      fc.forEach(function (v) {
        var best = null;
        F.times.forEach(function (t, i) { var x = F.series[v][i];
          if (t > F.obs && dayKey(t) === d.k && x != null && (best === null || x > best)) best = x; });
        if (v === S.data.main) pkMain = best;
        var lv = levelOf(best, F.point);
        if (lv === "warning" || lv === "danger") nWarn++;
      });
      if (n > 3) return;
      var on = S.range === "day:" + d.k;
      var b = el("button", { type: "button", "class": "day", "aria-pressed": on ? "true" : "false" });
      b.appendChild(el("span", { "class": "d" }, n === 0 ? "Today" : fmt(d.t, { dayOnly: true })));
      var f = el("span", { "class": "f" });
      f.appendChild(el("b", {}, q(pkMain))); f.appendChild(document.createTextNode(" m³/s"));
      b.appendChild(f);
      b.appendChild(el("span", { "class": "r" }, "Rain " + (rain >= 10 ? Math.round(rain) :
                                                             Math.round(rain * 10) / 10) + " mm"));
      var lv = levelOf(pkMain, F.point);
      if (lv) b.appendChild(badge(lv, lv === "normal" ? "Normal" : lv === "warning" ? "Warning" : "Danger"));
      if (fc.length > 1 && F.point && (F.point.warning_m3s != null)) {
        b.appendChild(el("span", { "class": "r" }, nWarn + " of " + fc.length + " ▲"));
        b.setAttribute("aria-label", b.textContent.replace(" ▲", " forecasts above warning"));
      }
      b.addEventListener("click", function () { setRange(on ? "all" : "day:" + d.k); });
      box.appendChild(b);
    });
  }

  /* ── Chart: rain (bars) above river flow (lines), one time axis ─────── */
  function setRange(r) {
    S.range = r;
    Array.prototype.forEach.call(document.querySelectorAll("[data-range]"), function (b) {
      b.setAttribute("aria-pressed", b.getAttribute("data-range") === r ? "true" : "false"); });
    var F = focus(); if (F) { days(F); chart(F); }
  }
  function domain(F) {
    var t0 = F.times[0], t1 = F.times[F.times.length - 1];
    if (S.range === "24") return [F.obs - 6 * HOUR, Math.min(t1, F.obs + 24 * HOUR)];
    if (S.range === "72") return [F.obs - 3 * HOUR, Math.min(t1, F.obs + 72 * HOUR)];
    if (S.range.indexOf("day:") === 0) {
      var k = S.range.slice(4), inDay = F.times.filter(function (t) { return dayKey(t) === k; });
      if (inDay.length) return [Math.max(t0, inDay[0] - HOUR / 6), inDay[inDay.length - 1]];
    }
    return [t0, t1];
  }
  function keys(F) {
    var box = $("keys"); box.innerHTML = "";
    Object.keys(F.series).forEach(function (v) {
      var pk = peakAfter(F.times, F.series[v], F.obs);
      var b = el("button", { type: "button", "class": "key",
                             "aria-pressed": S.hidden[v] ? "false" : "true" });
      b.appendChild(lineKey(v));
      b.appendChild(el("span", {}, v + (v === S.data.main ? " (main)" : "")));
      if (pk && v !== NO_RAIN) b.appendChild(el("b", {}, q(pk.q)));
      b.title = (S.hidden[v] ? "Show " : "Hide ") + v;
      b.addEventListener("click", function () { S.hidden[v] = !S.hidden[v]; render(); });
      box.appendChild(b);
    });
  }
  function chart(F) {
    var box = $("chart"); box.innerHTML = "";
    keys(F);
    var M = F.measured, levelH = M ? 84 : 0;
    var W = Math.max(300, box.clientWidth || 600), ml = 40, mr = 76, top = 40, rainH = 52,
        gap = 12, flowH = 168, axisH = 22, H = top + rainH + gap + flowH + (M ? gap + levelH : 0) + axisH;
    var dom = domain(F), t0 = dom[0], t1 = dom[1];
    var X = function (t) { return ml + (t - t0) / (t1 - t0) * (W - ml - mr); };
    var g = sv("svg", { width: W, height: H, viewBox: "0 0 " + W + " " + H, role: "img",
                        "aria-label": "Rain over the basin and river flow at " + F.name });
    function line(x1, y1, x2, y2, style) {
      var l = sv("line", { x1: x1, y1: y1, x2: x2, y2: y2 }); l.setAttribute("style", style);
      g.appendChild(l); return l; }
    function text(x, y, s, o) {
      var t = sv("text", Object.assign({ x: x, y: y, "font-size": 11 }, o || {}));
      t.setAttribute("style", "fill:" + ((o && o.fill) || "var(--fg-2)")); t.textContent = s;
      g.appendChild(t); return t; }
    var rT = S.rain.times.map(T), vis = Object.keys(F.series).filter(function (v) { return !S.hidden[v]; });
    var rainTop = top, flowTop = top + rainH + gap, flowBot = flowTop + flowH;
    var lvTop = flowBot + gap, lvBot = lvTop + levelH, bot = M ? lvBot : flowBot;
    // measured part
    if (F.obs > t0) {
      var px = sv("rect", { x: ml, y: rainTop, width: Math.max(0, Math.min(X(F.obs), W - mr) - ml),
                            height: bot - rainTop });
      px.setAttribute("style", "fill:var(--past-strong)"); g.appendChild(px);
      if (X(F.obs) - ml > 20 + pastRain().length * 5.5)
        text(X(F.obs) - 4, flowTop + 12, pastRain(), { "font-size": 10, "text-anchor": "end" });
    }
    // day boundaries
    var lastK = null;
    for (var t = Math.ceil(t0 / (HOUR / 6)) * (HOUR / 6); t <= t1; t += HOUR / 6) {
      var k = dayKey(t);
      if (lastK !== null && k !== lastK) {
        line(X(t), rainTop - 4, X(t), bot, "stroke:var(--axis);stroke-width:1");
        text(X(t) + 4, rainTop - 6, fmt(t, { dayOnly: true }), { "font-weight": 700, fill: "var(--fg)" });
      }
      lastK = k;
    }
    // rain panel
    var rMax = 0;
    vis.forEach(function (v) { (S.rain.series[v] || []).forEach(function (x, i) {
      if (rT[i] > t0 && rT[i] - HOUR < t1 && x > rMax) rMax = x; }); });
    rMax = niceMax(Math.max(rMax, 1));
    var RY = function (x) { return rainTop + rainH - x / rMax * rainH; };
    ticks(rMax, 2).forEach(function (tk) {
      line(ml, RY(tk), W - mr, RY(tk), "stroke:var(--grid);stroke-width:1");
      text(ml - 6, RY(tk) + 4, compact(tk), { "text-anchor": "end", "class": "tick" });
    });
    text(ml + 4, RY(ticks(rMax, 2).slice(-1)[0]) + 12, "rain, mm/h", { "font-size": 10 });
    var pxH = (X(t0 + HOUR) - X(t0)), bw = Math.max(1, Math.min(18, pxH - 2));
    var mainRain = S.rain.series[S.data.main] || [];
    if (!S.hidden[S.data.main]) {
      mainRain.forEach(function (x, i) {
        if (!(x > 0) || rT[i] <= t0 || rT[i] - HOUR >= t1) return;
        var x0 = X(rT[i] - HOUR / 2) - bw / 2, y = RY(x);
        var r = sv("rect", { x: x0, y: y, width: bw, height: Math.max(0.5, rainTop + rainH - y), rx: 1.5 });
        r.setAttribute("style", "fill:var(--s1);opacity:" + (byStations() && S.rain.estimated[i] ? 0.4 : 0.85));
        g.appendChild(r);
      });
    }
    vis.forEach(function (v) {
      if (v === S.data.main || !S.rain.series[v]) return;
      var s = styleOf(v), d = "";
      S.rain.series[v].forEach(function (x, i) {
        if (rT[i] <= F.obs || rT[i] - HOUR >= t1 || rT[i] <= t0) return;
        var xa = X(Math.max(t0, rT[i] - HOUR)), xb = X(Math.min(t1, rT[i]));
        d += (d ? "L" : "M") + xa.toFixed(1) + " " + RY(x).toFixed(1) + "L" + xb.toFixed(1) + " " + RY(x).toFixed(1);
      });
      if (!d) return;
      var p = sv("path", { d: d, fill: "none" });
      p.setAttribute("style", "stroke:" + s.c + ";stroke-width:1.5" + (s.dash ? ";stroke-dasharray:" + s.dash : ""));
      g.appendChild(p);
    });
    line(ml, rainTop + rainH, W - mr, rainTop + rainH, "stroke:var(--axis);stroke-width:1");
    // flow panel
    var fMax = 0, pnt = F.point || {};
    vis.forEach(function (v) { F.series[v].forEach(function (x, i) {
      if (F.times[i] >= t0 && F.times[i] <= t1 && x > fMax) fMax = x; }); });
    var thr = [];
    if (pnt.warning_m3s != null) thr.push(["warning", pnt.warning_m3s]);
    if (pnt.danger_m3s != null) thr.push(["danger", pnt.danger_m3s]);
    thr.forEach(function (x) { if (x[1] < fMax * 1.6 || !fMax) fMax = Math.max(fMax, x[1] * 1.05); });
    fMax = niceMax(Math.max(fMax, 0.1));
    var FY = function (x) { return flowBot - x / fMax * flowH; };
    ticks(fMax, 4).forEach(function (tk) {
      line(ml, FY(tk), W - mr, FY(tk), "stroke:var(--grid);stroke-width:1");
      text(ml - 6, FY(tk) + 4, compact(tk), { "text-anchor": "end", "class": "tick" });
    });
    text(ml + 4, FY(ticks(fMax, 4).slice(-1)[0]) + 12, "river flow, m³/s", { "font-size": 10 });
    thr.forEach(function (x) {
      if (x[1] > fMax) return;
      line(ml, FY(x[1]), W - mr, FY(x[1]), "stroke:var(--" + x[0] + ");stroke-width:1.5;stroke-dasharray:6 4");
      text(W - mr + 4, FY(x[1]) + 4, x[0] + " " + q(x[1]), { "font-weight": 700,
        fill: "var(--" + x[0] + "-ink)" });
    });
    var drawn = [];
    vis.slice().sort(function (a, b) { return (a === S.data.main) - (b === S.data.main); }).forEach(function (v) {
      var s = styleOf(v), d = "";
      F.series[v].forEach(function (x, i) {
        var tt = F.times[i];
        if (x == null || tt < t0 || tt > t1) return;
        d += (d ? "L" : "M") + X(tt).toFixed(1) + " " + FY(x).toFixed(1);
      });
      if (!d) return;
      var p = sv("path", { d: d, fill: "none", "stroke-linejoin": "round", "stroke-linecap": "round" });
      p.setAttribute("style", "stroke:" + s.c + ";stroke-width:" + s.w + (s.dash ? ";stroke-dasharray:" + s.dash : ""));
      g.appendChild(p);
      drawn.push(v);
    });
    // label the main forecast's peak
    var mainPk = !S.hidden[S.data.main] && peakAfter(F.times, F.series[S.data.main] || [], Math.max(F.obs, t0 - 1), t1);
    if (mainPk) {
      var cx = X(mainPk.t), cy = FY(mainPk.q);
      var c = sv("circle", { cx: cx, cy: cy, r: 4.5 });
      c.setAttribute("style", "fill:var(--s1);stroke:var(--panel);stroke-width:2"); g.appendChild(c);
      text(Math.min(cx + 7, W - mr - 70), Math.max(flowTop + 10, cy - 7), q(mainPk.q) + " m³/s",
           { "font-weight": 700, fill: "var(--fg)" });
    }
    line(ml, flowBot, W - mr, flowBot, "stroke:var(--axis);stroke-width:1");
    // measured water level: its own panel and scale (right), never on the flow scale
    if (M) {
      var mT = M.times.map(T), lo = Infinity, hi = -Infinity;
      M.values.forEach(function (v, i) {
        if (v == null || mT[i] < t0 || mT[i] > t1) return;
        lo = Math.min(lo, v); hi = Math.max(hi, v); });
      if (lo === Infinity) {
        text(ml + 4, lvTop + 14, "No gauge readings in this time.", { "font-size": 10 });
      } else {
        var span = Math.max(0.3, (hi - lo) * 1.2), mid = (hi + lo) / 2;
        lo = mid - span / 2; hi = mid + span / 2;
        var LY = function (v) { return lvBot - (v - lo) / (hi - lo) * levelH; };
        var lst = niceMax(span / 3);
        for (var n = Math.ceil(lo / lst); n * lst <= hi + 1e-9; n++) {
          var lt = n * lst;
          line(ml, LY(lt), W - mr, LY(lt), "stroke:var(--grid);stroke-width:1");
          text(W - mr + 4, LY(lt) + 4, lt.toFixed(lst < 0.1 ? 2 : 1), { "class": "tick" });
        }
        var md = "", prev = null;
        M.values.forEach(function (v, i) {
          if (v == null || mT[i] < t0 || mT[i] > t1) return;
          md += (md && prev !== null && mT[i] - prev <= 35 * 60e3 ? "L" : "M") +
                X(mT[i]).toFixed(1) + " " + LY(v).toFixed(1);
          prev = mT[i];
        });
        var mp = sv("path", { d: md, fill: "none", "stroke-linejoin": "round" });
        mp.setAttribute("style", "stroke:var(--fg);stroke-width:2"); g.appendChild(mp);
        text(ml + 4, lvTop + 11, "water level measured at " + M.name + ", m", { "font-size": 10 });
      }
      line(ml, lvBot, W - mr, lvBot, "stroke:var(--axis);stroke-width:1");
    }
    // now
    if (F.obs >= t0 && F.obs <= t1) {               // now: a pill on top, measured ← | → forecast
      var nx = X(F.obs), label = "Now " + fmt(F.obs), pw = 8 + label.length * 6.6;
      line(nx, 18, nx, bot, "stroke:var(--accent);stroke-width:2");
      var px0 = Math.max(ml, Math.min(nx - pw / 2, W - mr - pw));
      var pill = sv("rect", { x: px0, y: 2, width: pw, height: 18, rx: 9 });
      pill.setAttribute("style", "fill:var(--accent)"); g.appendChild(pill);
      text(px0 + pw / 2, 15, label, { "text-anchor": "middle", "font-weight": 700, fill: "var(--on-accent)" });
      if (px0 - ml > 80) text(px0 - 6, 15, byStations() ? "← measured" : "← past",
                              { "text-anchor": "end", "font-weight": 700 });
      if (W - mr - (px0 + pw) > 80) text(px0 + pw + 6, 15, "forecast →", { "font-weight": 700 });
    }
    // time axis
    var pxPerHour = (W - ml - mr) / ((t1 - t0) / HOUR), step = 24 * HOUR;
    [1, 2, 3, 6, 12].some(function (h) {           // labels at least 56 px apart
      if (h * pxPerHour >= 56) { step = h * HOUR; return true; } return false; });
    var Q = HOUR / 4;                               // local whole hours (e.g. Nepal is UTC+5:45)
    for (var tk = Math.ceil(t0 / Q) * Q; tk <= t1; tk += Q) {
      var hm = fmt(tk), hh = +hm.slice(0, 2);
      if (hm.slice(3) !== "00" || (hh * HOUR) % step !== 0) continue;
      line(X(tk), bot, X(tk), bot + 4, "stroke:var(--axis);stroke-width:1");
      text(X(tk), bot + 16, fmt(tk), { "text-anchor": "middle", "class": "tick" });
    }
    box.appendChild(g);
    crosshair(box, g, F, { X: X, t0: t0, t1: t1, ml: ml, mr: mr, W: W, top: rainTop, bot: bot,
                           FY: FY, vis: drawn, rT: rT });
    var est = byStations() && S.rain.estimated.some(Boolean);
    $("chart-note").textContent = "Rain bars: average over the basin, " +
      (byStations() ? "" : "before now " + (S.data.past_forecast || S.data.main) +
        "'s own estimate (no rain stations are used here), ") +
      S.data.main + " forecast after now" + (est ? "; paler bars are hours a station missed (estimated)" : "") +
      ". Lines: river flow for each forecast. " + (F.river ? "" : "Dashed lines: warning and danger levels. ") +
      (M ? "Black line at the bottom: the water level measured at " + M.name +
           ", in metres as the gauge reads it (not the depth), on its own scale (right)." : "");
    if (S.tableOpen) table(F);
  }
  function crosshair(box, g, F, k) {
    var tip = el("div", { "class": "tip", hidden: "" }); box.appendChild(tip);
    var ln = sv("line", { y1: k.top - 2, y2: k.bot }); ln.setAttribute("style", "stroke:var(--fg-2);stroke-width:1");
    ln.style.display = "none"; g.appendChild(ln);
    var dots = sv("g"); g.appendChild(dots);
    var idx = null;
    var times = F.times.filter(function (t) { return t >= k.t0 && t <= k.t1; });
    function show(i) {
      if (!times.length) return;
      idx = Math.max(0, Math.min(times.length - 1, i));
      var t = times[idx], x = k.X(t), j = F.times.indexOf(t);
      ln.setAttribute("x1", x); ln.setAttribute("x2", x); ln.style.display = "";
      dots.innerHTML = ""; tip.innerHTML = "";
      tip.appendChild(el("div", { "class": "t" }, fmt(t, { day: true }) +
                         (t <= F.obs ? " · " + pastRain() : " · forecast")));
      var rows = k.vis.map(function (v) { return { v: v, x: F.series[v][j] }; })
        .filter(function (r) { return r.x != null; }).sort(function (a, b) { return b.x - a.x; });
      rows.forEach(function (r) {
        var s = styleOf(r.v), c = sv("circle", { cx: x, cy: k.FY(r.x), r: 4 });
        c.setAttribute("style", "fill:" + s.c + ";stroke:var(--panel);stroke-width:2"); dots.appendChild(c);
        var row = el("div", { "class": "row" });
        row.appendChild(lineKey(r.v, 16)); row.appendChild(el("b", {}, q(r.x) + " m³/s"));
        row.appendChild(el("span", {}, r.v)); tip.appendChild(row);
      });
      var ri = -1;
      k.rT.forEach(function (rt, n) { if (rt - HOUR < t && t <= rt) ri = n; });
      if (ri >= 0) {
        tip.appendChild(el("div", { "class": "sep" }));
        var rr = el("div", { "class": "row" }); rr.appendChild(el("span"));
        rr.appendChild(el("b", {}, (S.rain.series[S.data.main][ri] || 0).toFixed(1) + " mm/h"));
        rr.appendChild(el("span", {}, "rain (" + S.data.main + ")")); tip.appendChild(rr);
      }
      var mv = F.measured ? nearestReading(F.measured, t) : null;
      if (mv != null) {
        tip.appendChild(el("div", { "class": "sep" }));
        var mr2 = el("div", { "class": "row" }); mr2.appendChild(el("span"));
        mr2.appendChild(el("b", {}, metres(mv)));
        mr2.appendChild(el("span", {}, "measured level")); tip.appendChild(mr2);
      }
      tip.hidden = false;
      var bw = box.clientWidth, tw = tip.offsetWidth;
      tip.style.left = (x + 14 + tw > bw ? x - tw - 14 : x + 14) + "px";
      tip.style.top = (k.top + 6) + "px";
    }
    function hide() { tip.hidden = true; ln.style.display = "none"; dots.innerHTML = ""; }
    function nearest(px) {
      var best = 0, bd = Infinity;
      times.forEach(function (t, i) { var dd = Math.abs(k.X(t) - px); if (dd < bd) { bd = dd; best = i; } });
      return best;
    }
    var hit = sv("rect", { x: k.ml, y: k.top - 4, width: k.W - k.ml - k.mr, height: k.bot - k.top + 4 });
    hit.setAttribute("style", "fill:transparent;cursor:crosshair"); g.appendChild(hit);
    function move(e) {
      var r = g.getBoundingClientRect(), px = (e.clientX - r.left) * (k.W / r.width);
      if (px < k.ml || px > k.W - k.mr) { hide(); return; }
      show(nearest(px));
    }
    g.addEventListener("pointerdown", move);
    g.addEventListener("pointermove", function (e) {
      var r = g.getBoundingClientRect(), px = (e.clientX - r.left) * (k.W / r.width);
      if (px < k.ml || px > k.W - k.mr) { hide(); return; }
      show(nearest(px));
    });
    g.addEventListener("pointerleave", hide);
    box.onkeydown = function (e) {
      var step = e.shiftKey ? 1 : Math.max(1, Math.round(HOUR / ((times[1] - times[0]) || HOUR)));
      if (e.key === "ArrowRight") { show(idx === null ? nearest(k.X(F.obs)) : idx + step); e.preventDefault(); }
      else if (e.key === "ArrowLeft") { show(idx === null ? nearest(k.X(F.obs)) : idx - step); e.preventDefault(); }
      else if (e.key === "Home") { show(0); e.preventDefault(); }
      else if (e.key === "End") { show(times.length - 1); e.preventDefault(); }
      else if (e.key === "Escape") hide();
    };
    box.onblur = hide;
  }
  function table(F) {
    var box = $("chart-table"); box.innerHTML = "";
    var t = el("table"), cap = el("caption", { "class": "vh" }, "Hourly rain and river flow at " + F.name);
    t.appendChild(cap);
    var names = Object.keys(F.series), hr = el("tr");
    hr.appendChild(el("th", { scope: "col" }, "Time (" + tzLabel() + ")"));
    hr.appendChild(el("th", { scope: "col", "class": "num" }, "Rain, " + S.data.main + " (mm)"));
    names.forEach(function (v) { hr.appendChild(el("th", { scope: "col", "class": "num" }, v + " (m³/s)")); });
    if (F.measured) hr.appendChild(el("th", { scope: "col", "class": "num" }, "Measured level (m)"));
    t.appendChild(hr);
    var dom = domain(F), rT = S.rain.times.map(T);
    F.times.forEach(function (tt, i) {
      if (tt < dom[0] || tt > dom[1] || (tt % HOUR) !== 0) return;
      var r = el("tr");
      r.appendChild(el("td", {}, fmt(tt, { day: true }) + (tt <= F.obs ? (byStations() ? " (measured)" : " (past)") : "")));
      var ri = rT.indexOf(tt);
      r.appendChild(el("td", { "class": "num" }, ri >= 0 ? (S.rain.series[S.data.main][ri] || 0).toFixed(1) : "–"));
      names.forEach(function (v) { r.appendChild(el("td", { "class": "num" }, q(F.series[v][i]))); });
      if (F.measured) { var mm = nearestReading(F.measured, tt);
        r.appendChild(el("td", { "class": "num" }, mm == null ? "–" : mm.toFixed(2))); }
      t.appendChild(r);
    });
    box.appendChild(t);
  }

  /* ── Right column: flood maps, sources; footer downloads ────────────── */
  function sideList() {
    var box = $("map-list"); box.innerHTML = "";
    if (!S.data.areas.length) box.appendChild(el("p", { "class": "chart-note" }, "No flood maps are set."));
    S.data.areas.forEach(function (a) {
      var b = el("button", { type: "button", "class": "mcard",
                             "aria-pressed": S.flood === a.id ? "true" : "false" });
      b.appendChild(el("span", { "class": "n" }, a.name + (a.basin ? " (all rivers)" : "")));
      b.appendChild(el("span", { "class": "badge none" }, a.method === "2d" ? "2-D" : "HAND"));
      b.appendChild(el("span", { "class": "v" }, a.ok ? (a.flooded_km2.toFixed(2) + " km² under water, deepest " +
        a.max_depth_m.toFixed(1) + " m") : "No map this time" + (a.error ? ": " + a.error : "")));
      if (!a.ok) b.disabled = true;
      b.addEventListener("click", function () { showFlood(S.flood === a.id ? "" : a.id, true); });
      box.appendChild(b);
    });
    (S.data.links || []).forEach(function (l) {          // website: links: in the forecast file
      var a = el("a", { "class": "mcard", href: l.url });
      a.appendChild(el("span", { "class": "n" }, l.name));
      if (l.text) a.appendChild(el("span", { "class": "v" }, l.text));
      box.appendChild(a);
    });
  }
  function sources() {
    var t = $("sources"); t.innerHTML = "";
    var hr = el("tr");
    ["Forecast", "Issued", "Note"].forEach(function (h) { hr.appendChild(el("th", { scope: "col" }, h)); });
    t.appendChild(hr);
    S.data.versions.forEach(function (v) {
      var r = el("tr"), c = el("td");
      c.appendChild(lineKey(v.name, 18)); c.appendChild(document.createTextNode(" " + v.name + (v.main ? " (main)" : "")));
      r.appendChild(c);
      r.appendChild(el("td", { "class": "nowrap" }, v.issued_utc ? short(T(v.issued_utc.replace(" ", "T") + "Z")) : "–"));
      r.appendChild(el("td", {}, (v.ran ? "" : "did not run. ") + (v.note || "")));
      t.appendChild(r);
    });
  }
  function downloads() {
    var p = $("downloads"); p.innerHTML = "";
    p.appendChild(document.createTextNode("Download: "));
    var links = S.data.points.map(function (x) { return ["data/points/" + x.id + ".csv", x.name + " flows (CSV)"]; });
    links.push(["data/rain.csv", "rain (CSV)"]);
    S.data.areas.forEach(function (a) { if (a.ok) links.push([a.tif, a.name + " depth (GeoTIFF)"]); });
    links.forEach(function (l, i) {
      if (i) p.appendChild(document.createTextNode(" · "));
      p.appendChild(el("a", { href: l[0], download: "" }, l[1]));
    });
    if (S.data.attribution && S.data.attribution.length) {
      $("credits").textContent = "Model: MRRpy. " + S.data.attribution.join(". ") +
        ". Maps © OpenStreetMap contributors, OpenTopoMap, Esri.";
    }
  }

  /* ── Selection, render, load ────────────────────────────────────────── */
  function select(sel, pan) {
    S.sel = sel;
    if (sel.kind === "point") {
      try { history.replaceState(null, "", "#" + hashId(sel.id)); } catch (e) { /* framed */ }
      var p = S.data.points.filter(function (x) { return x.id === sel.id; })[0];
      if (pan && map && p && p.lat != null) map.panTo([p.lat, p.lon]);
    }
    render();
  }
  function render() {
    var F = focus();
    pointList();
    drawMarkers();
    if (!F) return;
    focusPanel(F);
    chart(F);
  }
  function renderAll() {
    header(); alerts(); stationList(); sideList(); sources(); downloads();
    render();
  }
  function load() {
    return getJSON("data/latest.json").then(function (d) {
      S.data = d;
      return Promise.all([getJSON("data/rain.json")].concat(d.points.map(function (p) {
        return getJSON("data/points/" + p.id + ".json").then(function (j) { S.pts[p.id] = j; })
          .catch(function () {}); })));
    }).then(function (r) { S.rain = r[0]; S.riversQ = null; });
  }
  function start() {
    var bits = location.pathname.split("/").filter(Boolean);
    S.placeId = bits.length ? (bits[bits.length - 1].indexOf(".") > 0 ? bits[bits.length - 2] : bits[bits.length - 1]) : "";
    getJSON("../places.json").then(function (pl) { S.places = pl.places; if (S.data) header(); })
      .catch(function () {});
    load().then(function () {
      var h = location.hash.slice(1).replace(/-/g, "_");
      var p = S.data.points.filter(function (x) { return x.id === h; })[0] || S.data.points[0];
      S.sel = p ? { kind: "point", id: p.id } : null;
      $("tz-local").textContent = "Local time";
      initMap();
      renderAll();
      var basin = S.data.areas.filter(function (a) { return a.basin && a.ok; })[0] ||
                  S.data.areas.filter(function (a) { return a.ok; })[0];
      if (basin && map) showFlood(basin.id, false);
      S.data.areas.forEach(function (a) {
        if (a.ok) $("flood-pick").appendChild(el("option", { value: a.id }, a.name));
      });
      if (S.flood) $("flood-pick").value = S.flood;
      mapFoot();
    }).catch(function (e) {
      $("title").textContent = "No forecast here yet";
      $("updated").textContent = "The first update has not been published. (" + e.message + ")";
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-range]"), function (b) {
      b.addEventListener("click", function () { setRange(b.getAttribute("data-range")); }); });
    $("table-toggle").addEventListener("click", function () {
      S.tableOpen = !S.tableOpen;
      this.setAttribute("aria-expanded", S.tableOpen ? "true" : "false");
      this.textContent = S.tableOpen ? "Hide the table" : "Show as a table";
      $("chart-table").hidden = !S.tableOpen;
      var F = focus(); if (F && S.tableOpen) table(F);
    });
    function setTz(utc) {
      S.utc = utc;
      $("tz-local").setAttribute("aria-pressed", utc ? "false" : "true");
      $("tz-utc").setAttribute("aria-pressed", utc ? "true" : "false");
      if (S.data) renderAll();
    }
    $("tz-local").addEventListener("click", function () { setTz(false); });
    $("tz-utc").addEventListener("click", function () { setTz(true); });
    var timer;
    window.addEventListener("resize", function () {
      clearTimeout(timer); timer = setTimeout(function () { if (S.data) render(); }, 150); });
    setInterval(function () {                       // a new update: reload, keep what is selected
      if (!S.data) return;
      getJSON("data/latest.json").then(function (d) {
        if (d.cycle_utc === S.data.cycle_utc) return;
        var flood = S.flood;
        load().then(function () { renderAll(); if (map) showFlood(flood, false); if (S.rivers) {
          getJSON("data/rivers.geojson").then(function (g) { S.rivers = g; drawRivers(); }); } });
      }).catch(function () {});
    }, 5 * 60e3);
  }
  start();
})();
