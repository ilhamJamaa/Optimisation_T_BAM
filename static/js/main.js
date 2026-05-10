// ── Date ─────────────────────────────────────────────────
const elDate = document.getElementById('current-date');
if (elDate) {
  elDate.textContent = new Date().toLocaleDateString('fr-MA', {
    weekday:'long', day:'2-digit', month:'long', year:'numeric'
  });
}

// ── Upload Drag & Drop ───────────────────────────────────
function initUploadZone(zoneId, inputId, onFile) {
  const zone = document.getElementById(zoneId);
  const input = document.getElementById(inputId);
  if (!zone || !input) return;
  zone.addEventListener('click', () => input.click());
  input.addEventListener('change', () => { if (input.files[0]) onFile(input.files[0]); });
  zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('dragover'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
  zone.addEventListener('drop', e => {
    e.preventDefault(); zone.classList.remove('dragover');
    const f = e.dataTransfer.files[0];
    if (f) onFile(f);
  });
}

// ── Loading ──────────────────────────────────────────────
function showLoading(text='Traitement en cours...') {
  let ov = document.getElementById('loading-overlay');
  if (!ov) {
    ov = document.createElement('div');
    ov.id = 'loading-overlay';
    ov.className = 'loading-overlay';
    ov.innerHTML = `<div class="loading-card"><div class="spinner"></div><div class="loading-text" id="loading-text">${text}</div></div>`;
    document.body.appendChild(ov);
  } else {
    const t = document.getElementById('loading-text');
    if(t) t.textContent = text;
  }
  ov.classList.add('show');
}
function hideLoading() {
  const ov = document.getElementById('loading-overlay');
  if (ov) ov.classList.remove('show');
}

// ── Toast ────────────────────────────────────────────────
function toast(msg, type='success') {
  const colors = { success:'#1565C0', error:'#B71C1C', warning:'#E65100', info:'#003DA5' };
  const t = document.createElement('div');
  t.style.cssText = `position:fixed;bottom:24px;right:24px;z-index:9999;
    background:${colors[type]||colors.success};color:white;
    padding:12px 20px;border-radius:10px;font-family:Tajawal,sans-serif;
    font-size:0.9rem;font-weight:700;box-shadow:0 4px 20px rgba(0,0,0,0.25);
    animation:fadeIn 0.3s ease;border-left:4px solid rgba(255,255,255,0.4);`;
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3500);
}

// ── Render Table ─────────────────────────────────────────
function renderTable(containerId, rows, cols) {
  const wrap = document.getElementById(containerId);
  if (!wrap || !rows || rows.length === 0) {
    if(wrap) wrap.innerHTML = '<div style="color:#7F8C8D;text-align:center;padding:20px;font-size:0.875rem;">Aucune donnée</div>';
    return;
  }
  const headers = cols || Object.keys(rows[0]);
  let html = `<div class="table-wrap"><table><thead><tr>${headers.map(h=>`<th>${h}</th>`).join('')}</tr></thead><tbody>`;
  rows.forEach(row => {
    html += `<tr>${headers.map(h => {
      const val = row[h] ?? '';
      if (h==='STATUT' || h==='STATUT_CORRECTION') {
        const cls = val==='IDENTIQUE'?'badge-info':val==='CORRIGEE'?'badge-ok':val==='NON_TROUVEE'?'badge-error':'badge-warn';
        return `<td><span class="badge ${cls}">${val}</span></td>`;
      }
      if (h==='SCORE_FUZZY'||h==='SCORE') {
        const c = val>=90?'#1B5E20':val>=75?'#E65100':'#B71C1C';
        return `<td style="color:${c};font-family:'JetBrains Mono',monospace;font-weight:700;">${val}%</td>`;
      }
      if (h==='STATUT_GEOCODAGE') {
        const cls = val==='OK'?'badge-ok':val==='APPROXIMATIF'?'badge-warn':'badge-error';
        return `<td><span class="badge ${cls}">${val}</span></td>`;
      }
      return `<td>${val}</td>`;
    }).join('')}</tr>`;
  });
  html += '</tbody></table></div>';
  wrap.innerHTML = html;
}
