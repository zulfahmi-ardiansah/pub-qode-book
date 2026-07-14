/* Qodebook — theme, tree, list controls, download popup.
   Every init is a no-op on pages without its markup, so one script serves them all. */

/* Level names and copy come from the server (see base.html), so the client renders
   in whatever language the page was rendered in. Never hardcode either here. */
const LEVELS = window.LEVELS;
const T = window.T;

/* Fills {placeholders} the same way Python's str.format does, so one string table
   serves both sides. */
const fmt = (key, values = {}) =>
  String(T[key] ?? key).replace(/\{(\w+)\}/g, (_, k) => (k in values ? values[k] : `{${k}}`));

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const nf = new Intl.NumberFormat("en-US");

function esc(value) {
  if (value === null || value === undefined) return "";
  return String(value).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

function debounce(fn, ms) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

async function getJSON(url) {
  const res = await fetch(url);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `Request failed (${res.status})`);
  return body;
}

/* A UNSPSC code spends two digits per level, so everything past the node's own
   level is placeholder zeros — dim them and the column shows depth at a glance.
   Mirrors code_split() in app.py. */
function codeHTML(name, code, level) {
  if (name !== "unspsc" || level < 1) return esc(code);
  const cut = level * 2;
  return `${esc(code.slice(0, cut))}<span class="code-dim">${esc(code.slice(cut))}</span>`;
}

/* ---------------- Theme ---------------- */
function initTheme() {
  const toggle = $("#theme-toggle");
  if (!toggle) return;
  toggle.addEventListener("click", () => {
    const dark = document.documentElement.classList.toggle("dark");
    localStorage.setItem("qodebook-theme", dark ? "dark" : "light");
  });
}

/* ---------------- Nav drawer (narrow screens only) ----------------
   The panel is a plain block the stylesheet hides above 720px, so this only ever
   flips one class — no width checks, no measuring. */
function initNav() {
  const burger = $("#nav-open");
  const nav = $("#site-nav");
  if (!burger || !nav) return;

  const set = (open) => {
    nav.classList.toggle("is-open", open);
    burger.setAttribute("aria-expanded", String(open));
  };

  burger.addEventListener("click", () => set(burger.getAttribute("aria-expanded") !== "true"));

  // A tab either navigates away or opens the download popup; both make the drawer
  // dead weight. The theme and language controls stay, so you can see what they did.
  nav.addEventListener("click", (e) => {
    if (e.target.closest(".tab")) set(false);
  });

  document.addEventListener("click", (e) => {
    if (!nav.contains(e.target) && !burger.contains(e.target)) set(false);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") set(false);
  });
  window.addEventListener("resize", () => {
    if (window.innerWidth > 720) set(false);
  });
}

