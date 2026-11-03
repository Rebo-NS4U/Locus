const map = L.map('map', {worldCopyJump: true}).setView([40.7128, -74.006], 12);
const tiles = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
}).addTo(map);
tiles.on('tileerror', () => { document.getElementById('notice').hidden = false; });
let marker, acknowledged, routeLine, bridge, stopMarkers = [], follow = true;
let palette = {accent: '#ffb77e', text: '#efdfd6', bg: '#423429'};
window.setTheme = colors => {
  palette = colors;
  for (const [key, value] of Object.entries(colors)) document.documentElement.style.setProperty('--' + key, value);
  if (marker) marker.setStyle({color: palette.accent});
  if (routeLine) routeLine.setStyle({color: palette.accent});
};
window.setOptions = (night, shouldFollow) => {
  document.body.classList.toggle('night', night);
  follow = shouldFollow;
};
window.setPin = (lat, lon) => {
  if (marker) marker.remove();
  marker = L.circleMarker([lat, lon], {radius: 10, color: palette.accent, weight: 3, fillOpacity: 0.15}).addTo(map);
  marker.bindTooltip('Selected pin · preview');
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
window.setRoute = (points, stops) => {
  if (routeLine) routeLine.remove();
  routeLine = L.polyline(points.length ? points : stops, {color: palette.accent, weight: 4, opacity: points.length ? 0.9 : 0.45, dashArray: points.length ? null : '6,8'}).addTo(map);
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
  if (routeLine && routeLine.getLatLngs().length > 1) map.fitBounds(routeLine.getBounds(), {padding: [35, 35]});
};
new QWebChannel(qt.webChannelTransport, channel => {
  bridge = channel.objects.locus;
  map.on('click', event => {
    const point = event.latlng.wrap();
    setPin(point.lat, point.lng);
    channel.objects.locus.pick(point.lat, point.lng);
  });
});
