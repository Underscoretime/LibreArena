// Shared helpers for all LibreArena pages (no build step).
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const autoPill = a => a?.pass === true ? '<span class="pill pass">auto pass</span>'
  : a?.pass === false ? '<span class="pill fail">auto fail</span>' : '<span class="pill warn">needs score</span>';