/* ---------------- Browse: lazy tree + search ---------------- */
function initBrowse() {
  const page = $("[data-tree-root]");
  if (!page) return;

  const name = page.dataset.treeRoot;
  const treeSection = $("#tree-section");
  const results = $("#search-results");
  const list = $("#search-list");
  const count = $("#search-count");

  const branchHTML = (rows) =>
    rows
      .map(
        (row) => `
        <li role="treeitem" ${row.has_children ? 'aria-expanded="false"' : ""} data-code="${esc(row.code)}">
          <div class="tree-row">
            ${
              row.has_children
                ? `<button type="button" class="tree-toggle" aria-label="Expand ${esc(row.code)}">
                     <svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.4" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7"/></svg>
                   </button>`
                : `<span class="tree-gap"></span>`
            }
            <a href="/browse/${name}/${encodeURIComponent(row.code)}" class="tree-link">
              <span class="code">${codeHTML(name, row.code, row.level)}</span>
              <span class="tree-name">${esc(row.title)}</span>
              <span class="lvl" data-level="${row.level}">${esc(LEVELS[name][row.level])}</span>
            </a>
          </div>
          <ul class="hidden" role="group"></ul>
        </li>`
      )
      .join("");

  page.addEventListener("click", async (e) => {
    const toggle = e.target.closest(".tree-toggle");
    if (!toggle) return;

    const item = toggle.closest("[role='treeitem']");
    const children = $("ul", item);
    const open = item.getAttribute("aria-expanded") === "true";

    if (open) {
      item.setAttribute("aria-expanded", "false");
      children.classList.add("hidden");
      return;
    }
    item.setAttribute("aria-expanded", "true");
    children.classList.remove("hidden");

    if (item.dataset.loaded) return;
    children.innerHTML = `<li class="tree-wait">${esc(T.loading)}</li>`;
    try {
      const data = await getJSON(`/api/tree/${name}?parent=${encodeURIComponent(item.dataset.code)}`);
      children.innerHTML = branchHTML(data.rows);
      item.dataset.loaded = "1";
    } catch (err) {
      children.innerHTML = `<li class="tree-wait">${esc(err.message)}</li>`;
    }
  });

  const run = debounce(async (q) => {
    if (!q.trim()) {
      results.classList.add("hidden");
      treeSection.classList.remove("hidden");
      return;
    }
    treeSection.classList.add("hidden");
    results.classList.remove("hidden");
    count.textContent = T.searching;
    list.innerHTML = `<p class="row-empty">${esc(T.searching)}</p>`;

    try {
      const data = await getJSON(`/api/search/${name}?q=${encodeURIComponent(q.trim())}`);
      count.textContent = data.total
        ? `${nf.format(data.total)}${
            data.total > data.rows.length ? ` · ${fmt("first", { n: data.rows.length })}` : ""
          }`
        : T.none;
      list.innerHTML = data.rows.length
        ? data.rows
            .map(
              (row) => `
          <a href="/browse/${name}/${encodeURIComponent(row.code)}" class="row">
            <span class="code">${codeHTML(name, row.code, row.level)}</span>
            <span class="row-title">${esc(row.title)}</span>
            <span class="lvl" data-level="${row.level}">${esc(LEVELS[name][row.level])}</span>
          </a>`
            )
            .join("")
        : `<p class="row-empty">${esc(fmt("no_match", { q: q.trim() }))}</p>`;
    } catch (err) {
      count.textContent = "";
      list.innerHTML = `<p class="row-empty">${esc(err.message)}</p>`;
    }
  }, 250);

  $("#tree-search").addEventListener("input", (e) => run(e.target.value));
}

/* ---------------- Definitions: clamp, with a toggle only when it overflows ---------------- */
function initClamp() {
  $$("[data-clamp]").forEach((el) => {
    el.classList.add("is-clamped");
    if (el.scrollHeight <= el.clientHeight + 1) {
      el.classList.remove("is-clamped");   // short enough — nothing to reveal
      return;
    }

    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "link-btn";
    toggle.style.padding = "10px 0 0";
    el.after(toggle);

    let open = false;
    const render = () => {
      el.classList.toggle("is-clamped", !open);
      toggle.textContent = open ? T.show_less : T.read_full;
      toggle.setAttribute("aria-expanded", String(open));
    };
    toggle.addEventListener("click", () => { open = !open; render(); });
    render();
  });
}

/* ---------------- The rail's children: show the first few, fold the rest ---------------- */
const RAIL_VISIBLE = 3;

function initCollapse() {
  $$("[data-collapse]").forEach((container) => {
    const items = $$("[data-collapse-item]", container);
    const extras = items.slice(RAIL_VISIBLE);
    if (!extras.length) return;

    const holder = document.createElement("li");
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "link-btn";
    toggle.style.padding = "8px 0 0";
    holder.appendChild(toggle);
    items[items.length - 1].after(holder);

    let open = false;
    const render = () => {
      extras.forEach((el) => el.classList.toggle("hidden", !open));
      toggle.textContent = open ? T.show_less : fmt("n_more", { n: extras.length });
      toggle.setAttribute("aria-expanded", String(open));
    };
    toggle.addEventListener("click", () => { open = !open; render(); });
    render();
  });
}

/* ---------------- Long lists: search + pages of five ----------------
   Rows are already in the DOM (a node has at most ~99 siblings and a few dozen
   links), so filtering and paging are local — no round trips. */
const PAGE = 5;

