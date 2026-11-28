/* Bioconda on Apple Silicon: renders the tables from data/*.json (written by farm/export.py).
   No result is computed here: statuses come from the farm; this file only filters and displays them. */
"use strict";
const COLS = ["linux-64", "linux-aarch64", "osx-64", "osx-arm64"];
const PNAME = { "linux-64": "x86 Linux", "linux-aarch64": "arm64 Linux", "osx-64": "Intel Mac", "osx-arm64": "Apple Silicon" };
const ST = {
  N: ["✓ native", "solves, with a native build for this platform"],
  A: ["✓ noarch", "solves; the package is platform-independent"],
  S: ["✓ installs", "the environment solves"],
  R: ["✗ Intel only", "no Apple Silicon build; an Intel-Mac build exists (works through Rosetta 2 until macOS 28)"],
  B: ["✗ no build", "no build of this version for this platform"],
  U: ["✗ dependency", "a build exists, but a dependency has no build for this platform"],
  O: ["⚙ newer OS", "needs a newer macOS / glibc than the machine we model"],
  E: ["? farm", "farm problem; retried next night"],
  "-": ["–", "not tested"],
};
const GOOD = new Set(["N", "A", "S"]);
const SMOKE = {
  V: ["✓✓", "installed for real on a real machine and passed the package's own test"],
  I: ["✓i", "installed for real on a real machine (the package has no test)"],
  T: ["✗t", "installed on a real machine, but the package's own test failed"],
  F: ["✗i", "the real install failed on a real machine"],
  E: ["?", "farm problem during the real-machine test"],
};
const smoke = (p, pl) => { const c = p.sm && p.sm[pl]; return c ? ` <span class="sm sm-${c}" title="${esc(SMOKE[c][1])}">${SMOKE[c][0]}</span>` : ""; };
const REASON = { not_opted_in: "recipe not opted in", opted_in_no_build: "opted in, build missing", dropped: "build dropped", no_recipe: "recipe gone" };
/* Embedding (e.g. yuvalbloch.com/wild-west/): a page with <div id="app"> and window.BIOARM = {base, back}
   gets this page's markup from `base` and every relative link points back to `base`. */
