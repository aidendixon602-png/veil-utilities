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


CSS = """
:root { color-scheme:dark; --bg:#0b0d17; --surface:#141826; --surface2:#1a2031;
        --border:#30374a; --text:#f5f5fb; --muted:#b0b8ca; --subtle:#929bb0;
        --accent:#a99bff; --rose:#ff8993; }
* { box-sizing:border-box; }
html { scroll-behavior:smooth; }
body { margin:0; background:var(--bg); color:var(--text);
       font:14px/1.55 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
a { color:inherit; text-decoration:none; }
a:hover { color:#fff; }
a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible { outline:2px solid #d4cbff; outline-offset:3px; }
.skip-link { position:absolute; left:-9999px; top:8px; padding:10px 14px; background:#fff; color:#111; z-index:8; }
.skip-link:focus { left:12px; color:#111; }
.shell { min-height:100vh; display:grid; grid-template-columns:224px minmax(0,1fr); }
.sidebar { background:#101321; border-right:1px solid #282d3f; display:flex; flex-direction:column;
           padding:26px 18px; gap:36px; }
.brand { display:flex; align-items:center; gap:11px; color:var(--text); padding:0 5px; }
.brand:hover { color:var(--text); }
.brand-mark { display:grid; place-items:center; width:39px; height:39px; flex:none; border-radius:12px;
              background:#704fcd; border:1px solid #9676ed; box-shadow:0 6px 18px #7452ce30; color:#fff; }
.brand-mark svg { width:23px; height:23px; }
.logo { display:block; font-size:15px; font-weight:750; letter-spacing:-.025em; line-height:1.15; }
.brand-caption { display:block; margin-top:4px; color:var(--subtle); font-size:11px; line-height:1.1; }
.nav-label { display:block; color:#8993a9; font-size:10px; font-weight:700; letter-spacing:.11em; padding:0 12px 10px; }
.nav-link { display:flex; align-items:center; gap:11px; color:#e7e5ff; background:#282846; border:1px solid #454063;
            border-radius:9px; padding:11px 12px; font-size:13px; font-weight:650; }
.nav-link svg { width:17px; height:17px; color:#c6b9ff; flex:none; }
.side-note { margin-top:auto; border:1px solid #30364b; border-radius:12px; padding:16px 14px;
             background:#191d2d; color:var(--muted); font-size:12px; line-height:1.55; }
.side-note strong { display:block; color:#e7e5f2; font-size:12px; margin-bottom:6px; }
.workspace { min-width:0; }
header { height:76px; padding:0 40px; display:flex; align-items:center; justify-content:space-between; gap:20px;
         border-bottom:1px solid #2b3142; background:#101321; }
.top-title { color:var(--muted); font-size:12px; } .top-title b { color:#f2f1f8; font-size:13px; font-weight:650; }
.top-separator { padding:0 9px; color:#677087; }
.who { display:flex; align-items:center; gap:10px; font-size:12px; color:var(--muted); }
.who b { color:var(--text); font-weight:650; } .inline { display:inline; }
.account-dot { display:grid; place-items:center; width:32px; height:32px; border-radius:50%;
               background:#393153; color:#d8cfff; font-size:13px; font-weight:700; }
.container { max-width:1440px; margin:0 auto; padding:42px 40px 72px; }
.sub { display:none; }
.eyebrow { color:#c1b5ff; font-size:11px; font-weight:700; letter-spacing:.09em; text-transform:uppercase; margin:0 0 10px; }
h1,h2,h3,p { margin:0; } h1 { font-size:clamp(27px,3vw,36px); line-height:1.18; letter-spacing:-.04em; font-weight:720; }
h2 { font-size:17px; line-height:1.3; letter-spacing:-.02em; font-weight:700; } h3 { font-size:14px; }
p.muted { color:var(--muted); margin:8px 0 20px; }
.page-heading { display:flex; justify-content:space-between; align-items:flex-end; gap:20px; margin:0 0 28px; }
.page-heading p:not(.eyebrow) { color:var(--muted); margin-top:9px; max-width:650px; }
.stats { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; margin:0 0 26px; }
.stat-card { position:relative; overflow:hidden; min-width:0; padding:20px; border:1px solid var(--border);
             border-radius:13px; background:#161b2a; box-shadow:0 10px 24px #00000012; }
.stat-card:before { content:""; display:block; width:32px; height:3px; background:#a78dff; border-radius:4px; margin-bottom:17px; }
.stat-card:nth-child(2):before { background:#f18c98; } .stat-card:nth-child(3):before { background:#73bfcc; }
.stat-card:nth-child(4):before { background:#e4ba70; }
.stat-lbl { color:var(--muted); font-size:12px; font-weight:550; }
.stat-val { margin-top:5px; font-size:27px; line-height:1.15; font-weight:720; letter-spacing:-.04em; font-variant-numeric:tabular-nums; }
.stat-foot { color:var(--subtle); font-size:11px; margin-top:7px; }
.card { background:var(--surface); border:1px solid var(--border); border-radius:14px; padding:25px; margin-bottom:16px;
        box-shadow:0 10px 25px #00000012; }
.card-head { display:flex; align-items:flex-start; gap:14px; padding-bottom:19px; margin-bottom:22px; border-bottom:1px solid #30374a; }
.section-icon { display:grid; place-items:center; width:38px; height:38px; flex:none; border:1px solid #51486f;
                background:#28243b; border-radius:11px; color:#d8c8ff; font:700 11px ui-monospace,SFMono-Regular,Consolas,monospace; }
.card-head p { color:var(--muted); font-size:13px; margin-top:4px; }
.btn { display:inline-flex; align-items:center; justify-content:center; gap:8px; min-height:40px; padding:10px 16px;
       background:#7254cb; border:1px solid #896ee0; border-radius:8px; color:white;
       font-family:inherit; font-size:12px; line-height:1.2; font-weight:700; cursor:pointer; text-decoration:none; white-space:nowrap; }
.btn:hover { background:#6243ba; border-color:#a38cf5; color:#fff; }
.btn-plain { background:#22283a; border-color:#3c445a; color:#f0f0f8; }
.btn-plain:hover { background:#30384c; border-color:#606a84; }
.form-group { margin-bottom:18px; min-width:0; }
label { display:block; color:#e0e3ed; font-size:12px; font-weight:650; margin-bottom:7px; }
input,select,textarea { display:block; width:100%; min-height:42px; padding:10px 12px; background:#0e1220; color:var(--text);
               border:1px solid #424c63; border-radius:8px; font:inherit; font-size:13px; }
input[type=hidden] { display:none; } input::placeholder { color:#98a2b6; }
input:focus,select:focus { border-color:#b7a8ff; }
.hint { display:block; color:var(--muted); font-size:12px; line-height:1.5; margin-top:7px; }
p.hint + .btn { margin-top:16px; } textarea { resize:vertical; }
.note { background:#252335; border:1px solid #554969; border-radius:9px; color:#e1d7e8; padding:13px 15px; margin-bottom:22px; font-size:13px; }
.note.ok { border-color:#35745e; color:#bcebd1; background:#19332c; }
.note.bad { border-color:#ab535b; }
.center { max-width:620px; margin:65px auto; padding:58px 42px; text-align:center; }
.center h1 { margin:0 0 14px; } .center .btn { margin-top:8px; }
.grid,.form-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:0 20px; }
.server-box { border:1px solid var(--border); border-radius:10px; padding:16px; display:flex; justify-content:space-between; gap:15px; }
.server-name { font-weight:650; overflow-wrap:anywhere; } .server-meta,.table-note { font-size:12px; color:var(--muted); }
.filters { display:flex; flex-wrap:wrap; gap:12px; align-items:end; margin:23px 0 20px; padding:18px; border:1px solid #30384a;
           border-radius:11px; background:#111522; }
.filters .form-group { flex:1 1 120px; margin:0; } .filters .form-group:first-child { flex:2 1 190px; }
.table-note { padding:0 0 5px; } .table-wrap { overflow-x:auto; } table { width:100%; border-collapse:collapse; text-align:left; }
th { color:var(--subtle); font-size:10px; font-weight:700; letter-spacing:.06em; text-transform:uppercase; white-space:nowrap; }
th,td { padding:15px 12px; border-bottom:1px solid #30374a; } td { color:#e0e3ed; font-size:13px; }
td:first-child { min-width:200px; font-weight:650; } td:first-child a:hover { color:#c5b6ff; }
.server-initial,.server-icon { display:inline-grid; vertical-align:middle; place-items:center; width:38px; height:38px; margin-right:12px;
              border-radius:12px; border:1px solid #5c5284; color:#ede8ff; background:#373055; font-size:14px; font-weight:700; }
.server-icon { object-fit:cover; }
td:nth-child(2) { font-variant-numeric:tabular-nums; color:var(--muted); font-size:12px; }
tbody tr:hover { background:#ffffff06; } tbody tr:last-child td { border-bottom:0; }
.tag { display:inline-flex; align-items:center; gap:7px; padding:4px 9px; border-radius:99px; font-size:11px; font-weight:700;
       color:#c0c7d5; background:#262b3a; }
.tag:before { content:""; width:6px; height:6px; border-radius:50%; background:#929baa; }
.tag.on { color:#a9e9c9; background:#193a31; } .tag.on:before { background:#78dba8; }
.tag.off { color:#ffd0c9; background:#482b32; } .tag.off:before { background:#ff9b91; }
#antinuke .section-icon, #blacklist .section-icon { background:#382631; border-color:#754954; color:#ffd1d2; }
#logging .section-icon, #exclusions .section-icon { background:#20333d; border-color:#3e6977; color:#b3e9ef; }
.settings-layout { display:grid; grid-template-columns:190px minmax(0,1fr); gap:24px; align-items:start; }
.section-nav { position:sticky; top:22px; border:1px solid var(--border); background:#141826; border-radius:12px; padding:14px 10px; }
.section-nav span { display:block; padding:4px 10px 10px; color:var(--subtle); font-size:10px; font-weight:700; letter-spacing:.08em; }
.section-nav a { display:block; padding:9px 10px; color:#c9ccda; border-radius:7px; font-size:13px; }
.section-nav a:hover,.section-nav a:focus { background:#29283f; color:#f2edff; }
.settings-actions { display:flex; align-items:center; justify-content:space-between; gap:14px; padding:16px 0; }
.settings-actions p { color:var(--muted); font-size:12px; } .settings-actions .buttons,.heading-actions { display:flex; flex-wrap:wrap; gap:10px; }
.confirm-choice { display:flex; align-items:center; gap:9px; font-weight:500; margin:14px 0; }
.confirm-choice input { width:18px; height:18px; min-height:0; flex:none; }
.rules-heading { border-top:1px solid var(--border); padding-top:22px; margin-top:26px; }
.rule-row { display:grid; grid-template-columns:minmax(125px,1.4fr) repeat(4,minmax(85px,1fr)) auto;
            align-items:end; gap:10px; padding:15px 0; border-bottom:1px solid var(--border); }
.rule-row .form-group { margin:0; } .rule-row .hint { margin-top:2px; }
@media (max-width:1050px) { .rule-row { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .rule-row > div:first-child { grid-column:1 / -1; } }
@media (max-width:1050px) { .shell { grid-template-columns:76px minmax(0,1fr); } .sidebar { padding:24px 12px; }
  .brand { justify-content:center; padding:0; } .brand-copy,.nav-label,.nav-text,.side-note { display:none; }
  .nav-link { justify-content:center; padding:12px; } .settings-layout { grid-template-columns:1fr; gap:18px; }
  .section-nav { position:static; display:flex; flex-wrap:wrap; gap:3px; } .section-nav span { width:100%; } }
@media (max-width:760px) { .shell { display:block; } .sidebar { display:none; } header { height:auto; min-height:66px; padding:15px 20px; }
  .container { padding:30px 20px 50px; } .stats { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .top-title { display:block; } .page-heading { align-items:flex-start; flex-direction:column; } }
@media (max-width:480px) { header { flex-wrap:wrap; } .who { width:100%; justify-content:flex-end; }
  .container { padding:26px 15px 36px; } .card { padding:20px 16px; } .grid,.form-grid { grid-template-columns:1fr; }
  .stat-card { padding:16px; } .stat-val { font-size:23px; } .settings-actions { align-items:stretch; flex-direction:column; }
  .center { margin:35px auto; padding:42px 20px; } }
@media (max-width:760px) {
  .server-table,.server-table tbody { display:block; width:100%; }
  .server-table thead { position:absolute; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden;
                        clip:rect(0,0,0,0); white-space:nowrap; border:0; }
  .server-table tr { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px 12px;
                     border-bottom:1px solid #30374a; padding:18px 0; }
  .server-table tr:last-child { border:0; } .server-table td { display:block; min-width:0; padding:0; border:0; overflow-wrap:anywhere; }
  .server-table td:before { content:attr(data-label); display:block; color:var(--subtle); font-size:10px;
                            font-weight:700; letter-spacing:.04em; text-transform:uppercase; margin-bottom:3px; }
  .server-table td:first-child,.server-table td:last-child { grid-column:1 / -1; }
  .server-table td:first-child:before,.server-table td:last-child:before { display:none; }
  .server-table td:last-child .btn { width:100%; margin-top:3px; }
  .server-table td[colspan] { grid-column:1 / -1; }
}
/* Workspace navigation and feature overview */
.sidebar { gap:28px; }
.sidebar nav { display:grid; gap:3px; }
.nav-label { padding:17px 10px 5px; }
.nav-link { background:transparent; border-color:transparent; color:#b9c0d3; padding:9px 10px; }
.nav-link:hover { background:#20253b; }
.nav-link.active { background:#302a50; border-color:#51467b; color:#fff; }
.nav-icon { display:grid; place-items:center; flex:none; width:19px; color:#c4b4ff; font-size:17px; }
.server-switch { display:flex; align-items:center; min-width:0; padding:10px; gap:8px; margin:0 0 6px; border:1px solid #353c53; border-radius:11px; background:#1a1f31; }
.server-switch .server-icon,.server-switch .server-initial { width:36px; height:36px; margin:0; flex:none; }
.server-switch span:not(.server-initial) { min-width:0; }
.server-switch small,.server-switch strong { display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.server-switch small { color:var(--subtle); font-size:9px; letter-spacing:.08em; }
.server-switch strong { font-size:12px; } .server-switch b { margin-left:auto; color:var(--subtle); }
.mobile-nav { display:none; }
.server-hero { position:relative; overflow:hidden; display:flex; justify-content:space-between; gap:20px; padding:36px;
  border:1px solid #514879; border-radius:18px; background:radial-gradient(circle at 85% 0%,#6446aa80,transparent 42%),linear-gradient(115deg,#202345,#191a30 65%); margin-bottom:38px; }
.hero-server { display:flex; align-items:center; gap:16px; min-width:0; } .hero-server > div { min-width:0; } .hero-server h1 { overflow-wrap:anywhere; } .hero-server .server-icon,.hero-server .server-initial { width:58px; height:58px; margin:0; font-size:22px; }
.hero-server p,.hero-copy { color:#c7c7de; } .hero-server p { margin-top:5px; font-size:12px; }
.hero-copy { max-width:530px; margin-top:22px; } .hero-emblem { font-size:88px; line-height:1; color:#bdaaff; opacity:.35; }
.overview-heading { margin-bottom:20px; } .overview-heading h2 { font-size:21px; } .overview-heading p:last-child { color:var(--muted); margin-top:5px; }
.feature-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:14px; }
.feature-tile { display:flex; flex-direction:column; min-width:0; min-height:190px; padding:21px; border:1px solid var(--border); border-radius:14px; background:linear-gradient(145deg,#1a2032,#141827); transition:border-color .15s,transform .15s; }
.feature-tile:hover { border-color:#907de0; transform:translateY(-2px); } .tile-top { display:flex; justify-content:space-between; align-items:center; color:#a49bc7; margin-bottom:18px; }
.tile-icon { display:grid; place-items:center; width:38px; height:38px; border-radius:11px; background:#332b50; color:#d6caff; font-size:11px; font-weight:700; }
.feature-tile:nth-child(3n+2) .tile-icon { background:#342b42; color:#f3bbd7; }
.feature-tile:nth-child(3n) .tile-icon { background:#203b43; color:#a9e1e6; }
.feature-tile h3 { font-size:15px; } .feature-tile p { color:var(--muted); font-size:12px; margin:7px 0 16px; }
.tile-status { color:#c9bcff; font-size:11px; font-weight:700; margin-top:auto; } .overview-note { color:var(--subtle); font-size:12px; margin-top:20px; }
@media (max-width:1050px) { .server-switch { justify-content:center; padding:6px; } .server-switch span:not(.server-initial),.server-switch b { display:none; } .feature-grid { grid-template-columns:repeat(2,minmax(0,1fr)); } }
@media (max-width:760px) { .mobile-nav { display:block; margin:12px 20px 0; border:1px solid var(--border); background:#141826; border-radius:10px; }
 .mobile-nav summary { padding:12px 14px; cursor:pointer; font-weight:650; } .mobile-nav nav { padding:0 10px 10px; }
 .mobile-nav .nav-label,.mobile-nav .nav-text { display:block; } .mobile-nav .nav-link { justify-content:flex-start; } .mobile-nav .server-switch { display:none; }
 .server-hero { padding:25px; } .hero-emblem { font-size:55px; } }
@media (max-width:480px) { .feature-grid { grid-template-columns:1fr; } .feature-tile { min-height:160px; } .hero-emblem { display:none; } }
/* Landing workspace */
:root { --bg:#090b16; --surface:#121625; --border:#30354b; --accent:#b9a4ff; }
body { background:radial-gradient(ellipse at 85% 0%,#211b3f 0%,transparent 36%),#090b16; }
.sidebar { background:#0e1120; border-color:#252a40; }
header { background:#0e1120; border-color:#252a40; }
.container { max-width:1500px; }
.welcome-panel { position:relative; overflow:hidden; display:flex; justify-content:space-between; min-height:248px; border:1px solid #64578d; border-radius:20px; padding:32px 38px; background:radial-gradient(circle at 80% 15%,#7554cc80,transparent 40%),linear-gradient(115deg,#292448,#17182e 74%); box-shadow:0 18px 55px #0003; }
.welcome-copy { position:relative; z-index:1; } .welcome-kicker { display:inline-flex; align-items:center; gap:8px; padding:5px 10px; border:1px solid #9a85d74d; border-radius:100px; color:#ded5ff; background:#ffffff0d; font-size:11px; font-weight:700; letter-spacing:.03em; }
.kicker-dot { width:6px; height:6px; background:#bca7ff; border-radius:50%; box-shadow:0 0 10px #cdbfff; }
.welcome-panel h1 { margin-top:17px; font-size:clamp(30px,3.4vw,45px); line-height:1.08; letter-spacing:-.045em; } .welcome-panel h1 span { color:#cdbdff; }
.welcome-panel p { color:#cbc7e1; margin-top:12px; max-width:520px; }
.welcome-actions { display:flex; flex-wrap:wrap; align-items:center; gap:20px; margin-top:23px; }
.welcome-actions .btn { background:#d0c3ff; border-color:#d0c3ff; color:#21183d; padding:11px 17px; } .welcome-actions .btn:hover { background:white; }
.hero-action { color:#e5dcff; font-size:12px; font-weight:700; } .hero-action:hover { text-decoration:underline; }
.welcome-art { position:relative; align-self:center; width:210px; height:210px; flex:none; display:grid; place-items:center; }
.art-orbit { position:absolute; border:1px solid #d1c0ff33; border-radius:50%; inset:0; } .orbit-two { inset:23px; border-style:dashed; }
.art-shield { display:grid; place-items:center; width:112px; height:112px; border-radius:32px; background:linear-gradient(145deg,#896be1,#3f3174); border:1px solid #e1d6ff55; box-shadow:0 16px 45px #07041166,0 0 55px #aa8bff44; transform:rotate(-8deg); color:#e4daff; }
.art-shield svg { width:60px; transform:rotate(8deg); } .art-spark { position:absolute; color:#c8b7ff; font-size:24px; } .spark-one { top:12px; right:25px; } .spark-two { bottom:14px; left:17px; }
.activity-bar { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); border:1px solid #2d3248; border-radius:14px; background:#141827; margin-top:16px; }
.activity-bar > div { padding:15px 22px; min-width:0; } .activity-bar > div + div { border-left:1px solid #30354a; }
.activity-bar span,.guild-facts span { display:block; color:#929bb2; font-size:10px; font-weight:700; letter-spacing:.07em; }
.activity-bar strong { display:block; color:#f4f1ff; font-size:18px; margin-top:2px; }
.servers-section { margin-top:40px; } .servers-heading { display:flex; align-items:end; justify-content:space-between; gap:20px; }
.servers-heading h2 { font-size:25px; } .servers-heading p:last-child { color:var(--muted); margin-top:5px; }
.server-count { flex:none; border:1px solid #363d54; background:#171c2c; padding:6px 11px; border-radius:100px; color:#bec5d5; font-size:11px; }
.server-search { margin:20px 0; } .search-row { display:flex; gap:10px; align-items:end; }
.search-field { flex:1; min-width:0; } .search-field label { position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0,0,0,0); }
.search-field input { min-height:44px; padding-left:15px; background:#141827; } .search-row .btn { min-height:44px; }
.advanced-filters { margin-top:12px; color:#c3b7f1; font-size:12px; } .advanced-filters summary { cursor:pointer; font-weight:700; padding:4px 0; }
.filter-row { display:flex; flex-wrap:wrap; align-items:end; gap:12px; padding:14px; margin-top:8px; background:#141827; border:1px solid var(--border); border-radius:12px; }
.filter-row .form-group { flex:1 1 140px; margin:0; } .filter-row .btn { min-height:42px; }
.guild-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:15px; }
.guild-card { min-width:0; display:flex; flex-direction:column; padding:20px; border:1px solid #30364d; border-radius:16px; background:linear-gradient(150deg,#1b2034,#131727 80%); box-shadow:0 10px 30px #0002; }
.guild-card:hover { border-color:#7565a9; } .guild-card-top { display:flex; align-items:start; justify-content:space-between; gap:10px; }
.guild-card .server-initial,.guild-card .server-icon { width:52px; height:52px; border-radius:16px; margin:0; font-size:19px; }
.guild-access { color:#c8bdf1; background:#373050; border:1px solid #524969; border-radius:99px; padding:5px 9px; font-size:10px; font-weight:700; }
.guild-identity { margin-top:17px; min-width:0; } .guild-identity a { display:block; font-size:16px; font-weight:750; overflow-wrap:anywhere; } .guild-identity a:hover { color:#c7b5ff; }
.guild-identity span { display:block; color:#969fb5; font-size:11px; margin-top:3px; overflow-wrap:anywhere; }
.guild-facts { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px; border-top:1px solid #33384d; margin:19px 0 16px; padding-top:15px; }
.guild-facts strong { display:block; font-size:12px; margin-top:5px; overflow-wrap:anywhere; } .guild-facts .fact-on { color:#a6e8c7; } .guild-facts .fact-off { color:#ffb6b7; }
.guild-open { display:flex; justify-content:space-between; gap:8px; margin-top:auto; border:1px solid #4d466b; border-radius:9px; padding:10px 12px; background:#292542; color:#e8e0ff; font-size:12px; font-weight:700; }
.guild-open:hover { background:#3a315f; border-color:#8c79cb; } .empty-servers { border:1px dashed #424961; border-radius:16px; padding:35px; color:var(--muted); background:#141827; } .empty-servers strong { color:var(--text); font-size:15px; } .empty-servers p { margin:7px 0; } .empty-servers a { color:#c5b5ff; font-weight:700; }
@media (max-width:1100px) { .guild-grid { grid-template-columns:repeat(2,minmax(0,1fr)); } }
@media (max-width:760px) { .welcome-panel { padding:26px; min-height:0; } .welcome-art { width:150px; height:150px; } .art-shield { width:88px; height:88px; } .art-shield svg { width:48px; }
 .activity-bar { grid-template-columns:repeat(2,minmax(0,1fr)); } .activity-bar > div:nth-child(3) { border-left:0; } .activity-bar > div:nth-child(n+3) { border-top:1px solid #30354a; }
 .servers-section { margin-top:30px; } }
@media (max-width:540px) { .welcome-art { display:none; } .welcome-panel h1 { font-size:32px; } .guild-grid { grid-template-columns:1fr; } .servers-heading { align-items:start; flex-direction:column; gap:12px; } .search-row { flex-wrap:wrap; } .search-field { flex-basis:100%; } .search-row .btn { flex:1; } }
/* Read-only security and routing summaries */
.health-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; margin-bottom:15px; }
.health-grid > div { border:1px solid var(--border); border-radius:10px; background:#101522; padding:13px; }
.health-grid span,.route-row small { color:var(--muted); font-size:11px; }
.health-grid span { display:block; font-weight:700; letter-spacing:.06em; } .health-grid strong { display:block; margin-top:5px; }
.routes-preview { padding:16px; margin-bottom:22px; border:1px solid var(--border); border-radius:10px; background:#101522; }
.route-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:0 22px; margin-top:12px; }
.route-row { display:grid; grid-template-columns:minmax(80px,1fr) minmax(80px,1fr); gap:3px 10px; border-top:1px solid var(--border); padding:10px 0; }
.route-row strong { overflow-wrap:anywhere; font-size:12px; } .route-row small { grid-column:2; }
.list-edit { border-top:1px solid var(--border); margin-top:20px; padding-top:20px; }
.list-edit:first-of-type { border-top:0; margin-top:0; padding-top:0; } .list-edit h3 { margin-bottom:5px; }
.list-controls { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:10px; align-items:end; margin-top:12px; }
.list-controls .form-group { margin:0; } .list-controls button { min-height:42px; }
@media (max-width:600px) { .route-grid { grid-template-columns:1fr; } .list-controls { grid-template-columns:1fr; } }
@media (prefers-reduced-motion:reduce) { html { scroll-behavior:auto; } .feature-tile { transition:none; } .feature-tile:hover { transform:none; } }
/* A quieter, information-first workspace. Existing controls and permission checks are unchanged. */
:root { --bg:#090e19; --surface:#121a29; --surface2:#192235; --border:#29384c;
        --text:#eef3fb; --muted:#adbdd0; --subtle:#91a4ba; --accent:#9aabff; }
body { background:#090e19; }
.sidebar { background:#0d1522; border-color:#233145; }
header { background:#0d1522; border-color:#233145; }
.brand-mark { background:#425ec1; border-color:#6c83d8; box-shadow:none; }
.nav-link.active { background:#1d3155; border-color:#385683; }
.nav-link:hover { background:#1a2a40; }
.nav-icon { color:#9fb7f1; }
.container { max-width:1440px; }
.welcome-panel { min-height:0; align-items:center; padding:30px 34px; border-color:#304760;
  border-radius:15px; background:linear-gradient(115deg,#152c48,#152338 75%,#1b2945); box-shadow:none; }
.welcome-kicker { background:#192e48; border-color:#405a78; color:#c5d6fa; }
.kicker-dot { background:#8ac7b4; box-shadow:none; }
.welcome-panel h1 { margin-top:13px; font-size:clamp(26px,3vw,35px); }
.welcome-panel p { color:#c1cede; max-width:560px; }
.welcome-actions { margin-top:19px; gap:18px; }
.welcome-actions .btn { color:#f7f9ff; background:#4c6fd1; border-color:#708cdd; }
.welcome-actions .btn:hover { background:#5d7bdf; }
.hero-action { color:#c5d7ff; }
.welcome-aside { display:flex; align-items:center; gap:13px; color:#b1c6eb; font-size:11px;
  font-weight:750; letter-spacing:.1em; }
.welcome-aside small { display:block; color:#91a7c0; font-size:9px; letter-spacing:.09em; margin-top:3px; }
.workspace-symbol { display:grid; place-items:center; width:52px; height:52px; color:#d7e2ff;
  background:#273e60; border:1px solid #486686; border-radius:14px; font-size:27px; }
.activity-bar { background:#111b2a; border-color:#293b51; }
.activity-bar > div + div { border-color:#293b51; }
.servers-section { margin-top:32px; }
.servers-heading h2 { font-size:21px; }
.server-search input,.filter-row { background:#121c2b; }
.guild-card,.feature-tile { background:#121c2b; border-color:#2b3b50; box-shadow:none; }
.guild-card:hover,.feature-tile:hover { border-color:#6687c1; }
.guild-access { background:#1c3041; border-color:#34566c; }
.guild-facts { border-color:#2b3b50; }
.guild-open,.tile-status { color:#adc6ff; }
.guild-open { background:#1c3050; border-color:#3b5881; }
.guild-open:hover { background:#29436b; border-color:#668bd1; }
.side-note { background:#152235; border-color:#2f4057; }
.tile-icon svg { width:21px; height:21px; }
.workspace-symbol svg { width:26px; height:26px; }
.server-hero { padding:27px 30px; border-color:#304760; border-radius:15px; margin-bottom:30px;
  background:linear-gradient(110deg,#152c48,#152338 75%); }
.hero-copy { margin-top:13px; }
.feature-tile { min-height:172px; border-radius:12px; }
.tile-icon,.feature-tile:nth-child(n) .tile-icon { font-size:19px; background:#213858; color:#c5d8ff; }
.card { background:#121c2b; border-color:#2b3b50; box-shadow:none; }
.section-icon { background:#213858; border-color:#3a5778; color:#c5d8ff; }
.btn { background:#4969c8; border-color:#6d87d8; }
.btn:hover { background:#5b7bdf; border-color:#90a9ed; }
.btn-plain { background:#1d2a3c; border-color:#3b4f69; }
.btn-plain:hover { background:#283b53; }
input,select,textarea { background:#0c1522; border-color:#425670; }
@media (max-width:760px) { .welcome-panel { padding:25px; } .welcome-aside { display:none; }
  .server-hero { padding:24px; } }
@media (max-width:480px) { .welcome-panel { padding:22px; } .feature-tile { min-height:150px; } }

"""


