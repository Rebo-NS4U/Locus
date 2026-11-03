const map = L.map('map', {worldCopyJump: true}).setView([40.7128, -74.006], 12);
const tiles = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
}).addTo(map);
tiles.on('tileerror', () => { document.getElementById('notice').hidden = false; });
let marker;
window.setPin = (lat, lon) => {
  if (marker) marker.remove();
  marker = L.circleMarker([lat, lon], {radius: 9, color: '#7256dd', fillOpacity: 0.8}).addTo(map);
  map.panTo([lat, lon]);
};
new QWebChannel(qt.webChannelTransport, channel => {
  map.on('click', event => {
    const point = event.latlng.wrap();
    setPin(point.lat, point.lng);
    channel.objects.locus.pick(point.lat, point.lng);
  });
});
