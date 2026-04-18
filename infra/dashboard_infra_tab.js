/**
 * Lumio Infra Tab — dashboard.js patch
 * Add this to your existing static/dashboard.js.
 * Adds a new "Infra" tab with service cards, start/stop controls, and logs.
 *
 * Depends on the existing dashboard's tab switching mechanism.
 * Integrate: add tab button to HTML, call initInfraTab() on load.
 */

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let infraServices = [];
let infraPollingInterval = null;
let selectedServiceLogs = null;

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
function initInfraTab() {
  loadInfraServices();
  infraPollingInterval = setInterval(loadInfraServices, 8000);
}

function destroyInfraTab() {
  if (infraPollingInterval) clearInterval(infraPollingInterval);
}

// ---------------------------------------------------------------------------
// Data
// ---------------------------------------------------------------------------
async function loadInfraServices() {
  try {
    const res = await fetch('/api/infra/services');
    if (!res.ok) {
      renderInfraError('Infra manager unreachable — is Ubuntu VM running?');
      return;
    }
    const data = await res.json();
    infraServices = data.services || [];
    renderInfraServices();
  } catch (e) {
    renderInfraError(`Connection error: ${e.message}`);
  }
}

// ---------------------------------------------------------------------------
// Render
// ---------------------------------------------------------------------------
function renderInfraServices() {
  const container = document.getElementById('infra-services-grid');
  if (!container) return;

  if (!infraServices.length) {
    container.innerHTML = '<div class="infra-empty">No services registered. Check services.yaml on Ubuntu VM.</div>';
    return;
  }

  container.innerHTML = infraServices.map(s => `
    <div class="infra-card ${s.running ? 'running' : 'stopped'}" id="infra-card-${s.id}">
      <div class="infra-card-header">
        <div>
          <div class="infra-service-name">${s.display_name}</div>
          <div class="infra-service-desc">${s.description || ''}</div>
        </div>
        <div class="infra-status-badge ${s.running ? 'badge-running' : 'badge-stopped'}">
          ${s.running ? '● Running' : '○ Stopped'}
        </div>
      </div>
      ${s.port ? `<div class="infra-port">Port ${s.port}</div>` : ''}
      <div class="infra-card-actions">
        ${s.running
          ? `<button class="btn-infra btn-stop" onclick="infraAction('${s.id}','stop')">Stop</button>
             <button class="btn-infra btn-restart" onclick="infraAction('${s.id}','restart')">Restart</button>`
          : `<button class="btn-infra btn-start" onclick="infraAction('${s.id}','start')">Start</button>`
        }
        <button class="btn-infra btn-logs" onclick="showLogs('${s.id}')">Logs</button>
      </div>
    </div>
  `).join('');
}

function renderInfraError(msg) {
  const container = document.getElementById('infra-services-grid');
  if (container) container.innerHTML = `<div class="infra-error">⚠ ${msg}</div>`;
}

// ---------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------
async function infraAction(serviceId, action) {
  const card = document.getElementById(`infra-card-${serviceId}`);
  if (card) card.style.opacity = '0.6';

  try {
    const res = await fetch(`/api/infra/services/${serviceId}/${action}`, { method: 'POST' });
    const data = await res.json();
    console.log(`[infra] ${serviceId} ${action}:`, data.status);
  } catch (e) {
    console.error(`[infra] ${action} failed:`, e);
  }
  setTimeout(loadInfraServices, 1200);
}

async function infraStartAll() {
  await fetch('/api/infra/services/start-all', { method: 'POST' });
  setTimeout(loadInfraServices, 1500);
}

async function infraStopAll() {
  if (!confirm('Stop all Lumio services?')) return;
  await fetch('/api/infra/services/stop-all', { method: 'POST' });
  setTimeout(loadInfraServices, 1500);
}

// ---------------------------------------------------------------------------
// Logs panel
// ---------------------------------------------------------------------------
async function showLogs(serviceId) {
  selectedServiceLogs = serviceId;
  const panel = document.getElementById('infra-logs-panel');
  const title = document.getElementById('infra-logs-title');
  const content = document.getElementById('infra-logs-content');
  if (!panel) return;

  title.textContent = `Logs — ${serviceId}`;
  content.textContent = 'Loading...';
  panel.style.display = 'flex';

  try {
    const res = await fetch(`/api/infra/services/${serviceId}/logs?tail=100`);
    const data = await res.json();
    content.textContent = data.logs || '(no logs)';
    content.scrollTop = content.scrollHeight;
  } catch (e) {
    content.textContent = `Error: ${e.message}`;
  }
}