const EMBED = window.BIOARM || null;
const BASE = (EMBED && EMBED.base) || "";
const U = (p) => (p && BASE && !/^(https?:|#|\/|mailto:|blob:)/.test(p) ? BASE + p : p);
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (n) => Number(n).toLocaleString("en-US");
const D = {};

function chip(code, href, extra) {
  const [label, title] = ST[code] || ST["-"];
  const t = esc(title + (extra ? ` (${extra})` : ""));
  return href ? `<a class="chip s-${code}" href="${esc(U(href))}" title="${t}">${label}</a>` : `<span class="chip s-${code}" title="${t}">${label}</span>`;
}
const pkgLink = (n) => U(`pkg/${encodeURIComponent(n)}/`);
function envLink(id) {
  const [src, path] = [id.slice(0, id.indexOf(":")), id.slice(id.indexOf(":") + 1)];
  return src === "nf-core" ? `https://github.com/nf-core/modules/tree/master/modules/nf-core/${path}`
    : `https://github.com/snakemake/snakemake-wrappers/tree/master/bio/${path}`;
}
const code = (p, pl, i = 0) => (p.v[i] ? p.v[i][1][COLS.indexOf(pl)] : "-");

async function shell() {
  const html = await fetch(BASE + "index.html").then((r) => r.text());
  const doc = new DOMParser().parseFromString(html, "text/html");
  doc.querySelectorAll("[href],[src]").forEach((e) => {
    for (const a of ["href", "src"]) if (e.hasAttribute(a)) e.setAttribute(a, U(e.getAttribute(a)));
  });
  doc.querySelectorAll("script, noscript").forEach((e) => e.remove());
  const back = (EMBED.back || []).map(([href, label]) => `<a href="${esc(href)}">${label}</a>`).join(" · ");
  $("#app").innerHTML = (back ? `<div class="embed-back wrap">${back}</div>` : "") + doc.body.innerHTML;
}

async function load() {
  if (EMBED && !$("#tabs")) await shell();
  const get = (f) => fetch(`${BASE}data/${f}`).then((r) => (r.ok ? r.json() : null)).catch(() => null);
  [D.sum, D.pk, D.env, D.news] = await Promise.all([get("summary.json"), get("packages.json"), get("envs.json"), get("news.json")]);
  D.byName = new Map((D.pk?.pkgs || []).map((p) => [p.n, p]));
  header();
  route();
  window.addEventListener("hashchange", route);
}

function header() {
  const s = D.sum;
  if (!s) return;
  $("#updated").textContent = `Package index of ${s.snapshot}.`;
  $("#gaps-n").textContent = `(${fmt(s.intel_only_tools)})`;
  const nf = s.envs_by_source["nf-core"], sm = s.envs_by_source.snakemake;
  const mac = s.newest["osx-arm64"] || {};
  const good = (mac.native || 0) + (mac.noarch || 0);
  const cal = s.calibration;
  const calAgree = cal ? Object.values(cal.platforms).reduce((a, p) => a + p.agree, 0) : 0;
  const calN = cal ? Object.values(cal.platforms).reduce((a, p) => a + p.checked, 0) : 0;
  $("#stats").innerHTML = [
    `<div class="stat bad"><b>${fmt(s.intel_only_tools)}</b><span>tools install on Intel Mac but not Apple Silicon (newest version)</span></div>`,
    `<div class="stat bad"><b>${fmt(nf.intel_mac_only + sm.intel_mac_only)}</b><span>of ${fmt(nf.total + sm.total)} nf-core / Snakemake steps are Intel-Mac only</span></div>`,
    `<div class="stat good"><b>${fmt(good)}</b><span>of ${fmt(s.packages)} tools install on Apple Silicon</span></div>`,
    cal ? `<div class="stat good"><b>${calAgree}/${calN}</b><span>solver answers matched real installs (<a href="#about">calibration</a>)</span></div>` : "",
    s.smoke && s.smoke["osx-arm64"] && s.smoke["osx-arm64"].runs ? `<div class="stat good"><b>${fmt(s.smoke["osx-arm64"].runs)}</b><span>tools installed and passed their own test on a real Apple Silicon Mac</span></div>` : "",
  ].join("");
}

function route() {
  const tab = (location.hash || "#tools").slice(1).split("/")[0];
  const known = ["gaps", "tools", "pipelines", "blockers", "news", "check", "about"];
  const t = known.includes(tab) ? tab : "tools";
  document.querySelectorAll(".tab").forEach((e) => e.classList.toggle("active", e.id === `tab-${t}`));
  document.querySelectorAll("#tabs a").forEach((e) => e.classList.toggle("active", e.dataset.tab === t));
  ({ gaps, tools, pipelines, blockers, news, check, about })[t]();
}

// ---------------------------------------------------------------- gaps
function gapRows() {
  return D.pk.pkgs.filter((p) => GOOD.has(code(p, "osx-64")) && !GOOD.has(code(p, "osx-arm64")) && !"OE-".includes(code(p, "osx-arm64")));
}
function gaps() {
  if (gaps.done) return;
  gaps.done = true;
  const draw = () => {
    const q = $("#gaps-q").value.trim().toLowerCase(), pipe = $("#gaps-pipe").checked;
    const rows = gapRows().filter((p) => (!q || p.n.includes(q)) && (!pipe || p.u > 0));
    $("#gaps-table").innerHTML = `<thead><tr><th>#</th><th>Tool</th><th>Newest</th><th>Apple Silicon</th><th>Blocked by</th><th>Why</th><th>Use instead</th><th class="num">Pipeline steps</th><th class="num">Downloads</th></tr></thead><tbody>` +
      rows.map((p, i) => {
        const c = code(p, "osx-arm64"), bl = p.b[`${p.v[0][0]}|osx-arm64`] || [];
        return `<tr><td class="num muted">${i + 1}</td><td class="name"><a href="${pkgLink(p.n)}">${esc(p.n)}</a></td><td>${esc(p.v[0][0])}</td>` +
          `<td>${chip(c, pkgLink(p.n))}</td><td class="wrapok small">${bl.filter((b) => b !== p.n).map(esc).join(", ")}</td>` +
          `<td class="small">${esc(REASON[p.r["osx-arm64"]] || "")}</td><td>${p.pin["osx-arm64"] ? `<b>${esc(p.pin["osx-arm64"])}</b> ✓` : ""}</td>` +
          `<td class="num">${p.u || ""}</td><td class="num">${fmt(p.d)}</td></tr>`;
      }).join("") + `</tbody>`;
  };
  $("#gaps-q").addEventListener("input", draw);
  $("#gaps-pipe").addEventListener("change", draw);
  $("#gaps-csv").addEventListener("click", () => {
    const lines = [["tool", "newest_version", "apple_silicon_status", "blocked_by", "reason", "newest_version_that_installs", "pipeline_steps", "downloads"].join(",")];
    for (const p of gapRows()) {
      const bl = (p.b[`${p.v[0][0]}|osx-arm64`] || []).filter((b) => b !== p.n).join(" ");
      lines.push([p.n, p.v[0][0], ST[code(p, "osx-arm64")][0].replace(/^✗ /, ""), bl, p.r["osx-arm64"] || "", p.pin["osx-arm64"] || "", p.u, p.d].map((x) => `"${String(x).replace(/"/g, '""')}"`).join(","));
    }
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/csv" }));
    a.download = `apple-silicon-gaps-${D.sum.snapshot}.csv`;
    a.click();
  });
  draw();
}

// ---------------------------------------------------------------- tools
function tools() {
  if (tools.done) return;
  tools.done = true;
  $("#tools-plat").innerHTML = COLS.slice().reverse().map((c) => `<option value="${c}">${PNAME[c]}</option>`).join("");
  $("#legend").innerHTML = "NABRUO".split("").map((c) => `<span>${chip(c)} ${esc(ST[c][1])}</span>`).join("");
  let limit = 200;
  const draw = () => {
    const q = $("#tools-q").value.trim().toLowerCase(), pl = $("#tools-plat").value, st = $("#tools-st").value;
    let rows = D.pk.pkgs.filter((p) => !q || p.n.includes(q));
    if (st) rows = rows.filter((p) => { const c = code(p, pl); return st === "good" ? GOOD.has(c) : st === "bad" ? "RBU".includes(c) : c === st; });
    if (q) rows.sort((a, b) => (b.n === q) - (a.n === q) || (b.n.startsWith(q)) - (a.n.startsWith(q)) || b.d - a.d);
    $("#tools-table").innerHTML = `<thead><tr><th>Tool</th><th>Newest</th>${COLS.map((c) => `<th>${PNAME[c]}</th>`).join("")}<th>Apple Silicon: use</th><th class="num">Downloads</th></tr></thead><tbody>` +
      rows.slice(0, limit).map((p) =>
        `<tr><td class="name"><a href="${pkgLink(p.n)}">${esc(p.n)}</a>${p.cf ? ' <span class="muted small" title="served by conda-forge with the standard setup">cf</span>' : ""}</td><td>${esc(p.v[0]?.[0] || "")}</td>` +
        COLS.map((c) => `<td>${chip(code(p, c), pkgLink(p.n), (p.b[`${p.v[0]?.[0]}|${c}`] || []).join(", "))}${smoke(p, c)}</td>`).join("") +
        `<td>${p.pin["osx-arm64"] ? esc(p.pin["osx-arm64"]) : ""}</td><td class="num">${fmt(p.d)}</td></tr>`).join("") + `</tbody>`;
    $("#tools-more").innerHTML = rows.length > limit ? `Showing ${limit} of ${fmt(rows.length)}. <a href="#tools" id="tools-all">Show all</a>` : `${fmt(rows.length)} tools.`;
    const all = $("#tools-all");
    if (all) all.addEventListener("click", (e) => { e.preventDefault(); limit = 1e9; draw(); });
  };
  ["#tools-q", "#tools-plat", "#tools-st"].forEach((s) => $(s).addEventListener("input", () => { limit = 200; draw(); }));
  draw();
}

// ---------------------------------------------------------------- pipelines
function pipelines() {
  if (pipelines.done) return;
  pipelines.done = true;
  const s = D.sum.envs_by_source;
  const pct = (a, b) => `${Math.round((100 * a) / b)}%`;
  $("#pipe-stats").innerHTML = ["nf-core", "snakemake"].map((k) => {
    const r = s[k], mac = r["osx-arm64"] || {}, lin = r["linux-aarch64"] || {};
    const name = k === "nf-core" ? "nf-core modules" : "Snakemake wrappers";
    return `<div class="stat"><b>${fmt(mac.solves || 0)} / ${fmt(r.total)}</b><span>${name} install on Apple Silicon (${pct(mac.solves || 0, r.total)}); ${fmt(r.intel_mac_only)} Intel-Mac only</span></div>` +
      `<div class="stat"><b>${fmt(lin.solves || 0)} / ${fmt(r.total)}</b><span>${name} install on arm64 Linux (${pct(lin.solves || 0, r.total)})</span></div>`;
  }).join("");
  let limit = 200;
  const draw = () => {
    const q = $("#pipe-q").value.trim().toLowerCase(), src = $("#pipe-src").value, st = $("#pipe-st").value;
    const rows = D.env.envs.filter((e) => (!q || e.id.toLowerCase().includes(q)) && (!src || e.id.startsWith(src + ":")) && (!st || e.s[3] === st));
    $("#pipe-table").innerHTML = `<thead><tr><th>Pipeline step</th>${COLS.map((c) => `<th>${PNAME[c]}</th>`).join("")}<th>Blocked on Apple Silicon by</th></tr></thead><tbody>` +
      rows.slice(0, limit).map((e) =>
        `<tr><td class="name"><a href="${esc(envLink(e.id))}" title="${esc(e.deps.join("\n"))}">${esc(e.id)}</a></td>` +
        COLS.map((c, i) => `<td>${chip(e.s[i], e.l[c], (e.b[c] || []).join(", "))}</td>`).join("") +
        `<td class="wrapok small">${(e.b["osx-arm64"] || []).map((b) => D.byName.has(b) ? `<a href="${pkgLink(b)}">${esc(b)}</a>` : esc(b)).join(", ")}</td></tr>`).join("") + `</tbody>`;
    $("#pipe-more").innerHTML = rows.length > limit ? `Showing ${limit} of ${fmt(rows.length)}. <a href="#pipelines" id="pipe-all">Show all</a>` : `${fmt(rows.length)} steps.`;
    const all = $("#pipe-all");
    if (all) all.addEventListener("click", (ev) => { ev.preventDefault(); limit = 1e9; draw(); });
  };
  ["#pipe-q", "#pipe-src", "#pipe-st"].forEach((x) => $(x).addEventListener("input", () => { limit = 200; draw(); }));
  draw();
}

// ---------------------------------------------------------------- blockers
function blockers() {
  if (blockers.done) return;
  blockers.done = true;
  const tally = (armIdx, refIdx, plat) => {
    const m = new Map();
    for (const e of D.env.envs) {
      if (!GOOD.has(e.s[refIdx]) || GOOD.has(e.s[armIdx]) || e.s[armIdx] === "E") continue;
      for (const b of e.b[plat] || []) { if (!b.startsWith("__")) m.set(b, (m.get(b) || 0) + 1); }
    }
    return [...m].sort((a, b) => b[1] - a[1]).slice(0, 60);
  };
  const table = (rows) => `<thead><tr><th>Package</th><th class="num">Pipeline steps blocked</th></tr></thead><tbody>` +
    rows.map(([b, n]) => `<tr><td class="name">${D.byName.has(b) ? `<a href="${pkgLink(b)}">${esc(b)}</a>` : esc(b)}</td><td class="num">${n}</td></tr>`).join("") + `</tbody>`;
  $("#blk-mac").innerHTML = table(tally(3, 2, "osx-arm64"));
  $("#blk-lin").innerHTML = table(tally(1, 0, "linux-aarch64"));
}

// ---------------------------------------------------------------- news
function news() {
  const n = D.news || [];
  if (!n.length) {
    $("#news").innerHTML = `<p>Nightly tracking started on ${esc(D.sum?.snapshot || "")}. Changes will appear here as they happen.</p>`;
    return;
  }
  const days = new Map();
  for (const x of n) { if (!days.has(x.date)) days.set(x.date, []); days.get(x.date).push(x); }
  $("#news").innerHTML = [...days].map(([d, xs]) => `<div class="newsday"><h3>${esc(d)}</h3><ul>` +
    xs.slice(0, 200).map((x) => `<li>${x.kind === "pkg" ? `<a href="${pkgLink(x.unit)}">${esc(x.text)}</a>` : esc(x.text)}</li>`).join("") +
    (xs.length > 200 ? `<li class="muted">… and ${xs.length - 200} more</li>` : "") + `</ul></div>`).join("");
}

// ---------------------------------------------------------------- environment check
function parseEnv(text) {
  const deps = [];
  let inDeps = false, depIndent = -1;
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.replace(/#.*$/, "");
    if (!line.trim()) continue;
    const indent = line.search(/\S/);
    if (/^\s*dependencies\s*:/.test(line)) { inDeps = true; depIndent = -1; continue; }
    if (inDeps && indent === 0 && !line.trim().startsWith("-")) inDeps = false;
    if (!inDeps) continue;
    const m = line.match(/^(\s*)-\s*(.+?)\s*$/);
    if (!m) continue;
    if (depIndent < 0) depIndent = m[1].length;
    if (m[1].length > depIndent) continue;          // the pip: sub-list
    const spec = m[2].replace(/^["']|["']$/g, "");
    if (/^pip\s*(:|$)/.test(spec)) continue;
    const s = spec.split("::").pop().trim();
    const nm = (s.match(/^[A-Za-z0-9_.\-]+/) || [""])[0].toLowerCase();
    if (!nm) continue;
    const vm = s.slice(nm.length).trim().match(/^(==?)\s*([^\s,|<>=*]+)(\*)?/);
    deps.push({ spec: s, name: nm, version: vm && !vm[3] ? vm[2] : null });
  }
  return deps;
}
function check() {
  if (check.done) return;
  check.done = true;
  $("#check-go").addEventListener("click", () => {
    const deps = parseEnv($("#check-in").value);
    if (!deps.length) { $("#check-out").innerHTML = `<p>No dependencies found. Paste the whole file, including the <code>dependencies:</code> line.</p>`; return; }
    const rows = [], block = { "osx-arm64": [], "linux-aarch64": [] }, sugg = { "osx-arm64": [], "linux-aarch64": [] };
    for (const d of deps) {
      const p = D.byName.get(d.name);
      if (!p) { rows.push(`<tr><td>${esc(d.spec)}</td><td colspan="${COLS.length}" class="muted small">not in this table (most conda-forge packages build for every platform; the command below checks it)</td><td></td></tr>`); continue; }
      let i = 0, note = "newest tested";
      if (d.version) {
        i = p.v.findIndex((v) => v[0] === d.version || v[0].startsWith(d.version + "."));
        note = i >= 0 ? `tested ${p.v[i][0]}` : `${d.version} not tested; showing newest`;
        if (i < 0) i = 0;
      }
      for (const pl of ["osx-arm64", "linux-aarch64"]) {
        const c = code(p, pl, i);
        if (!GOOD.has(c) && c !== "-") {
          block[pl].push(d.name);
          const ok = p.v.find((v) => GOOD.has(v[1][COLS.indexOf(pl)]));
          sugg[pl].push(ok ? `${d.name}=${ok[0]}` : `${d.name}: no tested version installs`);
        }
      }
      rows.push(`<tr><td class="name"><a href="${pkgLink(p.n)}">${esc(d.spec)}</a></td>` + COLS.map((c) => `<td>${chip(code(p, c, i), pkgLink(p.n))}</td>`).join("") + `<td class="small muted">${esc(note)}</td></tr>`);
    }
    const summary = ["osx-arm64", "linux-aarch64"].map((pl) => block[pl].length
      ? `<p><b>${PNAME[pl]}:</b> blocked by ${block[pl].map(esc).join(", ")}. Versions that installed in our runs: <code>${sugg[pl].map(esc).join("  ")}</code></p>`
      : `<p><b>${PNAME[pl]}:</b> no blocker among the packages in this table.</p>`).join("");
    const cmd = (pl) => `micromamba create -n check --dry-run --platform ${pl} --override-channels -c conda-forge -c bioconda --strict-channel-priority -f environment.yml`;
    $("#check-out").innerHTML = summary +
      `<div class="tablewrap"><table><thead><tr><th>Package</th>${COLS.map((c) => `<th>${PNAME[c]}</th>`).join("")}<th>Version</th></tr></thead><tbody>${rows.join("")}</tbody></table></div>` +
      `<h2>Confirm on your own machine (a full solve, ~1 minute)</h2><pre>${esc(cmd("osx-arm64"))}</pre><pre>${esc(cmd("linux-aarch64"))}</pre>` +
      `<p class="note small">A lookup can miss conflicts between packages; the command above solves the whole file for that platform without installing anything.</p>`;
  });
}

// ---------------------------------------------------------------- about
function about() {
  if (about.done) return;
  about.done = true;
  const s = D.sum, cal = s.calibration;
  const vp = Object.entries(s.virtual_packages).map(([k, v]) => `<li><b>${PNAME[k]}</b> (${k}): ${v.map(esc).join(", ")}</li>`).join("");
  const calRows = cal ? Object.entries(cal.platforms).map(([pl, p]) =>
    `<tr><td>${PNAME[pl]}</td><td>${esc(p.runner)}</td><td class="num"><b>${p.agree}/${p.checked}</b></td><td class="num">${p.installed}</td><td class="num">${p.failed}</td></tr>`).join("") : "";
  $("#about").innerHTML = `
  <h2>About this table</h2>
  <p>Apple Silicon Macs (M1 and later) run tools built for Intel Macs through a translator called Rosetta 2, often without the user noticing. <b>macOS 27 is the last macOS with full Rosetta 2</b>; from macOS 28 (autumn 2027) it is kept only for a small set of games, so bioconda tools that have only an Intel-Mac build will stop working on Macs. In bioconda, Apple Silicon and arm64 Linux builds are opt-in per recipe, and many popular recipes never opted in.</p>
  <p>This table answers, for each tool: <b>does it install natively on Apple Silicon and on arm64 Linux, at which versions, and if not, why, and which version to use instead?</b> It also covers every nf-core module and Snakemake wrapper, so you can check a whole pipeline. It is re-checked every night, and changes are kept as history in <a href="#news">What's new</a>.</p>
  <p><b>How to use it:</b> search a tool in <a href="#tools">All tools</a>, see what doesn't work yet in <a href="#gaps">Don't work on Apple Silicon</a>, or paste your <code>environment.yml</code> into <a href="#check">Check an environment</a>. Every result links to the log it came from.</p>
  <p><b>Who made it:</b> <a href="https://yuvalbloch.com/">Yuval Bloch</a>. The code was written with the help of AI (Claude). No result is typed in by hand or decided by the AI: every ✓ and ✗ comes from a real solver run or a real install, stored with its command, date and log. The code and all data are open on <a href="https://github.com/yuvalbloch/bioconda-arm-readiness">GitHub</a>. <span class="small"><a href="https://yuvalbloch.com/wild-west/bioconda-apple-silicon/">The story behind this table →</a></span></p>
  <h2>How results are produced</h2>
  <ul>
    <li><b>What is tested:</b> ${fmt(s.packages)} bioconda packages (the 1,000 most-downloaded plus every one used by an nf-core module or Snakemake wrapper), newest version plus up to 3 earlier ones (${fmt(s.package_versions)} package versions), and ${fmt(s.envs)} real nf-core / Snakemake environment files.</li>
    <li><b>How:</b> each package version is solved alone into an empty environment with <a href="https://github.com/conda/rattler">rattler</a> (the solver behind pixi), channels conda-forge + bioconda with strict priority (bioconda's documented setup), for each platform plus noarch. ${fmt(s.solve_cells)} solves a night.</li>
    <li><b>The machines we model</b> (changing these changes results; newer machines can only do better):<ul>${vp}</ul></li>
    <li><b>Statuses come from fixed rules</b> over the stored solver output (<a href="https://github.com/yuvalbloch/bioconda-arm-readiness/blob/main/farm/classify.py">classify.py</a>). “Why” labels come from the bioconda recipe (<code>extra: additional-platforms</code>); “Use instead” is only ever a version that installed in our own runs. Downloads and pipeline use only decide what is tested.</li>
    <li><b>History:</b> results are never overwritten. A re-check with the same answer only moves its “checked” date; a different answer is stored as a new result with its log, and shows up in <a href="#news">What's new</a>.</li>
  </ul>
  <h2>Calibration: solver vs. real installs</h2>
  ${cal ? `<p>On ${esc(cal.date)}, ${cal.cases} cases (picked by a fixed rule from every result group) were solved by the farm's solver and then really installed with <code>micromamba create</code> on GitHub's real arm64 machines, minutes apart. <a href="${esc(cal.run_url)}">Workflow run with all logs</a>.</p>
  <div class="tablewrap"><table><thead><tr><th>Platform</th><th>Machine</th><th class="num">Solver agrees with real install</th><th class="num">Installed</th><th class="num">Failed</th></tr></thead><tbody>${calRows}</tbody></table></div>
  <p class="note small">A real install that succeeds doesn't prove the tool runs; that is what the weekly real-machine test below checks.</p>` : "<p>Not run yet.</p>"}
  <h2>On real machines: install + the package's own test</h2>
  ${s.smoke && Object.keys(s.smoke["osx-arm64"] || {}).length ? `<p>Every week, the most-downloaded tools that the table marks ✓ are installed for real on GitHub's Apple Silicon (macos-15) and arm64 Linux (ubuntu-24.04-arm) machines, and the test that bioconda's own CI ran when it built the package is run again (it ships inside the package). Last run: ${esc(s.smoke_date || "")}.</p>
  <div class="tablewrap"><table><thead><tr><th>Platform</th><th class="num">✓✓ passed its test</th><th class="num">✓i installed, no test</th><th class="num">✗t test failed</th><th class="num">✗i install failed</th></tr></thead><tbody>${
    ["osx-arm64", "linux-aarch64"].map((pl) => { const c = s.smoke[pl] || {}; return `<tr><td>${PNAME[pl]}</td><td class="num">${c.runs || 0}</td><td class="num">${c.installs || 0}</td><td class="num">${c.test_fail || 0}</td><td class="num">${c.install_fail || 0}</td></tr>`; }).join("")
  }</tbody></table></div><p class="note small">In the tools table these show as a small mark next to the status: ${Object.values(SMOKE).slice(0, 4).map((x) => `<b>${x[0]}</b> ${esc(x[1])}`).join("; ")}.</p>` : "<p>The first weekly run hasn't finished yet.</p>"}
  <h2>Limits</h2>
  <ul>
    <li>“✓” means a consistent set of builds exists and (as calibration shows) installs. Only the tools marked ✓✓ were also run on a real machine, and a package's own test is usually a quick check such as <code>--help</code>, not a full analysis.</li>
    <li>Pipeline steps are tested as written in their environment file. Many also run from containers instead of conda.</li>
    <li>Packages that conda-forge serves (Perl modules, biopython, mafft…) are tested at conda-forge's newest version, because with the standard setup that is what users get.</li>
  </ul>
  <p>Full method: <a href="https://github.com/yuvalbloch/bioconda-arm-readiness/blob/main/docs/methodology.md">docs/methodology.md</a>. Found a wrong result? <a href="https://github.com/yuvalbloch/bioconda-arm-readiness/issues/new?title=Wrong%20result">Open an issue</a> with the tool, version and platform.</p>`;
}

load();
