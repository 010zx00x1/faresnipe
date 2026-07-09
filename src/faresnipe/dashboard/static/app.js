'use strict';

const GOOGLE_FLIGHTS_URL = 'https://www.google.com/travel/flights';

const DESTINATION_NAMES = {
  AEP: 'Buenos Aires',
  ANF: 'Antofagasta',
  ARI: 'Arica',
  ASU: 'Asuncion',
  ATL: 'Atlanta',
  BBA: 'Balmaceda',
  BOG: 'Bogota',
  CCP: 'Concepcion',
  CDG: 'Paris',
  CJC: 'Calama',
  COR: 'Cordoba',
  CUN: 'Cancun',
  DFW: 'Dallas',
  EZE: 'Buenos Aires',
  GIG: 'Rio de Janeiro',
  GRU: 'Sao Paulo',
  GYE: 'Guayaquil',
  IPC: 'Isla de Pascua',
  IQQ: 'Iquique',
  JFK: 'Nueva York',
  LIM: 'Lima',
  LSC: 'La Serena',
  MAD: 'Madrid',
  MDZ: 'Mendoza',
  MEX: 'Ciudad de Mexico',
  MIA: 'Miami',
  MVD: 'Montevideo',
  PMC: 'Puerto Montt',
  PTY: 'Panama',
  AEP: 'Buenos Aires',
  RGL: 'Rio Gallegos',
  SCL: 'Santiago',
  UIO: 'Quito',
  YYZ: 'Toronto',
  ZCO: 'Temuco',
};

const state = {
  summary: null,
  routes: [],
  opportunities: [],
  config: null,
  compareRows: [],
};

const $ = (id) => document.getElementById(id);

