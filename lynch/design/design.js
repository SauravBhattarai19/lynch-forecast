/* Design-storm page: reads data/design.json, data/points/*.json, data/basin.geojson and
   maps/<storm>/<area>/.  Flows in m³/s and cfs, depths in m and ft; every chart has
   the same numbers in a table. */
(function () {
  "use strict";
  var CFS = 35.3147, FT = 3.28084;
  var COLOURS = ["#1d6fb8", "#c2410c", "#15803d", "#7c3aed"];
  var D = null, S = { storm: null, area: null, point: null, pts: {} };
  var map, base, overlay = null, img = null, pointLayer = null;

  function $(id) { return document.getElementById(id); }
  function el(tag, attrs, text) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }
  function sv(tag, attrs, text) {
    var e = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    if (text !== undefined) e.textContent = text;
    return e;
  }
  function getJSON(u) { return fetch(u + "?t=" + Date.now()).then(function (r) {
    if (!r.ok) throw new Error(u + ": " + r.status); return r.json(); }); }
  function n0(x) { return Math.round(x).toLocaleString("en-US"); }
  function qq(x) { return x == null ? "–" : (x >= 100 ? n0(x) : x.toFixed(1)) + " m³/s (" + n0(x * CFS) + " cfs)"; }
  function storm(id) { return D.storms.filter(function (s) { return s.id === id; })[0]; }
  function colourOf(id) { return COLOURS[D.storms.map(function (s) { return s.id; }).indexOf(id) % COLOURS.length]; }
  function lonText(lon) { return Math.abs(lon).toFixed(4) + "° " + (lon < 0 ? "W" : "E"); }

  /* ── Storm picker and the words at the top ─────────────────────────── */
  function pickers() {
    var box = $("storm-pick"); box.innerHTML = "";
    D.storms.forEach(function (s) {
      var b = el("button", { type: "button", "aria-pressed": s.id === S.storm ? "true" : "false" }, s.name);
      b.addEventListener("click", function () { S.storm = s.id; render(); });
      box.appendChild(b);
    });
    var ap = $("area-pick"), keep = S.area; ap.innerHTML = "";
    var names = [];
    D.areas.forEach(function (a) { if (a.storm === S.storm && names.indexOf(a.area) < 0) names.push(a.area); });
    names.sort(function (x, y) { return area(x).basin - area(y).basin; });
    names.forEach(function (n) {
      var a = area(n);
      ap.appendChild(el("option", { value: n }, n + (a.basin ? " (every creek, HAND)" : " (2-D)") +
        (a.ok ? "" : " — no map")));
    });
    S.area = names.indexOf(keep) >= 0 ? keep : names[0];
    ap.value = S.area || "";
  }
  function area(name) {
    return D.areas.filter(function (a) { return a.storm === S.storm && a.area === name; })[0] || {};
  }
  function words() {
    var s = storm(S.storm);
    $("title").textContent = "Design storms · " + D.name.replace(/ flood forecast.*/, "");
    $("lead").textContent = "The " + D.storms.map(function (x) { return x.name; }).join(" and the ") +
      " of NOAA Atlas 14 fall on the whole basin, " + D.duration_hours + " hours each, starting from the soil " +
      "as the forecast had it on " + D.state_utc.slice(0, 10) + ". Each is run through the same model as the live " +
      "forecast (USGS 3DEP 10 m terrain) and the flood depth is drawn on the USGS 3DEP 1 m lidar.";
    var f = $("facts"); f.innerHTML = "";
    var g = gaugePoint();
    [[s.depth_mm.toFixed(0) + " mm", s.depth_in.toFixed(2) + " in of rain in " + D.duration_hours + " h"],
     [s.peak_1h_mm.toFixed(0) + " mm", "in the wettest hour (" + (s.peak_1h_mm / 25.4).toFixed(2) + " in)"],
     [g ? n0(g.peaks[S.storm].q * CFS) + " cfs" : "–", g ? "highest flow at " + g.name : ""],
     [area(S.area).ok ? area(S.area).flooded_km2.toFixed(2) + " km²" : "–",
      area(S.area).ok ? "under 10 cm of water or more, " + S.area : ""]
    ].forEach(function (x) {
      var d = el("div", { "class": "fact" }); d.appendChild(el("b", {}, x[0])); d.appendChild(el("span", {}, x[1]));
      f.appendChild(d);
    });
  }
  function gaugePoint() {
    var ffa = D.gauge_frequency;
    var id = ffa && ffa.point;
    return D.points.filter(function (p) { return p.name === id || p.id === id; })[0] || null;
  }

  /* ── Map ────────────────────────────────────────────────────────────── */
  var BASES = {
    streets: ["https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", "© OpenStreetMap contributors"],
    satellite: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", "Esri, Maxar, Earthstar Geographics"],
    terrain: ["https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", "© OpenStreetMap contributors, SRTM, OpenTopoMap"]
  };
  function setBase(k) {
    if (base) map.removeLayer(base);
    base = L.tileLayer(BASES[k][0], { attribution: BASES[k][1], maxZoom: 19 }).addTo(map);
  }
  function initMap() {
    map = L.map("dmap", { zoomControl: true });
    setBase("streets");
    getJSON("data/basin.geojson").then(function (g) {
      var b = L.geoJSON(g, { style: { color: "#0f172a", weight: 2, fill: false }, interactive: false }).addTo(map);
      map.fitBounds(b.getBounds());
    }).catch(function () { map.setView([32.3, -90.23], 12); });
    map.on("click", readDepth);
    $("base-pick").addEventListener("change", function () { setBase(this.value); });
    $("opacity").addEventListener("input", function () { if (overlay) overlay.setOpacity(+this.value); });
    $("area-pick").addEventListener("change", function () { S.area = this.value; showArea(true); words(); });
  }
  function showArea(zoom) {
    if (overlay) { map.removeLayer(overlay); overlay = null; }
    img = null;
    var a = area(S.area);
    $("legend").innerHTML = "";
    $("map-note").textContent = "";
    if (!a.ok) { $("map-note").textContent = a.area ? "No map for this storm: " + (a.error || "") : ""; return; }
    overlay = L.imageOverlay(a.png, a.bounds, { opacity: +$("opacity").value,
      alt: "Deepest water, " + a.area + ", " + storm(S.storm).name }).addTo(map);
    if (zoom) map.fitBounds(a.bounds, { maxZoom: a.basin ? 14 : 16 });
    var lg = $("legend"); lg.appendChild(document.createTextNode("Deepest water: "));
    a.classes.forEach(function (c) {
      var s = el("span"); s.appendChild(el("span", { "class": "swatch", style: "background:" + c.colour }));
      s.appendChild(document.createTextNode(c.from_m + (c.to_m ? "–" + c.to_m : "+") + " m (" +
        (c.from_m * FT).toFixed(1) + (c.to_m ? "–" + (c.to_m * FT).toFixed(1) : "+") + " ft) · " +
        c.area_km2.toFixed(2) + " km²"));
      lg.appendChild(s);
    });
    $("map-note").textContent = (a.basin ? "Every creek of the basin by HAND (height above the nearest " +
      "creek) on the 1 m lidar: quick, and fair for the main valleys. " :
      "A 2-D flood simulation on the 1 m lidar, the creeks entering at the box edges with the model's " +
      "flows. ") + "Deepest " + a.max_depth_m.toFixed(1) + " m (" + (a.max_depth_m * FT).toFixed(1) + " ft)" +
      (a.seconds ? "; drawn in " + Math.round(a.seconds) + " s." : ".");
    var im = new Image();
    im.onload = function () {
      var c = document.createElement("canvas"); c.width = im.naturalWidth; c.height = im.naturalHeight;
      var cx = c.getContext("2d"); cx.drawImage(im, 0, 0);
      try { img = { a: a, d: cx.getImageData(0, 0, c.width, c.height), w: c.width, h: c.height }; }
      catch (e) { img = null; }
    };
    im.src = a.png;
  }
  function mercY(lat) { var r = lat * Math.PI / 180; return Math.log(Math.tan(Math.PI / 4 + r / 2)); }
  function readDepth(e) {
    var out = $("readout");
    if (!img) { out.textContent = "Pick a map to read the depth."; return; }
    var a = img.a, b = a.bounds, lat = e.latlng.lat, lon = e.latlng.lng;
    var fx = (lon - b[0][1]) / (b[1][1] - b[0][1]);
    var fy = (mercY(b[1][0]) - mercY(lat)) / (mercY(b[1][0]) - mercY(b[0][0]));
    var where = lat.toFixed(4) + "° N, " + lonText(lon);
    if (fx < 0 || fx > 1 || fy < 0 || fy > 1) { out.textContent = where + " is outside this map."; return; }
    var x = Math.min(img.w - 1, Math.floor(fx * img.w)), y = Math.min(img.h - 1, Math.floor(fy * img.h));
    var i = (y * img.w + x) * 4, px = img.d.data, cls = null;
    if (px[i + 3] > 0) a.classes.forEach(function (c) {
      var r = parseInt(c.colour.slice(1, 3), 16), g = parseInt(c.colour.slice(3, 5), 16), bb = parseInt(c.colour.slice(5, 7), 16);
      if (Math.abs(r - px[i]) + Math.abs(g - px[i + 1]) + Math.abs(bb - px[i + 2]) < 12) cls = c;
    });
    out.textContent = "At " + where + ": " + (cls ? "deepest water " + cls.from_m + (cls.to_m ? "–" + cls.to_m : "+") +
      " m (" + (cls.from_m * FT).toFixed(1) + (cls.to_m ? "–" + (cls.to_m * FT).toFixed(1) : "+") + " ft)" :
      "dry, or under " + a.classes[0].from_m + " m") + " in the " + storm(S.storm).name + ".";
  }
  function drawPoints() {
    if (pointLayer) map.removeLayer(pointLayer);
    pointLayer = L.layerGroup().addTo(map);
    D.points.forEach(function (p) {
      if (p.lat == null) return;
      var pk = p.peaks[S.storm];
      L.circleMarker([p.lat, p.lon], { radius: p.id === S.point ? 8 : 6, color: "#fff", weight: 2,
        fillColor: "#0f172a", fillOpacity: 1, keyboard: true, title: p.name })
        .bindTooltip(p.name + ": " + (pk ? qq(pk.q) : "–"))
        .on("click", function () { S.point = p.id; $("pt-pick").value = p.id; chart(); drawPoints(); })
        .addTo(pointLayer);
    });
  }

  /* ── Peaks table and chart ──────────────────────────────────────────── */
  function peaks() {
    var t = $("peaks"); t.innerHTML = "";
    var h = el("tr"); h.appendChild(el("th", { scope: "col" }, "Point"));
    D.storms.forEach(function (s) { h.appendChild(el("th", { scope: "col", "class": "num" }, s.name)); });
    t.appendChild(h);
    D.points.forEach(function (p) {
      var r = el("tr"); r.appendChild(el("th", { scope: "row" }, p.name));
      D.storms.forEach(function (s) {
        var pk = p.peaks[s.id];
        r.appendChild(el("td", { "class": "num" }, pk ? qq(pk.q) + ", " + pk.hours.toFixed(1) + " h" : "–"));
      });
      t.appendChild(r);
    });
    var ffa = D.gauge_frequency, g = gaugePoint(), note = $("ffa-note");
    note.textContent = "Times: hours after the rain starts.";
    if (ffa && g) {
      var bits = D.storms.map(function (s) {
        var obs = ffa.q_m3s[String(s.years)];
        var mod = g.peaks[s.id] ? g.peaks[s.id].q : null;
        return s.years + "-year: model " + (mod == null ? "–" : n0(mod * CFS)) + " cfs, gauge " +
          (obs == null ? "–" : n0(obs * CFS)) + " cfs" + (mod != null && obs ? " (" + (mod / obs * 100).toFixed(0) + " %)" : "");
      });
      note.textContent = "Check against the river: the " + ffa.peaks + " yearly peaks of USGS " + ffa.site +
        " (" + ffa.from + "–" + ffa.to + ", " + ffa.method + ") give a " + bits.join("; ") +
        ". A design storm's flow need not equal the flood of the same return period, but it should be near it. " +
        "Times: hours after the rain starts.";
    }
  }
  function chart() {
    var p = D.points.filter(function (x) { return x.id === S.point; })[0];
    if (!p) return;
    $("pt-name").textContent = p.name;
    var go = S.pts[p.id] ? Promise.resolve(S.pts[p.id]) : getJSON("data/points/" + p.id + ".json")
      .then(function (j) { S.pts[p.id] = j; return j; });
    go.then(function (F) { drawChart(p, F); });
  }
  function drawChart(p, F) {
    var box = $("dchart"); box.innerHTML = "";
    var W = 640, H = 330, L0 = 56, R0 = 46, T0 = 12, B0 = 34, rainH = 70;
    var hs = F.hours, h0 = Math.min.apply(null, hs), h1 = Math.max.apply(null, hs);
    var qmax = 0;
    Object.keys(F.series).forEach(function (k) { F.series[k].forEach(function (v) { if (v > qmax) qmax = v; }); });
    var ffa = D.gauge_frequency, isG = gaugePoint() && gaugePoint().id === p.id;
    if (isG) D.storms.forEach(function (s) { var o = ffa.q_m3s[String(s.years)]; if (o > qmax) qmax = o; });
    qmax = qmax * 1.08 || 1;
    var rmax = 0;
    D.storms.forEach(function (s) { hourly(s).forEach(function (v) { if (v > rmax) rmax = v; }); });
    var X = function (h) { return L0 + (h - h0) / (h1 - h0) * (W - L0 - R0); };
    var Y = function (q) { return H - B0 - q / qmax * (H - B0 - T0 - rainH); };
    var svg = sv("svg", { viewBox: "0 0 " + W + " " + H, role: "presentation" });
    for (var k = 0; k <= 4; k++) {
      var qv = qmax * k / 4;
      svg.appendChild(sv("line", { x1: L0, x2: W - R0, y1: Y(qv), y2: Y(qv), stroke: "#e2e8f0" }));
      svg.appendChild(sv("text", { x: L0 - 6, y: Y(qv) + 4, "text-anchor": "end", "font-size": 11, fill: "#475569" },
        qv >= 100 ? n0(qv) : qv.toFixed(0)));
    }
    svg.appendChild(sv("text", { x: 12, y: T0 + rainH + 10, "font-size": 11, fill: "#475569",
      transform: "rotate(-90 12 " + (T0 + rainH + 10) + ")", "text-anchor": "end" }, "m³/s"));
    for (var hh = Math.ceil(h0 / 6) * 6; hh <= h1; hh += 6) {
      svg.appendChild(sv("text", { x: X(hh), y: H - B0 + 16, "text-anchor": "middle", "font-size": 11, fill: "#475569" }, hh + " h"));
    }
    var nS = D.storms.length, bw = (X(1) - X(0)) / (nS + 0.5);
    D.storms.forEach(function (s, j) {
      hourly(s).forEach(function (v, i) {
        var hx = i;                                   // hour i of the storm (the chart counts from its start)
        var hgt = v / (rmax || 1) * (rainH - 8);
        svg.appendChild(sv("rect", { x: X(hx) + j * bw, y: T0, width: Math.max(1, bw - 0.5), height: hgt,
          fill: colourOf(s.id), opacity: 0.55 }));
      });
    });
    svg.appendChild(sv("text", { x: W - R0 + 4, y: T0 + 10, "font-size": 11, fill: "#475569" }, n0(rmax) + " mm/h"));
    Object.keys(F.series).forEach(function (k) {
      var d = "";
      F.series[k].forEach(function (v, i) { if (v == null) return; d += (d ? "L" : "M") + X(hs[i]).toFixed(1) + " " + Y(v).toFixed(1); });
      svg.appendChild(sv("path", { d: d, fill: "none", stroke: colourOf(k), "stroke-width": 2.2 }));
      if (isG && ffa.q_m3s[String(storm(k).years)]) {
        var o = ffa.q_m3s[String(storm(k).years)];
        svg.appendChild(sv("line", { x1: L0, x2: W - R0, y1: Y(o), y2: Y(o), stroke: colourOf(k), "stroke-dasharray": "6 4" }));
        svg.appendChild(sv("text", { x: W - R0 + 4, y: Y(o) + 4, "font-size": 10, fill: colourOf(k) }, "gauge " + storm(k).years + "-yr"));
      }
    });
    var lx = L0 + 6;
    D.storms.forEach(function (s) {
      svg.appendChild(sv("rect", { x: lx, y: T0 + rainH + 6, width: 12, height: 4, fill: colourOf(s.id) }));
      svg.appendChild(sv("text", { x: lx + 16, y: T0 + rainH + 12, "font-size": 11, fill: "#0f172a" }, s.name));
      lx += 16 + s.name.length * 6.4 + 16;
    });
    box.appendChild(svg);
    var t = el("table", { "class": "srcs" });
    var hr = el("tr"); hr.appendChild(el("th", {}, "Storm"));
    ["Highest flow", "Hours after rain starts"].forEach(function (x) { hr.appendChild(el("th", { "class": "num" }, x)); });
    t.appendChild(hr);
    D.storms.forEach(function (s) {
      var pk = p.peaks[s.id], r = el("tr");
      r.appendChild(el("td", {}, s.name));
      r.appendChild(el("td", { "class": "num" }, pk ? qq(pk.q) : "–"));
      r.appendChild(el("td", { "class": "num" }, pk ? pk.hours.toFixed(1) : "–"));
      t.appendChild(r);
    });
    box.appendChild(t);
  }
  function hourly(s) {
    var per = 60 / D.step_min, out = [];
    for (var i = 0; i < s.blocks.length; i += per) {
      var v = 0; for (var j = i; j < i + per && j < s.blocks.length; j++) v += s.blocks[j]; out.push(v);
    }
    return out;
  }

  /* ── How it was made, downloads ─────────────────────────────────────── */
  function how() {
    var u = $("how"); u.innerHTML = "";
    var a = D.atlas14 || {}, s0 = D.storms[0];
    [
      "Rain: NOAA Atlas 14 Volume " + a.volume + " (version " + a.version + ", " + a.region + "), partial-duration " +
        "depths at " + (+a.lat).toFixed(4) + "° N, " + lonText(+a.lon) + ". Return periods Atlas 14 does not give " +
        "(20 years) are read between its own on the Gumbel scale.",
      "Storm shape: nested (balanced), in " + D.step_min + "-minute blocks: every duration from " + D.step_min +
        " minutes to " + D.duration_hours + " hours holds its Atlas 14 depth; the most intense block falls " +
        Math.round(D.peak_at * 100) + " % of the way through. The same rain everywhere in the basin, no areal reduction.",
      "Start: the forecast's saved state of " + D.state_utc.replace("T", " ").replace("Z", " UTC") +
        " (the soil as wet as it was then), " + D.lead_hours + " h dry, the storm, then " + D.after_hours + " h more.",
      "Model: MRRpy, Green-Ampt infiltration with impervious ground, semi-implicit diffusive-wave routing on " +
        "the USGS 3DEP 1/3 arc-second (10 m) DEM with the NHDPlus creeks burned in.",
      "Flood maps: on the USGS 3DEP 1 m lidar (bare earth). The 2-D boxes run from " + D.pad_hours +
        " h before the flood rises to " + D.pad_hours + " h after it falls, the creeks entering at the box " +
        "edges with the model's flows. Culverts and storm drains are not in the terrain.",
      "Rain totals: " + D.storms.map(function (s) { return s.name + " " + s.depth_mm.toFixed(0) + " mm (" +
        s.depth_in.toFixed(2) + " in)"; }).join(", ") + "."
    ].forEach(function (x) { u.appendChild(el("li", {}, x)); });
    var p = $("downloads"); p.innerHTML = ""; p.appendChild(document.createTextNode("Download: "));
    var links = [];
    D.points.forEach(function (x) { links.push(["data/points/" + x.id + ".csv", x.name + " flows (CSV)"]); });
    D.areas.forEach(function (x) { if (x.ok) links.push([x.tif, storm(x.storm).name + ", " + x.area + " depth (GeoTIFF, 1 m)"]); });
    links.forEach(function (l, i) {
      if (i) p.appendChild(document.createTextNode(" · "));
      p.appendChild(el("a", { href: l[0], download: "" }, l[1]));
    });
  }

  function render() {
    pickers(); words(); showArea(false); drawPoints(); peaks(); chart();
  }
  getJSON("data/design.json").then(function (d) {
    D = d;
    document.title = "Design storms · " + D.name.replace(/ flood forecast.*/, "");
    S.storm = D.storms[0].id;
    var g = gaugePoint();
    S.point = g ? g.id : (D.points[0] || {}).id;
    var pp = $("pt-pick");
    D.points.forEach(function (p) { pp.appendChild(el("option", { value: p.id }, p.name)); });
    pp.value = S.point;
    pp.addEventListener("change", function () { S.point = this.value; chart(); drawPoints(); });
    initMap(); render(); how();
    var a = area(S.area); if (a.ok) map.fitBounds(a.bounds, { maxZoom: 15 });
  }).catch(function (e) { $("lead").textContent = "The design storms have not been run yet (" + e.message + ")."; });
})();