function initLists() {
  $$("[data-list]").forEach((container) => {
    const rows = $$("[data-list-item]", container);
    if (rows.length <= PAGE) return;

    // The noun arrives already plural and already translated — English and
    // Indonesian don't agree on how to make a plural, so the template says it
    // rather than the script guessing.
    const noun = container.dataset.listNoun || "";
    const text = new Map(rows.map((el) => [el, el.textContent.toLowerCase()]));

    const find = document.createElement("div");
    find.className = "list-find";
    find.innerHTML = `
      <div class="find">
        <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path stroke-linecap="round" d="M20 20l-3.5-3.5"/></svg>
        <input type="search" class="field" autocomplete="off" placeholder="${esc(
          fmt("filter", { n: rows.length, noun })
        )}">
      </div>`;
    container.before(find);

    const empty = document.createElement("p");
    empty.className = "row-empty hidden";
    container.append(empty);

    const pager = document.createElement("div");
    pager.className = "pager";
    pager.innerHTML = `
      <button type="button" class="btn" data-prev>${esc(T.prev)}</button>
      <span class="pager-count" aria-live="polite"></span>
      <button type="button" class="btn" data-next>${esc(T.next)}</button>`;
    container.after(pager);

    const input = $("input", find);
    const prev = $("[data-prev]", pager);
    const next = $("[data-next]", pager);
    const count = $(".pager-count", pager);

    let page = 1;
    let matches = rows;
    let query = "";

    const render = () => {
      const pages = Math.max(1, Math.ceil(matches.length / PAGE));
      page = Math.min(page, pages);
      const start = (page - 1) * PAGE;
      const shown = matches.slice(start, start + PAGE);

      rows.forEach((el) => el.classList.toggle("hidden", !shown.includes(el)));
      empty.textContent = fmt("nothing_matches", { q: query });
      empty.classList.toggle("hidden", matches.length > 0);
      pager.classList.toggle("hidden", matches.length === 0);

      count.textContent = matches.length
        ? `${T.show} ${start + 1}–${start + shown.length} ${T.of} ${matches.length}`
        : "";
      prev.disabled = page <= 1;
      next.disabled = page >= pages;
    };

    input.addEventListener(
      "input",
      debounce((e) => {
        query = e.target.value.trim();
        const q = query.toLowerCase();
        matches = q ? rows.filter((el) => text.get(el).includes(q)) : rows;
        page = 1;
        render();
      }, 150)
    );
    prev.addEventListener("click", () => { page -= 1; render(); });
    next.addEventListener("click", () => { page += 1; render(); });

    // Open on the page holding the current entry, so "you are here" is never
    // stranded behind pagination.
    const current = rows.findIndex((el) => el.hasAttribute("data-list-current"));
    if (current >= 0) page = Math.floor(current / PAGE) + 1;
    render();
  });
}

/* ---------------- Download popup ---------------- */
function initDownload() {
  const modal = $("#download-modal");
  const opener = $("#download-open");
  if (!modal || !opener) return;

  function open() {
    modal.classList.remove("hidden");
    document.body.style.overflow = "hidden";
    // The first download link, so a keyboard lands inside the dialog, not behind it.
    modal.querySelector("a")?.focus();
  }

  function close() {
    modal.classList.add("hidden");
    document.body.style.overflow = "";
    opener.focus();
  }

  /* The server builds a 158k-row file before a single byte comes back, so a plain
     link would sit there looking broken. Fetch it ourselves, spin the button
     while we wait, then hand the finished blob to the browser to save. */
  async function pull(link) {
    if (modal.dataset.busy) return;

    const row = link.closest(".dl-row");
    const error = $(".dl-error", row);
    const links = $$("a[download]", modal);
    const label = link.textContent;

    modal.dataset.busy = "1";
    error.classList.add("hidden");
    links.forEach((other) => other.classList.toggle("is-off", other !== link));
    link.classList.add("is-busy");
    link.innerHTML = `<span class="spinner" aria-hidden="true"></span>${esc(T.download_working)}`;

    try {
      const res = await fetch(link.href);
      if (!res.ok) throw new Error(`Request failed (${res.status})`);
      const blob = await res.blob();

      const url = URL.createObjectURL(blob);
      const save = document.createElement("a");
      save.href = url;
      save.download = link.getAttribute("download");
      save.click();
      URL.revokeObjectURL(url);
      close();
    } catch (err) {
      error.textContent = T.download_failed;
      error.classList.remove("hidden");
    } finally {
      delete modal.dataset.busy;
      link.classList.remove("is-busy");
      link.textContent = label;
      links.forEach((other) => other.classList.remove("is-off"));
    }
  }

  opener.addEventListener("click", open);
  $$("[data-close-modal]", modal).forEach((el) => el.addEventListener("click", close));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !modal.classList.contains("hidden") && !modal.dataset.busy) close();
  });
  $$("a[download]", modal).forEach((link) =>
    link.addEventListener("click", (e) => {
      e.preventDefault();
      pull(link);
    })
  );
}

initTheme();
initNav();
initBrowse();
initClamp();
initCollapse();
initLists();
initDownload();