async function api(path, options) {
  const res = await fetch(path, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function isMock(row) {
  return row && (row.provider === 'mock' || row.provider_label === 'Mock');
}

function routeKey(row) {
  return `${row.origin}-${row.destination}`;
}

function destinationLabel(code) {
  const name = DESTINATION_NAMES[code];
  return name ? `${name} (${code})` : code;
}

function formatPrice(value, currency = 'CLP') {
  const n = Number(value);
  if (!Number.isFinite(n)) return 'Sin precio';
  return `$${Math.round(n).toLocaleString('es-CL')} ${currency || 'CLP'}`.trim();
}

function parseDate(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function sameLocalDay(a, b) {
  return a.getFullYear() === b.getFullYear()
    && a.getMonth() === b.getMonth()
    && a.getDate() === b.getDate();
}

function formatTime(date) {
  return date.toLocaleTimeString('es-CL', { hour: '2-digit', minute: '2-digit' });
}

function formatRelative(isoDate) {
  const date = parseDate(isoDate);
  if (!date) return 'Sin datos aún';
  const now = new Date();
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (sameLocalDay(date, now)) return `hoy ${formatTime(date)}`;
  if (sameLocalDay(date, yesterday)) return `ayer ${formatTime(date)}`;
  const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const startDate = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  const days = Math.max(1, Math.round((startToday - startDate) / 86400000));
  return `hace ${days} días ${formatTime(date)}`;
}

function quoteType(row) {
  if (!row || !row.has_price) return '';
  return row.quote_type || (row.status_kind === 'mistake' ? 'mistake_fare' : row.status_kind === 'deal' ? 'deal' : 'baseline');
}

function quoteTypeLabel(type) {
  return {
    mistake_fare: 'Mistake fare',
    deal: 'Deal',
    baseline: 'Baseline',
  }[type] || 'Baseline';
}

function quoteBadge(row) {
  const type = quoteType(row);
  if (!type) return '';
  return `<span class="quote-badge ${type}">${quoteTypeLabel(type)}</span>`;
}

function minutesSince(isoDate) {
  const date = parseDate(isoDate);
  if (!date) return null;
  return Math.max(0, Math.floor((Date.now() - date.getTime()) / 60000));
}

function lastScanStartedAt(summary) {
  return summary && summary.last_scan
    ? (summary.last_scan.started_at || summary.last_scan.updated_at || summary.latest_observed_at)
    : summary.latest_observed_at;
}

function statusFromSummary(summary) {
  const mode = String(summary.scan_mode || '').toLowerCase();
  if (mode.includes('inactive') || mode.includes('apagado') || mode.includes('off')) return 'off';
  const startedAt = lastScanStartedAt(summary);
  const date = parseDate(startedAt);
  if (!date) return 'yesterday';
  const mins = minutesSince(startedAt);
  if (summary.scan_running || (mins !== null && mins < 15)) return 'running';
  if (sameLocalDay(date, new Date())) return 'today';
  return 'yesterday';
}

function renderHeader(summary) {
  const status = statusFromSummary(summary);
  const startedAt = lastScanStartedAt(summary);
  const date = parseDate(startedAt);
  let text = '🟠 Vigía sin escaneo hoy · último: sin datos';

  if (status === 'off') {
    text = '🔴 Vigía apagado · systemctl start faresnipe';
  } else if (status === 'running') {
    const mins = minutesSince(startedAt);
    text = `🟢 Vigía activo · último escaneo hace ${mins === null ? 0 : mins} min`;
  } else if (status === 'today' && date) {
    text = `🟡 Vigía activo · último escaneo hoy ${formatTime(date)}`;
  } else if (date) {
    text = `🟠 Vigía sin escaneo hoy · último: ${formatRelative(startedAt)}`;
  }

  $('statusSubtitle').textContent = text;
}

function configuredOrigins() {
  if (!state.config) return [];
  if (Array.isArray(state.config.origins) && state.config.origins.length) {
    return state.config.origins.filter((origin) => origin.enabled);
  }
  const codes = (state.config.routes || [])
    .filter((route) => route.enabled)
    .map((route) => route.origin)
    .filter(Boolean);
  return [...new Set(codes)].sort().map((code) => ({
    code,
    name: code,
    route_count: configuredRouteCount(code),
    destinations: [],
    enabled: true,
  }));
}

function configuredRouteCount(origin) {
  if (!state.config || !Array.isArray(state.config.routes)) return 0;
  return state.config.routes.filter((route) => route.enabled && route.origin === origin).length;
}

function latestObservedForOrigin(origin) {
  const dates = state.routes
    .filter((route) => route.origin === origin && route.has_price && !isMock(route))
    .map((route) => parseDate(route.observed_at))
    .filter(Boolean)
    .sort((a, b) => b - a);
  return dates[0] || null;
}

function renderOriginSummary() {
  const origins = configuredOrigins();
  $('originSummary').innerHTML = origins.map((origin) => {
    const code = origin.code || origin;
    const count = Number(origin.route_count || configuredRouteCount(code));
    const latest = latestObservedForOrigin(code);
    return `
      <article class="origin-card">
        <h2>Salidas desde ${origin.name || code}</h2>
        <p class="airport-code">${code}</p>
        <p class="route-count">${count} ruta${count === 1 ? '' : 's'} vigilada${count === 1 ? '' : 's'}</p>
        <p><span>Último precio visto:</span> ${latest ? formatRelative(latest.toISOString()) : 'Sin datos aún'}</p>
        <p><span>Moneda:</span> ${state.config.scanner.currency || 'CLP'}</p>
      </article>`;
  }).join('');
}

function renderDealBanner() {
  const deal = state.opportunities[0];
  if (!deal || (deal.status_kind !== 'deal' && deal.status_kind !== 'mistake')) {
    $('dealBanner').className = 'deal-banner no-deal';
    $('dealBanner').innerHTML = `
      <h2>No hay ofertas hoy.</h2>
      <p>Con un solo día de datos no se puede calcular la mediana histórica. El vigía necesita correr varios días para empezar a detectar gangas.</p>`;
    return;
  }

  const kind = deal.status_kind === 'mistake' ? 'mistake' : 'deal';
  const pct = deal.discount_pct || (deal.discount_ratio ? `-${Math.round(Number(deal.discount_ratio) * 100)}%` : '');
  const bookingUrl = deal.booking_url || GOOGLE_FLIGHTS_URL;
  $('dealBanner').className = `deal-banner ${kind}`;
  $('dealBanner').innerHTML = `
    <div class="deal-copy">
      <p class="eyebrow">Ganga del día</p>
      <h2>${deal.origin} → ${destinationLabel(deal.destination)}</h2>
      <div class="deal-price">${formatPrice(deal.price, deal.currency)}</div>
      ${deal.median_price ? `<p>antes ~${formatPrice(deal.median_price, deal.currency)} (mediana histórica)</p>` : ''}
      ${pct ? `<p class="discount">${pct} más barato</p>` : ''}
      ${quoteBadge(deal)}
      <p class="detected">Detectado a las ${parseDate(deal.observed_at) ? formatTime(parseDate(deal.observed_at)) : '--:--'}</p>
      <p class="deal-rule">Ganga del día = mayor descuento vs. mediana histórica.</p>
    </div>
    <a class="primary-action" href="${bookingUrl}" target="_blank" rel="noreferrer">Abrir en Google Flights</a>`;
}

function pricedRoutes() {
  return state.routes
    .filter((row) => !isMock(row))
    .filter((row) => row.has_price && Number.isFinite(Number(row.price)))
    .sort((a, b) => Number(a.price) - Number(b.price));
}

function renderLowestPrices() {
  const rows = pricedRoutes();
  if (rows.length < 3) {
    $('lowestPrices').innerHTML = `<div class="empty">Solo ${rows.length} ruta${rows.length === 1 ? '' : 's'} con precio. Necesitamos más días de escaneo.</div>`;
    return;
  }

  $('lowestPrices').innerHTML = rows.slice(0, 8).map((row) => `
    <div class="price-row">
      <div class="route">${row.origin} → ${row.destination}</div>
      <div class="price">${formatPrice(row.price, row.currency)}</div>
      <div class="seen">${quoteBadge(row)} visto ${formatRelative(row.observed_at)}</div>
      <a href="${row.booking_url || GOOGLE_FLIGHTS_URL}" target="_blank" rel="noreferrer">Abrir ↗</a>
    </div>`).join('');
}

function routeState(row) {
  if (!row.has_price && Number(row.route_samples || 0) > 0) return 'Vigilar';
  if (row.has_price) return 'Visto';
  return 'Sin escanear';
}

function renderRoutesTable() {
  const rows = state.routes.filter((row) => !isMock(row));
  $('routesToggle').textContent = `Ver las ${rows.length} rutas que vigila el bot`;
  const rowsByOrigin = rows.reduce((acc, row) => {
    (acc[row.origin] ||= []).push(row);
    return acc;
  }, {});
  const origins = configuredOrigins();
  $('routeGroups').innerHTML = origins.map((origin, index) => {
    const code = origin.code || origin;
    const originRows = (rowsByOrigin[code] || []).sort((a, b) => a.destination.localeCompare(b.destination));
    const withData = originRows.filter((row) => row.has_price).length;
    return `
      <details class="route-origin" ${index === 0 ? 'open' : ''}>
        <summary>
          <span>${origin.name || code} <strong>${code}</strong></span>
          <span>${originRows.length} rutas · ${withData} con datos</span>
        </summary>
        <table>
          <thead>
            <tr>
              <th>Ruta</th>
              <th>Último precio</th>
              <th>Visto</th>
              <th>Estado</th>
            </tr>
          </thead>
          <tbody>
            ${originRows.map((row) => `
              <tr>
                <td>${row.origin} → ${destinationLabel(row.destination)}</td>
                <td>${row.has_price ? formatPrice(row.price, row.currency) : 'Sin precio'}</td>
                <td>${row.observed_at ? formatRelative(row.observed_at) : 'Sin datos aún'}</td>
                <td>${row.has_price ? `${quoteBadge(row)} ${routeState(row)}` : routeState(row)}</td>
              </tr>`).join('')}
          </tbody>
        </table>
      </details>`;
  }).join('');
}

function renderCompareResults() {
  const rows = state.compareRows || [];
  if (!rows.length) {
    $('compareResults').innerHTML = '<div class="empty">Sin comparación cargada.</div>';
    return;
  }
  $('compareResults').innerHTML = `
    <table>
      <thead>
        <tr>
          <th>Origen</th>
          <th>Mejor precio</th>
          <th>Aerolínea</th>
          <th>Muestras</th>
          <th>Link</th>
        </tr>
      </thead>
      <tbody>
        ${rows.map((row) => `
          <tr>
            <td>${row.origin_name || row.origin} (${row.origin})</td>
            <td>${row.cheapest_price ? formatPrice(row.cheapest_price, row.currency) : 'Sin datos'}</td>
            <td>${row.cheapest_carrier || '-'}</td>
            <td>${row.samples || 0}</td>
            <td>${row.cheapest_booking_url ? `<a href="${row.cheapest_booking_url}" target="_blank" rel="noreferrer">Abrir ↗</a>` : '-'}</td>
          </tr>`).join('')}
      </tbody>
    </table>`;
}

async function compareOrigins() {
  const destination = $('compareDestination').value.trim().toUpperCase();
  if (!destination) {
    $('compareResults').innerHTML = '<div class="empty">Ingresa un destino.</div>';
    return;
  }
  $('compareDestination').value = destination;
  const params = new URLSearchParams({ destination });
  if ($('compareDeparture').value) params.set('departure_date', $('compareDeparture').value);
  if ($('compareReturn').value) params.set('return_date', $('compareReturn').value);
  const data = await api(`/api/compare-origins?${params.toString()}`);
  state.compareRows = data.rows || [];
  renderCompareResults();
}

async function loadSummary() {
  const data = await api('/api/summary');
  state.summary = data;
  renderHeader(data);
}

async function loadConfig() {
  state.config = await api('/api/config');
}

async function loadRoutes() {
  const data = await api('/api/routes');
  state.routes = (data.rows || []).filter((row) => !isMock(row));
}

async function loadOpportunities() {
  const data = await api('/api/opportunities');
  state.opportunities = (data.rows || []).filter((row) => !isMock(row));
}

async function loadHistory(origin, destination) {
  const data = await api(`/api/history?origin=${encodeURIComponent(origin)}&destination=${encodeURIComponent(destination)}&limit=80`);
  return (data.rows || []).filter((row) => !isMock(row));
}

function renderAll() {
  renderHeader(state.summary);
  renderOriginSummary();
  renderDealBanner();
  renderLowestPrices();
  renderRoutesTable();
  renderCompareResults();
}

function setPageStatus(message, kind = '') {
  $('pageStatus').textContent = message || '';
  $('pageStatus').className = `page-status ${kind}`;
}

async function refreshAll() {
  setPageStatus('Actualizando...');
  await Promise.all([loadSummary(), loadConfig(), loadRoutes(), loadOpportunities()]);
  renderAll();
  setPageStatus('');
}

async function scanNow() {
  const button = $('scanBtn');
  button.disabled = true;
  button.textContent = 'Escaneando...';
  setPageStatus('Escaneando...');
  try {
    await api('/api/scan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ limit_searches: 1 }),
    });
    await refreshAll();
    setPageStatus('');
  } finally {
    button.disabled = false;
    button.textContent = 'Escanear ahora';
  }
}

$('refreshBtn').addEventListener('click', () => {
  refreshAll().catch((err) => setPageStatus(err.message, 'error'));
});

$('scanBtn').addEventListener('click', () => {
  scanNow().catch((err) => setPageStatus(err.message, 'error'));
});

$('routesToggle').addEventListener('click', () => {
  const wrap = $('routesTableWrap');
  const expanded = wrap.hidden;
  wrap.hidden = !expanded;
  $('routesToggle').setAttribute('aria-expanded', String(expanded));
});

$('compareForm').addEventListener('submit', (event) => {
  event.preventDefault();
  compareOrigins().catch((err) => setPageStatus(err.message, 'error'));
});

refreshAll()
  .then(() => compareOrigins())
  .catch((err) => setPageStatus(err.message, 'error'));
