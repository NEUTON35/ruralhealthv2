(function () {
  const fallbackCenter = [4.5709, -74.2973];

  function numberValue(input) {
    if (!input || input.value === '') return null;
    const value = Number(input.value);
    return Number.isFinite(value) ? value : null;
  }

  function setupPicker(container) {
    if (!window.L || container.dataset.ready === '1') return;
    const latInput = document.querySelector(container.dataset.latInput);
    const lngInput = document.querySelector(container.dataset.lngInput);
    if (!latInput || !lngInput) return;

    const lat = numberValue(latInput);
    const lng = numberValue(lngInput);
    const hasPoint = lat !== null && lng !== null;
    const center = hasPoint ? [lat, lng] : fallbackCenter;
    const zoom = hasPoint ? 15 : 6;
    const map = L.map(container, { scrollWheelZoom: true }).setView(center, zoom);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; OpenStreetMap'
    }).addTo(map);

    const marker = L.marker(center, { draggable: true }).addTo(map);

    function writePoint(point) {
      latInput.value = point.lat.toFixed(6);
      lngInput.value = point.lng.toFixed(6);
    }

    if (!hasPoint) {
      writePoint(marker.getLatLng());
    }

    marker.on('dragend', () => writePoint(marker.getLatLng()));
    map.on('click', (event) => {
      marker.setLatLng(event.latlng);
      writePoint(event.latlng);
    });

    const gpsButton = container.closest('form')?.querySelector('[data-use-browser-location]');
    if (gpsButton && navigator.geolocation) {
      gpsButton.addEventListener('click', () => {
        navigator.geolocation.getCurrentPosition((position) => {
          const point = {
            lat: position.coords.latitude,
            lng: position.coords.longitude
          };
          marker.setLatLng(point);
          map.setView(point, 16);
          writePoint(point);
        }, () => alert('No fue posible obtener la ubicacion.'));
      });
    }

    container.dataset.ready = '1';
    window.setTimeout(() => map.invalidateSize(), 150);
  }

  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('[data-location-picker]').forEach(setupPicker);
  });
})();
