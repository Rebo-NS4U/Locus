const map = L.map('map', {worldCopyJump: true, doubleClickZoom: false}).setView([40.7128, -74.006], 12);
const tiles = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
}).addTo(map);
tiles.on('tileerror', () => { document.getElementById('notice').hidden = false; });
let acknowledged, routeLine, bridge, stopMarkers = [], legLines = [], follow = true;
let palette = {accent: '#ffb77e', text: '#efdfd6', bg: '#423429'};
window.setTheme = colors => {
  palette = colors;
  for (const [key, value] of Object.entries(colors)) document.documentElement.style.setProperty('--' + key, value);
  if (routeLine) routeLine.eachLayer(layer => { if (layer.setStyle) layer.setStyle({color: layer.options.segmentColor || palette.accent}); });
};
window.setOptions = (night, shouldFollow) => {
  document.body.classList.toggle('night', night);
  follow = shouldFollow;
};
window.focusLocation = (lat, lon) => {
  map.panTo([lat, lon]);
};
window.setAcknowledged = (point, connected) => {
  if (!point) {
    if (acknowledged) acknowledged.remove();
    acknowledged = null;
    return;
  }
  if (!acknowledged) acknowledged = L.circleMarker(point, {radius: 6, weight: 2, color: '#fff', fillColor: '#84d5c4', fillOpacity: 1}).addTo(map);
  acknowledged.setLatLng(point).setStyle({opacity: connected ? 1 : 0.4, fillOpacity: connected ? 1 : 0.4});
  acknowledged.bindTooltip(connected ? 'Last acknowledged location' : 'Last acknowledged location · connection lost');
  if (follow && connected) map.panTo(point, {animate: true, duration: 0.3});
};
// Playback highlighting lives in the route editor; keep the bridge API stable.
window.setActiveLeg = () => {};
window.setRoute = (points, stops, segments = [], selected = -1) => {
  if (routeLine) routeLine.remove();
  routeLine = L.featureGroup().addTo(map);
  legLines = [];
  const pieces = segments.length ? segments : [{points: points.length ? points : stops, ready: Boolean(points.length), color: palette.accent, controls: [], label: 'Route'}];
  pieces.forEach((segment, index) => {
    const line = L.polyline(segment.points, {segmentColor: segment.color, color: segment.color, weight: 4, opacity: segment.ready ? 0.9 : 0.5, dashArray: segment.ready ? null : '6,8', bubblingMouseEvents: false}).addTo(routeLine);
    line.options.baseWeight = 4;
    line.options.baseOpacity = segment.ready ? .9 : .5;
    legLines.push(line);
    line.bindTooltip(`${segment.from_stop || index + 1} → ${segment.to_stop || index + 2} · ${segment.label}${segment.ready ? '' : ' · needs planning'}`);
    if (segments.length) line.on('click', () => { if (bridge) bridge.selectLeg(index); });
    (segment.connectors || []).forEach(connector => L.polyline(connector, {segmentColor: '#efdfd6', color: '#efdfd6', weight: 4, dashArray: '3,5'}).bindTooltip('Direct connector from pin to mapped path').addTo(routeLine));
    (segment.stops || []).forEach(stop => {
      const light = stop.kind === 'traffic_light';
      const label = light ? 'Mapped traffic light · simulated queue/red phase' : stop.kind === 'crosswalk' ? 'Mapped crosswalk · occasional pedestrian yield' : 'Mapped stop sign · simulated 2–5 second stop';
      const html = light
        ? '<span class="traffic-light-sign" aria-label="Traffic light"><i></i><i></i><i></i></span>'
        : stop.kind === 'crosswalk' ? '<span class="crosswalk-sign" aria-label="Crosswalk">↔</span>' : '<span class="traffic-stop-sign" aria-label="Stop sign">STOP</span>';
      L.marker(stop.point, {title: label, zIndexOffset: 500,
        icon: L.divIcon({className: 'traffic-control', html, iconSize: [44, 44], iconAnchor: [22, 22]})})
        .bindTooltip(label).addTo(routeLine);
    });

  });
  stopMarkers.forEach(item => item.remove());
  stopMarkers = stops.map((point, index) => {
    const stop = L.marker(point, {draggable: true, icon: L.divIcon({className: 'route-stop', html: String(index + 1), iconSize: [24, 24], iconAnchor: [12, 12]})}).addTo(map);
    stop.on('dragend', () => {
      const moved = stop.getLatLng().wrap();
      if (bridge) bridge.moveStop(index, moved.lat, moved.lng);
    });
    return stop;
  });
};
window.fitRoute = () => {
  if (routeLine && routeLine.getBounds().isValid()) map.fitBounds(routeLine.getBounds(), {padding: [35, 35]});
};
new QWebChannel(qt.webChannelTransport, channel => {
  bridge = channel.objects.locus;
  map.on('click', event => {
    // Ignore the second click of a native double-click.
    if (event.originalEvent && event.originalEvent.detail > 1) return;
    const point = event.latlng.wrap();
    channel.objects.locus.pick(point.lat, point.lng);
  });
});

window.setTypography = (family, size) => {
  document.documentElement.style.setProperty('--font-family', JSON.stringify(family));
  document.documentElement.style.setProperty('--font-size', size + 'pt');
};
