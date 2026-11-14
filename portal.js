/* Front page of several flood forecasts (places.py writes places.json): a warnings
   strip for every place, a card per place and a map of their basins. */
(function () {
  "use strict";
  var LEVEL = { danger: "Above danger level", warning: "Above warning level",
                normal: "Below warning level" };
  var RANK = { danger: 3, warning: 2, normal: 1 };

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
  function fmt(ms, tz, brief) {
    var o = brief ? { weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false } :
                    { weekday: "short", day: "numeric", month: "short", hour: "2-digit",
                      minute: "2-digit", hour12: false };
    o.timeZone = tz || "UTC";
    try { return new Intl.DateTimeFormat("en-GB", o).format(new Date(ms)).replace(",", ""); }
    catch (e) { return new Date(ms).toISOString().slice(0, 16); }
  }
  function nameOf(c) {                      // 'Roshi Khola flood forecast (experimental)' → 'Roshi Khola'
    return c.short || c.name.replace(/\s*\([^)]*\)\s*$/, "").replace(/\s+flood\s+forecasts?$/i, "") || c.name;
  }
  function q(x) {
    if (x === null || x === undefined) return "–";
    return x >= 100 ? Math.round(x).toLocaleString("en-GB") : x >= 10 ? x.toFixed(1) : x.toFixed(x >= 1 ? 1 : 2);
  }
  function shape(level, size) {
    var s = size || 12, g = sv("svg", { width: s, height: s, viewBox: "0 0 12 12", "aria-hidden": "true" });
    var fill = level === "danger" ? "var(--danger)" : level === "warning" ? "var(--warning)" :
               level === "normal" ? "var(--normal)" : "var(--muted)";
    var m = level === "danger" ? sv("path", { d: "M6 0.6 11.4 6 6 11.4 0.6 6Z" }) :
            level === "warning" ? sv("path", { d: "M6 0.8 11.5 11 0.5 11Z" }) :
            sv("circle", { cx: 6, cy: 6, r: 4.6 });
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
  function page(c) { return c.id + "/index.html"; }
  // places.json written by an older lynch-forecast has no 'worst' (any forecast's
  // level) nor counts: fall back to the main forecast's level.
  function upgrade(c) {
    (c.points || []).forEach(function (p) { if (p.worst === undefined) p.worst = p.level || null; });
    if (c.has_forecast && c.worst === undefined) {
      c.worst = (c.points || []).reduce(function (w, p) {
        return (RANK[p.worst] || 0) > (RANK[w] || 0) ? p.worst : w; }, null);
    }
    return c;
  }

  function alerts(places) {
    var box = $("alerts"), any = false;
    box.innerHTML = "";
    places.forEach(function (c) {
      (c.points || []).forEach(function (p) {
        if (p.worst !== "warning" && p.worst !== "danger") return;
        any = true;
        var a = el("a", { "class": "alert " + p.worst, href: page(c) + (p.id ? "#" + p.id.replace(/_/g, "-") : "") });
        a.style.textDecoration = "none"; a.style.color = "inherit";
        a.appendChild(shape(p.worst, 16));
        var w = el("span");
        w.appendChild(el("span", { "class": "what" }, nameOf(c) + ", " + p.name + ": " +
                                                     (p.worst === "danger" ? "danger" : "warning")));
        w.appendChild(el("span", { "class": "why" }, (p.n_forecasts ? " · " + p.n_above_warning + " of " +
          p.n_forecasts + " forecasts above warning" : "") + " · up to " + q(p.high_q) + " m³/s"));
        a.appendChild(w);
        box.appendChild(a);
      });
    });
    if (!any) {
      var c = el("div", { "class": "alert calm" });
      c.appendChild(shape("normal", 16));
      c.appendChild(el("span", { "class": "what" }, "No forecast reaches a warning level at any place."));
      box.appendChild(c);
    }
  }

  function card(c) {
    var box = el("article", { "class": "panel place", "aria-labelledby": "place-" + c.id });
    var head = el("div", { "class": "panel-head" });
    var h = el("h3", { id: "place-" + c.id }); h.appendChild(el("a", { href: page(c) }, nameOf(c)));
    head.appendChild(h);
    if (c.has_forecast) head.appendChild(badge(c.worst, c.worst ? undefined : "No levels set"));
    box.appendChild(head);
    if (!c.has_forecast) {
      box.appendChild(el("p", { "class": "meta" }, "No forecast yet: the first update has not run."));
      return box;
    }
    var tz = c.timezone || "UTC";
    var meta = el("p", { "class": "meta" });
    if (c.late) meta.appendChild(el("span", { "class": "pill late" }, "Late"));
    meta.appendChild(document.createTextNode(" Updated " + fmt(T(c.cycle_utc), tz) + " · rain " +
      (c.rain_next_mm != null ? Math.round(c.rain_next_mm) + " mm" : "–") + " expected in " +
      c.forecast_hours + " h (" + c.main + ")" +
      (c.flooded_km2 != null ? " · " + c.flooded_km2.toFixed(1) + " km² under water on the basin map" : "") +
      (c.stations ? " · " + c.stations_ok + " of " + c.stations + " rain stations read" : "")));
    box.appendChild(meta);
    var t = el("table"), hr = el("tr");
    t.appendChild(el("caption", { "class": "vh" }, "Highest river flow expected at each point of " + c.name));
    ["Point", "Now", "Highest (" + c.main + ")", "Range of forecasts", "Level"].forEach(function (x, i) {
      hr.appendChild(el("th", { scope: "col", "class": i && i < 4 ? "num" : "" }, x)); });
    t.appendChild(hr);
    c.points.forEach(function (p) {
      var r = el("tr");
      var th = el("th", { scope: "row" });
      th.appendChild(el("a", { href: page(c) + (p.id ? "#" + p.id.replace(/_/g, "-") : "") }, p.name));
      r.appendChild(th);
      r.appendChild(el("td", { "class": "num" }, q(p.now_q)));
      r.appendChild(el("td", { "class": "num nowrap" }, q(p.main_q) + (p.main_utc ? " · " + fmt(T(p.main_utc), tz, true) : "")));
      r.appendChild(el("td", { "class": "num nowrap" }, p.low_q == null ? "–" : q(p.low_q) + " to " + q(p.high_q)));
      var lv = el("td");
      lv.appendChild(badge(p.worst, p.has_levels ? (p.worst ? undefined : "–") : "No levels"));
      if (p.has_levels && p.n_above_warning) lv.appendChild(el("span", { "class": "meta" },
        " " + p.n_above_warning + " of " + p.n_forecasts));
      r.appendChild(lv);
      t.appendChild(r);
    });
    var wrap = el("div", { "class": "tablewrap" }); wrap.appendChild(t); box.appendChild(wrap);
    var more = el("p", { "class": "open-row" });
    var open = el("a", { "class": "open", href: page(c) });
    open.appendChild(el("span", { "class": "big" }, "Open the " + nameOf(c) + " forecast →"));
    open.appendChild(el("span", { "class": "small" }, "map, rivers, flood maps, charts"));
    more.appendChild(open);
    box.appendChild(more);
    return box;
  }

  var BASES = {
    streets: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png", 19, "© OpenStreetMap contributors"],
    terrain: ["https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", 17, "© OpenStreetMap contributors, SRTM · style © OpenTopoMap (CC-BY-SA)"],
    satellite: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", 19,
                "Imagery © Esri, Maxar, Earthstar Geographics"]
  };
  // Basins can lie inside each other (a gauge's basin inside the whole river's):
  // the smaller ones are drawn on top, and a click inside several asks which.
  function inRing(x, y, ring) {
    var inside = false;
    for (var i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      var xi = ring[i][0], yi = ring[i][1], xj = ring[j][0], yj = ring[j][1];
      if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  }
  function inGeo(g, lon, lat) {
    var polys = [];
    (g.features || [g]).forEach(function (f) {
      var gm = f.geometry || f;
      if (gm.type === "Polygon") polys.push(gm.coordinates);
      if (gm.type === "MultiPolygon") polys = polys.concat(gm.coordinates);
    });
    return polys.some(function (p) {
      return inRing(lon, lat, p[0]) && !p.slice(1).some(function (h) { return inRing(lon, lat, h); });
    });
  }
  function areaOf(layer) {
    var b = layer.getBounds();
    return (b.getEast() - b.getWest()) * (b.getNorth() - b.getSouth());
  }
  function drawMap(places) {
    if (typeof L === "undefined") { $("map").hidden = true; return; }
    var map = L.map("map", { zoomSnap: 0.25, zoomDelta: 0.5, wheelPxPerZoomLevel: 90,
                             scrollWheelZoom: true }), base;
    window.addEventListener("resize", function () { map.invalidateSize(); });
    function setBase(n) { if (base) map.removeLayer(base); var b = BASES[n];
      base = L.tileLayer(b[0], { maxZoom: b[1], attribution: b[2] }).addTo(map); base.bringToBack(); }
    setBase("streets");
    $("base-pick").addEventListener("change", function () { setBase(this.value); });
    var all = L.featureGroup().addTo(map);
    var ink = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim();
    var basins = [];
    var done = places.filter(function (c) { return c.has_forecast; }).map(function (c) {
      return getJSON(c.id + "/data/basin.geojson").then(function (g) {
        var layer = L.geoJSON(g, { style: { color: ink, weight: 2, fillOpacity: 0.12 } });
        var tag = el("span"); tag.appendChild(shape(c.worst, 12));
        tag.appendChild(document.createTextNode(" " + nameOf(c)));
        layer.bindTooltip(tag.innerHTML, { permanent: true, direction: "center", className: "place-label" });
        layer.on("mouseover", function () { layer.setStyle({ weight: 4, fillOpacity: 0.22 }); });
        layer.on("mouseout", function () { layer.setStyle({ weight: 2, fillOpacity: 0.12 }); });
        layer.on("click", function (e) {
          L.DomEvent.stop(e);
          var here = basins.filter(function (b) { return inGeo(b.g, e.latlng.lng, e.latlng.lat); });
          if (here.length <= 1) { window.location.href = page((here[0] || { c: c }).c); return; }
          var box = el("div", { "class": "pick" });
          box.appendChild(el("p", {}, "Which forecast?"));
          here.forEach(function (b) {                       // smallest first
            var a = el("a", { "class": "open", href: page(b.c) });
            a.appendChild(el("span", { "class": "big" }, nameOf(b.c) + " →"));
            box.appendChild(a);
          });
          L.popup({ maxWidth: 280 }).setLatLng(e.latlng).setContent(box).openOn(map);
        });
        basins.push({ c: c, g: g, layer: layer, area: areaOf(layer) });
      }).catch(function () {});
    });
    Promise.all(done).then(function () {
      basins.sort(function (a, b) { return a.area - b.area; });
      basins.slice().reverse().forEach(function (b) { b.layer.addTo(all); });   // largest at the bottom
      if (all.getLayers().length) map.fitBounds(all.getBounds(), { padding: [30, 30], maxZoom: 11 });
      else map.setView([28.2, 84.1], 6.5);
    });
  }

  getJSON("places.json").then(function (d) {
    document.title = d.title; $("title").textContent = d.title;
    if (d.notice) $("notice").appendChild(document.createTextNode(" " + d.notice));
    var n = d.places.length, tz = n ? d.places[0].timezone : "UTC";
    $("updated").textContent = n + (n === 1 ? " place" : " places") + " · page made " + fmt(T(d.built_utc), tz);
    d.places.forEach(upgrade);
    var sorted = d.places.slice().sort(function (a, b) { return (RANK[b.worst] || 0) - (RANK[a.worst] || 0); });
    alerts(sorted);
    var list = $("places");
    sorted.forEach(function (c) { list.appendChild(card(c)); });
    drawMap(sorted);
  }).catch(function () {
    $("updated").textContent = "No places have been published here yet.";
  });
})();