function closeLogsPanel() {
  document.getElementById('infra-logs-panel').style.display = 'none';
  selectedServiceLogs = null;
}

// ---------------------------------------------------------------------------
// HTML snippet to inject into dashboard.html
// Add inside the tab panels section:
// ---------------------------------------------------------------------------
const INFRA_TAB_HTML = `
<!-- ======================================================= INFRA TAB -->
<div id="tab-infra" class="tab-panel" style="display:none;">
  <div class="infra-toolbar">
    <h2 class="section-title">Lumio Infrastructure</h2>
    <div style="display:flex;gap:8px;">
      <button class="btn-sm btn-primary" onclick="loadInfraServices()">Refresh</button>
      <button class="btn-sm btn-success" onclick="infraStartAll()">Start All</button>
      <button class="btn-sm btn-danger" onclick="infraStopAll()">Stop All</button>
    </div>
  </div>

  <div id="infra-services-grid" class="infra-grid"></div>

  <!-- Logs panel -->
  <div id="infra-logs-panel" style="display:none;position:fixed;bottom:0;left:0;right:0;
    height:40vh;background:#1a1d27;border-top:1px solid #2e3350;
    flex-direction:column;z-index:100;">
    <div style="display:flex;justify-content:space-between;align-items:center;
      padding:10px 16px;border-bottom:1px solid #2e3350;">
      <span id="infra-logs-title" style="font-size:0.85rem;color:#7986cb;"></span>
      <div style="display:flex;gap:8px;">
        <button class="btn-sm" onclick="showLogs(selectedServiceLogs)">Refresh</button>
        <button class="btn-sm btn-danger" onclick="closeLogsPanel()">Close</button>
      </div>
    </div>
    <pre id="infra-logs-content" style="flex:1;overflow-y:auto;padding:12px 16px;
      font-size:0.75rem;color:#b0bec5;white-space:pre-wrap;margin:0;"></pre>
  </div>
</div>
`;

// ---------------------------------------------------------------------------
// CSS to inject into dashboard (add to <style> tag)
// ---------------------------------------------------------------------------
const INFRA_CSS = `
.infra-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 16px;
  padding: 20px 0;
}

.infra-card {
  background: #1a1d27;
  border: 1px solid #2e3350;
  border-radius: 12px;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  transition: border-color 0.2s;
}

.infra-card.running { border-color: #388e3c; }
.infra-card.stopped { border-color: #2e3350; }

.infra-card-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 8px;
}

.infra-service-name { font-weight: 600; font-size: 0.95rem; }
.infra-service-desc { font-size: 0.78rem; color: #7986cb; margin-top: 2px; }
.infra-port { font-size: 0.75rem; color: #546e7a; }

.infra-status-badge {
  font-size: 0.72rem;
  font-weight: 600;
  padding: 3px 8px;
  border-radius: 20px;
  white-space: nowrap;
  flex-shrink: 0;
}

.badge-running { background: rgba(56,142,60,0.2); color: #66bb6a; }
.badge-stopped { background: rgba(96,125,139,0.15); color: #78909c; }

.infra-card-actions {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.btn-infra {
  padding: 5px 12px;
  border-radius: 6px;
  border: none;
  font-size: 0.78rem;
  font-weight: 600;
  cursor: pointer;
  transition: opacity 0.15s;
}

.btn-infra:hover { opacity: 0.85; }
.btn-start { background: #388e3c; color: #fff; }
.btn-stop { background: #c62828; color: #fff; }
.btn-restart { background: #f57c00; color: #fff; }
.btn-logs { background: #37474f; color: #b0bec5; }

.infra-empty, .infra-error {
  grid-column: 1/-1;
  color: #7986cb;
  font-size: 0.85rem;
  padding: 20px;
  text-align: center;
}

.infra-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-top: 8px;
}
`;

console.log('[Lumio] Infra tab JS loaded. Call initInfraTab() when tab is shown.');