# Workspace refresh: a restrained, high-contrast management interface. Forms and routes
# remain server rendered so the existing CSP and authorization model stay intact.
CSS += """
:root { --bg:#101116; --surface:#191b22; --surface2:#20232c; --border:#343741;
        --text:#f6f4f4; --muted:#c1bdc3; --subtle:#a6a2aa; --accent:#f0abb6; }
body { background:#101116; font-size:14px; }
.shell { grid-template-columns:242px minmax(0,1fr); }
.sidebar { background:#15161c; border-color:#2c2e36; padding:28px 16px; gap:34px; }
.brand { padding:0 8px; gap:12px; }
.brand-mark { background:#822f41; border-color:#b55b70; border-radius:12px; }
.brand-caption { color:#a7a2aa; }
.nav-label { color:#99949d; letter-spacing:.12em; padding-top:16px; }
.nav-link { background:transparent; border:1px solid transparent; color:#c9c6cb; padding:10px 12px; }
.nav-link:hover { background:#24242d; border-color:#33343e; }
.nav-link.active { background:#342229; border-color:#68404a; color:#fff4f5; }
.nav-icon,.nav-link svg { color:#d4a4ae; }
.server-switch { background:#202027; border-color:#37363f; }
.side-note { background:#1d1c23; border-color:#38343b; }
header { background:#15161c; border-color:#2c2e36; height:72px; }
.account-dot { background:#4b3039; color:#ffe4e8; }
.container { max-width:1500px; padding:42px 44px 76px; }
.eyebrow { color:#e1a5b0; letter-spacing:.14em; font-size:10px; }
.welcome-panel,.server-hero { background:#251d24; border:1px solid #55343e; border-radius:16px; box-shadow:none; }
.welcome-panel { padding:38px 40px; }
.welcome-panel h1 { font-size:clamp(30px,3.2vw,42px); letter-spacing:-.045em; }
.welcome-panel p,.hero-copy { color:#d1c3c9; }
.welcome-kicker { background:#39252d; border-color:#69404b; color:#f6c8d0; }
.kicker-dot { background:#e8a0ae; }
.welcome-actions .btn,.btn { background:#a84358; border-color:#c46578; color:#fff; }
.welcome-actions .btn:hover,.btn:hover { background:#bd5369; border-color:#df8495; }
.btn-plain { background:#26262e; border-color:#45434d; }
.btn-plain:hover { background:#33313a; border-color:#665d68; }
.hero-action { color:#f4b6c1; }
.workspace-symbol,.tile-icon,.feature-tile:nth-child(n) .tile-icon,.section-icon { background:#35262e; border-color:#62404a; color:#f2bdc6; }
.welcome-aside { color:#d8b5bd; }
.welcome-aside small { color:#b99ba3; }
.activity-bar { background:#191b22; border-color:#343741; }
.activity-bar > div + div { border-color:#343741; }
.card,.guild-card,.feature-tile { background:#1a1c23; border-color:#35363e; box-shadow:none; border-radius:14px; }
.guild-card:hover,.feature-tile:hover { border-color:#a96777; transform:translateY(-2px); }
.guild-card,.feature-tile { transition:border-color .15s ease,transform .15s ease; }
.card-head,.guild-facts { border-color:#38363e; }
.guild-open { background:#34252e; border-color:#70434e; color:#ffced6; }
.guild-open:hover { background:#4b303a; border-color:#a26473; }
.guild-access { background:#28362f; border-color:#496555; color:#c1ead1; }
.tile-status { color:#e9a9b6; }
.server-search input,.filter-row,input,select,textarea { background:#121319; border-color:#4b4851; }
input:focus,select:focus,textarea:focus { border-color:#e49baa; }
.section-nav { background:#1a1c23; border-color:#35363e; }
.section-nav a:hover,.section-nav a:focus { background:#35262e; }
.note { background:#28242a; border-color:#59434b; color:#eadce0; }
a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible { outline-color:#ffbdc9; }
.workspace-grid { display:grid; grid-template-columns:minmax(0,1fr) 250px; gap:26px; align-items:start; }
.workspace-grid .feature-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
.workspace-summary { border:1px solid var(--border); background:#191b22; border-radius:14px; padding:22px; }
.workspace-summary h2 { font-size:17px; margin:0; letter-spacing:-.02em; }
.workspace-summary > p:not(.eyebrow) { color:var(--muted); font-size:12px; }
.summary-row { display:flex; justify-content:space-between; gap:12px; padding:14px 0; border-top:1px solid var(--border); font-size:12px; }
.summary-row span { color:var(--muted); } .summary-row strong { color:var(--text); text-align:right; }
.workspace-summary .summary-note { margin-top:10px; line-height:1.6; color:var(--subtle); }
@media (max-width:1050px) { .shell { grid-template-columns:76px minmax(0,1fr); } .workspace-grid { grid-template-columns:1fr; } }
@media (max-width:760px) { .shell { display:block; } .container { padding:26px 20px 44px; } .welcome-panel { padding:26px; } }
@media (max-width:480px) { .container { padding:22px 15px 36px; } .welcome-panel { padding:22px; } .workspace-grid .feature-grid { grid-template-columns:1fr; } }
@media (prefers-reduced-motion:reduce) { *,*::before,*::after { scroll-behavior:auto !important; transition:none !important; } }
/* Workspace system: shared surfaces, hierarchy and responsive setup guidance. */
:root { --bg:#0b1018; --surface:#141e2a; --surface2:#1a2938; --border:#344758; --text:#f3f7fb; --muted:#bbc9d4; --subtle:#a7b8c4; --accent:#9cdfe0; }
body { background:var(--bg); font-size:15px; line-height:1.6; }
.sidebar, header { background:#101b26; border-color:#2b3d4d; }
.brand-mark { background:#237c80; border-color:#64bcbc; box-shadow:none; }
.nav-link { background:transparent; border-color:transparent; color:#c2d2dc; }
.nav-link:hover, .nav-link.active { background:#203a46; border-color:#47727a; color:#fff; }
.nav-link svg, .eyebrow { color:#91d7d5; }
.card, .workspace-summary, .guild-card, .feature-tile { background:var(--surface); border-color:var(--border); box-shadow:0 10px 26px #0002; }
.feature-tile:hover, .guild-card:hover { border-color:#6ababc; transform:none; }
.server-hero, .welcome-panel { background:linear-gradient(120deg,#173e47,#152736 70%); border-color:#426c74; }
.btn, .welcome-actions .btn { background:#157579; border-color:#5bb4b1; color:white; min-height:44px; font-size:13px; }
.btn:hover, .welcome-actions .btn:hover { background:#1b898b; border-color:#91d7d5; }
.btn-plain { background:#20303e; border-color:#466070; }
.btn-plain:hover { background:#2c4250; border-color:#6e9aa2; }
input, select, textarea { background:#0d1721; border-color:#526979; min-height:44px; }
:focus-visible, summary:focus-visible { outline:3px solid #a5ebdf; outline-offset:3px; }
.setup-guide { border:1px solid #3b5964; background:#142b33; border-radius:14px; padding:22px; margin:0 0 24px; }
.setup-guide > div p:not(.eyebrow), .setup-guide li span { color:var(--muted); }
.setup-guide ol { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:20px; padding:0; margin:18px 0 0; list-style:none; counter-reset:setup; }
.setup-guide li { counter-increment:setup; border-top:1px solid #45636b; padding-top:12px; min-width:0; }
.setup-guide li::before { content:"0" counter(setup); display:block; color:#a0e2d7; font-size:11px; font-weight:700; }
.setup-guide li a { display:block; color:#fff; font-weight:700; text-decoration:underline; text-underline-offset:3px; }
.setup-guide li span { display:block; font-size:12px; margin-top:5px; }
.form-status { min-height:1.6em; color:#f3d093; font-size:13px; margin-top:10px; }
@media (max-width:700px) { .setup-guide ol { grid-template-columns:1fr; gap:12px; } .setup-guide { padding:18px; } }
@media (prefers-reduced-motion:reduce) { *, *::before, *::after { scroll-behavior:auto !important; transition:none !important; animation:none !important; } }

"""


