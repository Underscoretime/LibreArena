// Shared helpers for all LibreArena pages (no build step).
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const autoPill = a => a?.pass === true ? '<span class="pill pass">auto pass</span>'
  : a?.pass === false ? '<span class="pill fail">auto fail</span>' : '<span class="pill warn">needs score</span>';
const VENDOR_COLORS = { Qwen: '#6157ff', Google: '#34a853', LFM: '#111111', Meta: '#0082fb', Mistral: '#ff7000', Microsoft: '#00a4ef', DeepSeek: '#4d6bfe', Unknown: '#5b6472' };
const VENDOR_LETTER = { Qwen: 'Q', Google: 'G', LFM: 'L', Meta: 'M', Mistral: 'M', Microsoft: 'P', DeepSeek: 'D', Unknown: '?' };
const vcolor = v => VENDOR_COLORS[v] || `hsl(${[...(v || '?')].reduce((a, c) => a + c.charCodeAt(0), 0) % 360} 45% 45%)`;
const badge = v => { const l = VENDOR_LETTER[v] || (v || '?')[0].toUpperCase(); const extra = v === 'LFM' ? 'border:1px solid #e9e6da;' : ''; return `<span class="vbadge" title="${esc(v)}" style="background:${vcolor(v)};${extra}">${esc(l)}</span>`; };
function guessVendor(id) { const s = String(id).toLowerCase();
  if (/qwen/.test(s)) return 'Qwen'; if (/gemma|google/.test(s)) return 'Google';
  if (/llama|meta/.test(s)) return 'Meta'; if (/mistral|mixtral/.test(s)) return 'Mistral';
  if (/phi/.test(s)) return 'Microsoft'; if (/deepseek/.test(s)) return 'DeepSeek';
  if (/lfm|liquid/.test(s)) return 'LFM'; return 'Unknown'; }
