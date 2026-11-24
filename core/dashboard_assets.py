"""Static dashboard styles and progressive-enhancement script. No user data is interpolated.

Styles layer the original control layouts, workspace skin and current design tokens.
"""

DASHBOARD_SCRIPT = r"""
(() => {
  const dirty = new Set();
  document.querySelectorAll('form[method="post" i]:not([action="/logout"])').forEach(form => {
    const status = document.createElement('p');
    status.className = 'form-status';
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    form.append(status);
    const mark = () => { dirty.add(form); status.textContent = 'Unsaved changes'; };
    form.addEventListener('input', mark);
    form.addEventListener('change', mark);
    form.addEventListener('submit', () => { dirty.delete(form); status.textContent = 'Saving…'; });
  });
  window.addEventListener('beforeunload', event => {
    if (dirty.size) { event.preventDefault(); event.returnValue = ''; }
  });
})();
"""


# One design system, one cascade. The previous file stacked six "theme layers" that each
# redefined :root tokens and component rules, so later layers silently recoloured earlier
# components (blue button borders on a teal background, plum nav highlights, pink eyebrows,
# navy panels on a near-black page). Every class emitted by the dashboard lives here once.
CSS = """
:root {
  color-scheme: dark;
  --bg: #0a0e16;
  --bg-elevated: #0f141f;
  --surface: #131a26;
  --surface-2: #18202e;
  --surface-3: #1f2837;
  --border: #253044;
  --border-strong: #33415a;
  --text: #eef3fa;
  --muted: #a3b0c2;
  --subtle: #7f8da1;
  --accent: #7aa2ff;
  --accent-strong: #9dbbff;
  --accent-soft: #1b2a4a;
  --success: #5fd39a;
  --success-soft: #12281f;
  --danger: #ff8189;
  --danger-soft: #2b1620;
  --warning: #f0c274;
  --warning-soft: #2a2317;
  --radius-lg: 16px;
  --radius: 12px;
  --radius-sm: 8px;
  --shadow: 0 18px 40px rgba(0, 0, 0, .35);
  --font: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}

* { box-sizing: border-box; }
html { scroll-behavior: smooth; scroll-padding-top: 84px; }
body { margin: 0; background: var(--bg); color: var(--text); font: 14px/1.55 var(--font);
       -webkit-font-smoothing: antialiased; }
a { color: var(--accent-strong); text-decoration: none; }
a:hover { color: #fff; }
h1, h2, h3, p { margin: 0; }
h1 { font-size: clamp(24px, 2.6vw, 32px); line-height: 1.2; letter-spacing: -.02em; font-weight: 700; }
h2 { font-size: 17px; line-height: 1.3; letter-spacing: -.01em; font-weight: 650; }
h3 { font-size: 14px; font-weight: 650; }
code, kbd, samp { font-family: var(--mono); font-size: .92em; background: var(--surface-3);
                  padding: 2px 6px; border-radius: 6px; overflow-wrap: anywhere; }
:focus-visible { outline: 2px solid var(--accent-strong); outline-offset: 2px; }
::selection { background: #33508f; color: #fff; }
.skip-link { position: absolute; left: 12px; top: -60px; z-index: 50; padding: 10px 14px;
             background: var(--text); color: #0a0e16; border-radius: var(--radius-sm); font-weight: 700; }
.skip-link:focus { top: 12px; color: #0a0e16; }

/* ---------------------------------------------------------------- shell */
.shell { min-height: 100vh; display: grid; grid-template-columns: 256px minmax(0, 1fr); }
.sidebar { background: var(--bg-elevated); border-right: 1px solid var(--border);
           display: flex; flex-direction: column; gap: 22px; padding: 22px 14px 18px;
           position: sticky; top: 0; height: 100vh; overflow-y: auto; }
.brand { display: flex; align-items: center; gap: 12px; padding: 2px 8px 6px; color: var(--text); }
.brand:hover { color: var(--text); }
.brand-mark { display: grid; place-items: center; flex: none; width: 38px; height: 38px;
              border-radius: 11px; color: #fff; background: linear-gradient(150deg, #6f9bff, #7c6eff);
              box-shadow: 0 8px 20px rgba(122, 162, 255, .25); }
.brand-mark svg { width: 22px; height: 22px; }
.brand-copy { min-width: 0; }
.logo { display: block; font-size: 15px; font-weight: 750; letter-spacing: -.01em; line-height: 1.15; }
.brand-caption { display: block; margin-top: 3px; color: var(--subtle); font-size: 11px; }
.sidebar-navigation { min-width: 0; }
.sidebar-navigation > summary { display: none; }
.sidebar-navigation nav { display: grid; gap: 2px; }
.nav-label { display: block; padding: 14px 10px 6px; color: var(--subtle); font-size: 10px;
             font-weight: 700; letter-spacing: .1em; }
.nav-link { display: flex; align-items: center; gap: 11px; padding: 9px 10px; border-radius: var(--radius-sm);
            color: var(--muted); font-size: 13px; font-weight: 550; border: 1px solid transparent; }
.nav-link:hover { background: var(--surface-2); color: var(--text); }
.nav-link.active { background: var(--accent-soft); border-color: #2d4674; color: #fff; }
.nav-label:first-child { padding-top: 4px; }
.nav-icon { display: grid; place-items: center; flex: none; width: 20px; height: 20px; color: #8fb0ff; }
.nav-icon svg { width: 18px; height: 18px; }
.nav-text { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.server-switch { display: flex; align-items: center; gap: 10px; min-width: 0; padding: 9px 10px;
                 margin: 2px 0 4px; border: 1px solid var(--border); border-radius: var(--radius);
                 background: var(--surface); color: var(--text); }
.server-switch:hover { border-color: var(--border-strong); color: var(--text); }
.server-switch .server-icon, .server-switch .server-initial { width: 32px; height: 32px; margin: 0;
                 border-radius: 9px; flex: none; font-size: 13px; }
.server-switch span:not(.server-initial) { min-width: 0; }
.server-switch small, .server-switch strong { display: block; overflow: hidden; text-overflow: ellipsis;
                 white-space: nowrap; }
.server-switch small { color: var(--subtle); font-size: 9px; letter-spacing: .08em; }
.server-switch strong { font-size: 12px; }
.server-switch b { margin-left: auto; color: var(--subtle); font-weight: 500; }
.side-note { margin-top: auto; display: flex; flex-direction: column; gap: 4px; padding: 13px 14px;
             border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface);
             color: var(--muted); font-size: 12px; }
.side-note strong { color: var(--text); font-size: 12px; }

.workspace { min-width: 0; display: flex; flex-direction: column; }
header { display: flex; align-items: center; justify-content: space-between; gap: 18px;
         min-height: 64px; padding: 12px 32px; border-bottom: 1px solid var(--border);
         background: rgba(15, 20, 31, .82); backdrop-filter: blur(8px);
         position: sticky; top: 0; z-index: 10; }
.top-title { display: flex; align-items: center; flex-wrap: wrap; gap: 0 8px; min-width: 0;
             color: var(--muted); font-size: 12px; }
.top-title > a { color: var(--subtle); }
.top-title b { color: var(--text); font-size: 13px; font-weight: 650; }
.top-separator { color: var(--border-strong); }
.crumb-server { max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sub { display: none; }
.who { display: flex; align-items: center; gap: 10px; flex: none; color: var(--muted); font-size: 12px; }
.who b { color: var(--text); font-weight: 650; }
.inline { display: inline; }
.account-dot { display: grid; place-items: center; width: 30px; height: 30px; border-radius: 50%;
               background: var(--surface-3); border: 1px solid var(--border-strong);
               color: var(--accent-strong); font-size: 12px; font-weight: 700; }
.container { width: 100%; max-width: 1320px; margin: 0 auto; padding: 30px 32px 64px; }
.mobile-nav { display: none; }

/* ---------------------------------------------------------------- text */
.eyebrow { color: var(--accent-strong); font-size: 10px; font-weight: 700; letter-spacing: .12em;
           text-transform: uppercase; margin: 0 0 8px; }
p.muted { color: var(--muted); margin: 8px 0 18px; }
.hint { display: block; margin-top: 6px; color: var(--muted); font-size: 12px; line-height: 1.5; }
.overview-note { margin-top: 18px; color: var(--subtle); font-size: 12px; }
.page-heading { display: flex; align-items: flex-end; justify-content: space-between; gap: 20px;
                margin: 0 0 24px; }
.page-heading p:not(.eyebrow) { margin-top: 7px; max-width: 680px; color: var(--muted); }
.heading-actions { display: flex; flex-wrap: wrap; gap: 10px; }

/* ---------------------------------------------------------------- surfaces */
.card { padding: 22px; margin-bottom: 16px; border: 1px solid var(--border);
        border-radius: var(--radius-lg); background: var(--surface); }
.card-head { display: flex; align-items: flex-start; gap: 13px; padding-bottom: 16px;
             margin-bottom: 18px; border-bottom: 1px solid var(--border); }
.card-head p { margin-top: 3px; color: var(--muted); font-size: 13px; }
.section-icon { display: grid; place-items: center; flex: none; width: 34px; height: 34px;
                border: 1px solid #2d4674; border-radius: 10px; background: var(--accent-soft);
                color: var(--accent-strong); font: 700 11px var(--mono); }
.note { padding: 12px 14px; margin-bottom: 18px; border: 1px solid var(--border-strong);
        border-radius: var(--radius-sm); background: var(--surface-2); color: var(--muted); font-size: 13px; }
.note strong { color: var(--text); }
.note a { color: var(--accent-strong); text-decoration: underline; text-underline-offset: 2px; }
.note.ok { border-color: #235c42; background: var(--success-soft); color: #b6f0d2; }
.note.bad { border-color: #6b2c36; background: var(--danger-soft); color: #ffc9cd; }
.center { max-width: 560px; margin: 48px auto; padding: 48px 36px; text-align: center; }
.center h1, .center h2 { margin-bottom: 12px; }
.center p { margin: 0 auto 20px; max-width: 440px; }
.stats { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; margin: 0 0 22px; }
.stat-card { min-width: 0; padding: 17px 18px; border: 1px solid var(--border);
             border-radius: var(--radius); background: var(--surface); }
.stat-card::before { content: ""; display: block; width: 26px; height: 3px; margin-bottom: 13px;
                     border-radius: 3px; background: var(--accent); }
.stat-card:nth-child(2)::before { background: var(--success); }
.stat-card:nth-child(3)::before { background: var(--warning); }
.stat-card:nth-child(4)::before { background: var(--danger); }
.stat-lbl { color: var(--muted); font-size: 11px; font-weight: 600; letter-spacing: .04em;
            text-transform: uppercase; }
.stat-val { margin-top: 4px; font-size: 26px; line-height: 1.15; font-weight: 700;
            letter-spacing: -.02em; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.stat-foot { margin-top: 6px; color: var(--subtle); font-size: 11px; }

/* ---------------------------------------------------------------- controls */
.btn { display: inline-flex; align-items: center; justify-content: center; gap: 7px;
       min-height: 38px; padding: 9px 15px; border: 1px solid #4a6dc0; border-radius: var(--radius-sm);
       background: #2c4c9c; color: #fff; font: 650 13px/1.2 var(--font); white-space: nowrap;
       cursor: pointer; text-decoration: none; }
.btn:hover { background: #365bb8; border-color: #6a8ae0; color: #fff; }
.btn-plain { border-color: var(--border-strong); background: var(--surface-2); color: var(--text); }
.btn-plain:hover { background: var(--surface-3); border-color: #44567a; color: #fff; }
.buttons { display: flex; flex-wrap: wrap; gap: 10px; }
label { display: block; margin-bottom: 6px; color: #dbe3ee; font-size: 12px; font-weight: 600; }
input, select, textarea { display: block; width: 100%; min-height: 38px; padding: 9px 11px;
       border: 1px solid var(--border-strong); border-radius: var(--radius-sm);
       background: var(--bg-elevated); color: var(--text); font: inherit; font-size: 13px; }
input::placeholder { color: #5f6d82; }
input:focus, select:focus, textarea:focus { border-color: var(--accent); outline: none;
       box-shadow: 0 0 0 3px rgba(122, 162, 255, .18); }
input[type=hidden] { display: none; }
textarea { resize: vertical; min-height: 76px; }
select { appearance: none; background-image: linear-gradient(45deg, transparent 50%, var(--muted) 50%),
         linear-gradient(135deg, var(--muted) 50%, transparent 50%);
         background-position: calc(100% - 17px) 50%, calc(100% - 12px) 50%;
         background-size: 5px 5px, 5px 5px; background-repeat: no-repeat; padding-right: 32px; }
input[type=checkbox] { display: inline-block; width: 16px; height: 16px; min-height: 0; margin: 0;
       accent-color: #5b82e8; vertical-align: middle; }
.form-group { min-width: 0; margin-bottom: 16px; }
.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 18px; }
.filters { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 12px; padding: 16px;
           margin: 0 0 18px; border: 1px solid var(--border); border-radius: var(--radius);
           background: var(--surface-2); }
.filters .form-group { flex: 1 1 160px; margin: 0; }
.confirm-choice { display: inline-flex; align-items: center; gap: 9px; margin: 12px 0;
                  color: var(--muted); font-size: 12px; font-weight: 500; }
.form-status { min-height: 1.5em; margin-top: 8px; color: var(--warning); font-size: 12px; }
.list-controls { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 10px; }
.list-controls .form-group, .list-controls input, .list-controls select { margin: 0; }
.list-edit { padding: 14px 0; border-top: 1px solid var(--border); }
.list-edit:first-of-type { border-top: 0; padding-top: 0; }
.list-edit h3 { margin-bottom: 2px; }
.channel-reference { margin: 4px 0 16px; color: var(--muted); font-size: 12px; }
.channel-reference summary { cursor: pointer; font-weight: 650; color: var(--accent-strong); }
.channel-reference ul { margin: 8px 0 0; padding-left: 18px; columns: 2; column-gap: 24px; }
.channel-reference li { break-inside: avoid; padding: 2px 0; }

/* ---------------------------------------------------------------- tables */
.table-wrap { overflow-x: auto; border: 1px solid var(--border); border-radius: var(--radius); }
table { width: 100%; border-collapse: collapse; text-align: left; }
thead th { padding: 10px 14px; background: var(--surface-2); color: var(--subtle); font-size: 10px;
           font-weight: 700; letter-spacing: .06em; text-transform: uppercase; white-space: nowrap;
           border-bottom: 1px solid var(--border); }
td { padding: 11px 14px; border-bottom: 1px solid var(--border); color: var(--text); font-size: 13px; }
td:first-child { font-weight: 600; }
.table-wrap td:first-child { min-width: 140px; }
tbody tr:last-child td { border-bottom: 0; }
tbody tr:hover { background: rgba(255, 255, 255, .02); }
td:last-child .btn { min-height: 32px; padding: 6px 12px; font-size: 12px; }

/* ---------------------------------------------------------------- servers */
.welcome-panel { display: flex; align-items: center; justify-content: space-between; gap: 24px;
  padding: 32px 34px; margin-bottom: 26px; border: 1px solid var(--border-strong);
  border-radius: 18px; background: linear-gradient(120deg, #16233a, #111827 70%); }
.welcome-copy { min-width: 0; }
.welcome-kicker { display: inline-flex; align-items: center; gap: 8px; padding: 4px 10px;
  border: 1px solid #2d4674; border-radius: 99px; background: var(--accent-soft);
  color: var(--accent-strong); font-size: 11px; font-weight: 700; }
.kicker-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--success); }
.welcome-panel h1 { margin-top: 14px; }
.welcome-panel p { max-width: 560px; margin-top: 10px; color: var(--muted); }
.welcome-actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 20px; }
.hero-action { display: inline-flex; align-items: center; gap: 6px; color: var(--accent-strong);
  font-size: 13px; font-weight: 650; }
.welcome-aside { display: flex; align-items: center; gap: 12px; flex: none; color: var(--muted);
  font-size: 11px; letter-spacing: .08em; }
.welcome-aside small { display: block; color: var(--subtle); font-size: 10px; }
.workspace-symbol { display: grid; place-items: center; width: 54px; height: 54px; border-radius: 14px;
  border: 1px solid var(--border-strong); background: var(--surface-2); color: var(--accent-strong); }
.workspace-symbol svg { width: 26px; height: 26px; }
.activity-bar { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); margin-bottom: 28px;
  border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); }
.activity-bar > div { min-width: 0; padding: 14px 18px; }
.activity-bar > div + div { border-left: 1px solid var(--border); }
.activity-bar span { display: block; color: var(--subtle); font-size: 10px; font-weight: 700;
  letter-spacing: .07em; }
.activity-bar strong { display: block; margin-top: 3px; font-size: 18px; font-variant-numeric: tabular-nums; }
.servers-section { margin-top: 28px; }
.servers-heading { display: flex; align-items: flex-end; justify-content: space-between; gap: 18px; }
.servers-heading h2 { font-size: 22px; }
.servers-heading p:last-child { margin-top: 4px; color: var(--muted); }
.server-count { flex: none; padding: 5px 11px; border: 1px solid var(--border); border-radius: 99px;
  background: var(--surface); color: var(--muted); font-size: 11px; }
.server-search { margin: 18px 0; }
.search-row { display: flex; align-items: flex-end; gap: 10px; }
.search-field { flex: 1; min-width: 0; }
.search-field label { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0, 0, 0, 0); }
.search-row .btn { min-height: 38px; }
.advanced-filters { margin-top: 10px; color: var(--muted); font-size: 12px; }
.advanced-filters summary { cursor: pointer; font-weight: 650; color: var(--accent-strong); padding: 4px 0; }
.filter-row { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 12px; padding: 14px;
  margin-top: 8px; border: 1px solid var(--border); border-radius: var(--radius);
  background: var(--surface-2); }
.filter-row .form-group { flex: 1 1 150px; margin: 0; }
.guild-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }
.guild-card { display: flex; flex-direction: column; min-width: 0; padding: 18px;
  border: 1px solid var(--border); border-radius: var(--radius-lg); background: var(--surface); }
.guild-card:hover { border-color: var(--border-strong); }
.guild-card-top { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; }
.guild-access { flex: none; padding: 3px 9px; border: 1px solid #235c42; border-radius: 99px;
  background: var(--success-soft); color: #a9e9c9; font-size: 10px; font-weight: 700; }
.guild-identity { margin-top: 14px; min-width: 0; }
.guild-identity a { display: block; color: var(--text); font-size: 15px; font-weight: 650;
  overflow-wrap: anywhere; }
.guild-identity a:hover { color: var(--accent-strong); }
.guild-identity span { display: block; margin-top: 3px; color: var(--subtle); font-size: 11px;
  font-family: var(--mono); }
.guild-facts { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px;
  margin: 16px 0 14px; padding-top: 13px; border-top: 1px solid var(--border); }
.guild-facts span { display: block; color: var(--subtle); font-size: 9px; font-weight: 700;
  letter-spacing: .07em; }
.guild-facts strong { display: block; margin-top: 3px; font-size: 12px; overflow-wrap: anywhere; }
.guild-facts .fact-on { color: var(--success); }
.guild-facts .fact-off { color: var(--danger); }
.guild-open { display: flex; align-items: center; justify-content: space-between; gap: 8px;
  margin-top: auto; padding: 9px 12px; border: 1px solid var(--border-strong);
  border-radius: var(--radius-sm); background: var(--surface-2); color: var(--text);
  font-size: 12px; font-weight: 650; }
.guild-open:hover { border-color: var(--accent); color: #fff; }
.empty-servers { padding: 28px; border: 1px dashed var(--border-strong); border-radius: var(--radius-lg);
  background: var(--surface); color: var(--muted); }
.empty-servers strong { color: var(--text); font-size: 15px; }
.empty-servers p { margin: 6px 0 10px; }
.server-hero { display: flex; align-items: center; justify-content: space-between; gap: 20px;
  padding: 26px 28px; margin-bottom: 26px; border: 1px solid var(--border-strong);
  border-radius: 18px; background: linear-gradient(120deg, #16233a, #111827 70%); }
.hero-server { display: flex; align-items: center; gap: 15px; min-width: 0; }
.hero-server > div { min-width: 0; }
.hero-server h1 { overflow-wrap: anywhere; }
.hero-server .server-icon, .hero-server .server-initial { width: 56px; height: 56px; margin: 0;
  border-radius: 15px; font-size: 21px; }
.hero-server p { margin-top: 4px; color: var(--muted); font-size: 12px; font-family: var(--mono); }
.hero-copy { max-width: 540px; margin-top: 14px; color: var(--muted); }
.server-icon { width: 34px; height: 34px; }
.server-initial, .server-icon { display: inline-grid; place-items: center; flex: none;
  width: 34px; height: 34px; margin-right: 10px; border-radius: 10px;
  border: 1px solid var(--border-strong); background: var(--surface-3);
  color: var(--accent-strong); font-size: 13px; font-weight: 700; }
.server-icon { object-fit: cover; }
.overview-heading { margin-bottom: 18px; }
.overview-heading h2 { font-size: 20px; }
.overview-heading p:last-child { margin-top: 4px; color: var(--muted); }
.workspace-grid { display: grid; grid-template-columns: minmax(0, 1fr) 260px; gap: 22px; align-items: start; }
.workspace-grid .feature-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.feature-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }
.feature-tile { display: flex; flex-direction: column; min-width: 0; min-height: 164px; padding: 18px;
  border: 1px solid var(--border); border-radius: var(--radius-lg); background: var(--surface); }
.feature-tile:hover { border-color: var(--border-strong); background: var(--surface-2); }
.tile-top { display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px;
  color: var(--subtle); }
.tile-icon { display: grid; place-items: center; width: 34px; height: 34px; border-radius: 9px;
  border: 1px solid #2d4674; background: var(--accent-soft); color: var(--accent-strong); }
.tile-icon svg { width: 19px; height: 19px; }
.feature-tile h3 { font-size: 14px; }
.feature-tile p { margin: 5px 0 12px; color: var(--muted); font-size: 12px; }
.tile-status { margin-top: auto; color: var(--accent-strong); font-size: 11px; font-weight: 650; }
.workspace-summary { padding: 20px; border: 1px solid var(--border); border-radius: var(--radius-lg);
  background: var(--surface); position: sticky; top: 84px; }
.workspace-summary h2 { margin-bottom: 4px; }
.workspace-summary > p:not(.eyebrow) { color: var(--muted); font-size: 12px; }
.summary-row { display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding: 11px 0; border-top: 1px solid var(--border); font-size: 12px; }
.summary-row span { color: var(--muted); }
.summary-row strong { color: var(--text); text-align: right; }
.workspace-summary .summary-note { margin-top: 10px; color: var(--subtle); line-height: 1.55; }
.setup-guide { padding: 20px; margin-bottom: 20px; border: 1px solid var(--border-strong);
  border-radius: var(--radius-lg); background: var(--surface-2); }
.setup-guide > div p:not(.eyebrow), .setup-guide li span { color: var(--muted); }
.setup-guide ol { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px;
  padding: 0; margin: 16px 0 0; list-style: none; counter-reset: setup; }
.setup-guide li { counter-increment: setup; min-width: 0; padding-top: 10px;
  border-top: 1px solid var(--border); }
.setup-guide li::before { content: "0" counter(setup); display: block; margin-bottom: 5px;
  color: var(--accent-strong); font-size: 10px; font-weight: 700; letter-spacing: .08em; }
.setup-guide li a { display: block; color: var(--text); font-size: 13px; font-weight: 650;
  text-decoration: underline; text-underline-offset: 3px; text-decoration-color: var(--border-strong); }
.setup-guide li a:hover { color: var(--accent-strong); }
.setup-guide li span { display: block; margin-top: 4px; font-size: 12px; }

/* ---------------------------------------------------------------- settings */
.settings-layout { display: grid; grid-template-columns: 190px minmax(0, 1fr); gap: 22px; align-items: start; }
.section-nav { position: sticky; top: 84px; padding: 12px 8px; border: 1px solid var(--border);
  border-radius: var(--radius); background: var(--surface); }
.section-nav span { display: block; padding: 4px 10px 8px; color: var(--subtle); font-size: 10px;
  font-weight: 700; letter-spacing: .08em; }
.section-nav a { display: block; padding: 7px 10px; border-radius: var(--radius-sm);
  color: var(--muted); font-size: 13px; }
.section-nav a:hover, .section-nav a:focus { background: var(--surface-2); color: var(--text); }
.settings-actions { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between;
  gap: 14px; padding: 16px 20px; border: 1px solid var(--border); border-radius: var(--radius);
  background: var(--surface); }
.settings-actions p { color: var(--muted); font-size: 12px; }
.rule-row { display: grid; grid-template-columns: minmax(130px, 1.4fr) repeat(4, minmax(85px, 1fr)) auto;
  align-items: end; gap: 10px; padding: 14px 0; border-bottom: 1px solid var(--border); }
.rule-row:last-of-type { border-bottom: 0; }
.feature-content { display: block; min-width: 0; }
.feature-content > .card:last-child { margin-bottom: 0; }
.card-subsection { margin-top: 22px; padding-top: 20px; border-top: 1px solid var(--border); }
.card-subsection .card-head { margin-bottom: 16px; padding-bottom: 14px; }
.rule-row .form-group { margin: 0; }
.rule-row .hint { margin-top: 2px; }
.rule-row .btn { min-height: 38px; }
.rules-heading { margin-top: 22px; padding-top: 18px; border-top: 1px solid var(--border); }
.health-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-bottom: 14px; }
.health-grid > div { padding: 12px 14px; border: 1px solid var(--border); border-radius: var(--radius);
  background: var(--surface-2); }
.health-grid span { display: block; color: var(--subtle); font-size: 10px; font-weight: 700;
  letter-spacing: .06em; }
.health-grid strong { display: block; margin-top: 4px; font-size: 15px; }
.routes-preview { margin-bottom: 18px; padding: 16px; border: 1px solid var(--border);
  border-radius: var(--radius); background: var(--surface-2); }
.routes-preview h3 { margin-bottom: 4px; }
.route-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 22px; margin-top: 10px; }
.route-row { display: grid; grid-template-columns: minmax(80px, 1fr) minmax(80px, 1fr); gap: 2px 10px;
  padding: 9px 0; border-top: 1px solid var(--border); }
.route-row span { color: var(--muted); font-size: 11px; }
.route-row strong { font-size: 12px; overflow-wrap: anywhere; }
.route-row small { grid-column: 2; color: var(--muted); font-size: 11px; }

/* ---------------------------------------------------------------- control center */
.control-grid { display: grid; grid-template-columns: 1.2fr 1fr; gap: 16px; }
.control-links { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin-top: 16px; }
.control-links a { display: flex; align-items: center; justify-content: space-between; gap: 10px;
  padding: 12px 14px; border: 1px solid var(--border-strong); border-radius: var(--radius-sm);
  background: var(--surface-2); color: var(--text); font-size: 13px; font-weight: 650; }
.control-links a:hover { border-color: var(--accent); color: #fff; }
.control-picker { display: flex; align-items: flex-end; gap: 12px; margin-bottom: 20px; }
.control-picker .form-group { flex: 1; margin: 0; }
/* ---------------------------------------------------------------- chips */
.tag { display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 99px;
       border: 1px solid var(--border-strong); background: var(--surface-2); color: var(--muted);
       font-size: 11px; font-weight: 600; }
.tag.on { border-color: #235c42; background: var(--success-soft); color: #a9e9c9; }
.tag.off { border-color: #6b2c36; background: var(--danger-soft); color: #ffc9cd; }
.tag-list { display: flex; flex-wrap: wrap; gap: 8px; }
.control-pagination { display: flex; gap: 8px; margin-top: 14px; }

/* ---------------------------------------------------------------- responsive */
@media (max-width: 1180px) {
  .guild-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .feature-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .rule-row { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .rule-row > div:first-child { grid-column: 1 / -1; }
  .workspace-grid { grid-template-columns: 1fr; }
  .workspace-summary { position: static; }
}
@media (max-width: 1050px) {
  .shell { grid-template-columns: 76px minmax(0, 1fr); }
  .sidebar { padding: 20px 10px; align-items: stretch; }
  .brand { justify-content: center; padding: 0 0 4px; }
  .brand-copy, .nav-label, .nav-text, .side-note, .server-switch span:not(.server-initial),
  .server-switch b { display: none; }
  .nav-link { justify-content: center; padding: 10px; }
  .server-switch { justify-content: center; padding: 6px; }
  .sidebar-navigation > summary { display: block; cursor: pointer; padding: 4px 6px 10px;
    color: var(--subtle); font-size: 10px; }
  .settings-layout { grid-template-columns: 1fr; gap: 16px; }
  .section-nav { position: static; display: flex; flex-wrap: wrap; gap: 2px; }
  .section-nav span { width: 100%; }
}
@media (max-width: 800px) {
  .shell { display: block; }
  .sidebar { display: none; }
  .mobile-nav { display: block; margin: 12px 20px 0; border: 1px solid var(--border);
    border-radius: var(--radius); background: var(--surface); }
  .mobile-nav > summary { cursor: pointer; padding: 12px 14px; font-weight: 650; font-size: 13px; }
  .mobile-nav nav { display: grid; gap: 2px; padding: 0 10px 10px; }
  .mobile-nav .nav-label { display: block; }
  .mobile-nav .nav-text { display: inline; }
  .mobile-nav .nav-link { justify-content: flex-start; }
  .mobile-nav .server-switch { display: none; }
  header { padding: 10px 20px; position: static; }
  .container { padding: 22px 20px 48px; }
  .stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .page-heading, .servers-heading { align-items: flex-start; flex-direction: column; gap: 12px; }
  .control-grid { grid-template-columns: 1fr; }
  .activity-bar { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .activity-bar > div:nth-child(3) { border-left: 0; }
  .activity-bar > div:nth-child(n+3) { border-top: 1px solid var(--border); }
  .setup-guide ol { grid-template-columns: 1fr; gap: 12px; }
  .route-grid { grid-template-columns: 1fr; }
  .server-hero, .welcome-panel { padding: 22px; }
  .welcome-aside { display: none; }
}
@media (max-width: 620px) {
  .guild-grid, .feature-grid, .workspace-grid .feature-grid { grid-template-columns: 1fr; }
  .form-grid { grid-template-columns: 1fr; }
  .search-row { flex-wrap: wrap; }
  .search-field { flex-basis: 100%; }
  .search-row .btn { flex: 1; }
  .control-links { grid-template-columns: 1fr; }
  .control-picker { align-items: stretch; flex-direction: column; }
  .settings-actions { flex-direction: column; align-items: stretch; }
  .rule-row { grid-template-columns: 1fr; }
  .channel-reference ul { columns: 1; }
}
@media (max-width: 480px) {
  header { flex-wrap: wrap; }
  .who { width: 100%; justify-content: flex-end; }
  .container { padding: 18px 14px 40px; }
  .card { padding: 16px; }
  .stats { grid-template-columns: 1fr; }
  .stat-val { font-size: 22px; }
  .center { margin: 28px auto; padding: 34px 18px; }
  .welcome-panel h1 { font-size: 26px; }
}
@media (prefers-reduced-motion:reduce) {
  html { scroll-behavior: auto; }
  *, *::before, *::after { transition: none !important; animation: none !important; }
}
"""
