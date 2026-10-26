/* Hands-free demo runner. Injected into the app page by tabs/autopilot.py (window.__lvAutoData holds the script).
   It drives the real screens like a person would: switches role through the header role switch, opens pages from the
   top navigation, scrolls, clicks and keys every field in. Nothing here knows about business rules: every target,
   value, delay and explanation comes from the script that Python built from data/autopilot.json. */
(function () {
  const DATA = window.__lvAutoData;
  if (!DATA) return;
  if (window.__lvAuto && window.__lvAuto.runId === DATA.runId) return;       // this run is already playing
  if (window.__lvAuto) window.__lvAuto.stop();

  const D = document, T = DATA.timing;
  const state = { stopped: false, paused: false, passed: 0, failed: 0, missed: [] };
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const norm = (s) => (s || "").replace(/\s+/g, " ").trim().toLowerCase();

  // ------------------------------------------------------------------ overlay
  const css = `
  #lv-auto-cursor{position:fixed;z-index:2147483000;width:16px;height:16px;border-radius:50%;background:#0070F2;border:2px solid #fff;
    box-shadow:0 0 0 3px rgba(0,112,242,.35);pointer-events:none;transition:left .12s,top .12s;left:-40px;top:-40px}
  #lv-auto-ring{position:fixed;z-index:2147482990;border:2px solid #0070F2;border-radius:8px;box-shadow:0 0 0 4px rgba(0,112,242,.18);
    pointer-events:none;transition:all .12s;display:none}
  #lv-auto-tag{position:fixed;z-index:2147483000;display:none;background:#AA0808;color:#fff;font:700 11px "72","Segoe UI",sans-serif;
    padding:3px 10px;border-radius:10px;pointer-events:none;box-shadow:0 2px 6px rgba(19,30,41,.3)}
  #lv-auto-flag{position:fixed;z-index:2147483000;left:50%;bottom:104px;transform:translateX(-50%);width:min(680px,92vw);background:#fff;
    border:1px solid #BCC3CA;border-left:6px solid #0070F2;border-radius:10px;box-shadow:0 8px 28px rgba(19,30,41,.22);padding:12px 16px;
    font:14px/1.45 "72","Segoe UI",sans-serif;color:#131E29;display:none}
  #lv-auto-flag .kind{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;padding:2px 8px;
    border-radius:10px;color:#fff;background:#0070F2;margin-bottom:6px}
  #lv-auto-flag.security{border-left-color:#7858FF}#lv-auto-flag.security .kind{background:#7858FF}
  #lv-auto-flag.test-pass{border-left-color:#256F3A}#lv-auto-flag.test-pass .kind{background:#256F3A}
  #lv-auto-flag.test-fail{border-left-color:#AA0808}#lv-auto-flag.test-fail .kind{background:#AA0808}
  #lv-auto-flag .backed{margin-top:6px;font-size:12px;color:#556B82}
  #lv-auto-panel{position:fixed;z-index:2147483000;right:16px;bottom:12px;display:flex;flex-direction:column;gap:7px;background:#131E29;color:#fff;
    border-radius:16px;padding:9px 10px 8px 14px;font:12px "72","Segoe UI",sans-serif;box-shadow:0 4px 14px rgba(19,30,41,.3);width:min(620px,92vw)}
  #lv-auto-panel .bar{display:flex;gap:3px;height:10px;align-items:stretch}
  #lv-auto-panel .seg{flex:1;border-radius:3px;background:rgba(255,255,255,.22);cursor:pointer;min-width:3px}
  #lv-auto-panel .seg.done{background:#0070F2}#lv-auto-panel .seg.now{background:#fff}
  #lv-auto-panel .seg.gap{margin-left:7px}
  #lv-auto-panel .row{display:flex;gap:8px;align-items:center}
  #lv-auto-panel .where{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  #lv-auto-panel button{background:#fff;color:#131E29;border:0;border-radius:14px;padding:4px 12px;font:600 12px "72","Segoe UI",sans-serif;cursor:pointer}
  #lv-auto-panel button.nav{background:#2A3B4D;color:#fff}
  #lv-auto-panel button.stop{background:#AA0808;color:#fff}`;
  const style = D.createElement("style");
  style.id = "lv-auto-style";
  style.textContent = css;
  D.head.appendChild(style);
  const mk = (id, tag) => { const e = D.createElement(tag || "div"); e.id = id; D.body.appendChild(e); return e; };
  const cursor = mk("lv-auto-cursor"), ring = mk("lv-auto-ring"), flagBox = mk("lv-auto-flag"), panel = mk("lv-auto-panel"),
    tag = mk("lv-auto-tag");
  tag.textContent = "Look here";
  panel.innerHTML = '<div class="bar"></div><div class="row"><button class="nav back" title="Replay the previous step">&#9664; Back</button>' +
    '<span class="where"></span><button class="nav ff" title="Speed up the demo: 1x, 2x or 3x">&#9654;&#9654; 1x</button>' +
    '<button class="pause">Pause</button><button class="stop">Stop</button></div>';
  const where = panel.querySelector(".where"), bar = panel.querySelector(".bar");
  const flat = [];                                           // every step of the run, in order, with the story it belongs to
  DATA.segments.forEach((seg, si) => seg.steps.forEach((step, k) => flat.push({ seg, step, first: k === 0 && si > 0 })));
  flat.forEach((f, idx) => {                                  // one slice of the progress bar per step; click one to jump there
    const s = D.createElement("div");
    s.className = "seg" + (f.first ? " gap" : "");
    s.title = f.seg.title + ": " + f.step.title;
    s.onclick = () => request({ kind: "seek", to: idx });
    bar.appendChild(s);
  });
  const setProgress = (i) => [...bar.children].forEach((s, k) => { s.className = "seg" + (flat[k].first ? " gap" : "") + (k < i ? " done" : k === i ? " now" : ""); });
  const resumeIfPaused = () => { if (state.paused) { state.paused = false; panel.querySelector(".pause").textContent = "Pause"; } };
  function request(j) {                                       // a Back, Next or bar click: picked up at the next safe point
    if (state.busy || state.stopped) return;
    resumeIfPaused();
    state.jump = j;
  }
  panel.querySelector(".back").onclick = () => request({ kind: "back" });
  panel.querySelector(".ff").onclick = (e) => {              // fast-forward: 1x, 2x, 3x, back to 1x. Every pause shortens with it.
    state.speed = state.speed === 3 ? 1 : (state.speed || 1) + 1;
    e.target.innerHTML = "&#9654;&#9654; " + state.speed + "x";
  };
  panel.querySelector(".pause").onclick = (e) => { state.paused = !state.paused; e.target.textContent = state.paused ? "Resume" : "Pause"; };
  panel.querySelector(".stop").onclick = () => api.stop();

  function showFlag(kind, text, backed, cls) {
    const label = { how: "How it works", security: "Security", test: "Check" }[kind] || kind;
    flagBox.className = cls || kind;
    flagBox.innerHTML = '<span class="kind"></span><div class="txt"></div>' + (backed ? '<div class="backed"></div>' : "");
    flagBox.querySelector(".kind").textContent = label;
    flagBox.querySelector(".txt").textContent = text;
    if (backed) flagBox.querySelector(".backed").textContent = "Backed by test: " + backed;
    flagBox.style.display = "block";
  }
  const hideFlag = () => { flagBox.style.display = "none"; };
  const clearRow = () => { tracked = null; ring.style.display = "none"; tag.style.display = "none"; };

  // ------------------------------------------------------------------ waiting
  const spd = () => (state.lockSpeed ? 1 : state.speed || 1);   // switching role always runs at normal speed: its dropdowns need the time
  const JUMP = { jump: true }, STOP = { stop: true };         // thrown to leave the step being played
  async function gate() {
    while (state.paused && !state.stopped && !state.jump) await sleep(150);
    if (state.stopped) throw STOP;
    if (state.jump && !state.busy) throw JUMP;
  }
  const hard = (ms) => sleep(state.fast ? Math.min(ms, 20) : ms / spd());      // a pause right after an action: never cut short, so the action is not repeated
  async function wait(ms) {                                   // a pause that Next, Back or Stop can cut short at once
    const end = Date.now() + (state.fast ? Math.min(ms, 20) : ms / spd());
    while (Date.now() < end) {
      if (state.stopped || (state.jump && !state.busy)) break;
      await sleep(Math.min(80, end - Date.now()));
    }
    await gate();
  }
  const app = () => D.querySelector('[data-testid="stApp"]');
  async function idle() {                                  // the app has finished re-running and the screen is steady
    await sleep(150);
    const t0 = Date.now();
    let calm = 0;
    while (Date.now() - t0 < 25000 && calm < 4) {
      calm = app() && app().getAttribute("data-test-script-state") === "running" ? 0 : calm + 1;
      await sleep(80);
    }
  }

  // ------------------------------------------------------------------ finding things
  const visible = (e) => {
    const r = e.getBoundingClientRect();
    if (!(r.width > 0 && r.height > 0 && getComputedStyle(e).visibility !== "hidden")) return false;
    for (let d = e.closest("details"); d; d = d.parentElement && d.parentElement.closest("details")) {
      if (!d.open && !e.closest("summary")) return false;   // inside a collapsed section: laid out, but not on screen
    }
    return true;
  };
  const CLICKABLE = 'button,[role="tab"],[role="radio"],label,summary,a[href],[role="option"]';
  function interactive(el) {
    if (!el) return null;
    if (el.matches("button,input,textarea,summary,[role=combobox],[role=tab]")) return el;
    return el.querySelector("button,input,textarea,summary,[role=combobox]") || el;
  }
  function scopeOf(t) {
    if (!t || !(t.in || t.inp)) return D;
    return (t.inp ? D.querySelector('[class*="st-key-' + t.inp + '"]') : D.querySelector(".st-key-" + t.in)) || D;
  }
  function find(t) {
    const scope = scopeOf(t);
    let el = null;
    if (t.key) el = D.querySelector(".st-key-" + t.key);
    else if (t.keyp) el = D.querySelector('[class*="st-key-' + t.keyp + '"]');
    else if (t.css) el = scope.querySelector(t.css);
    else if (t.any) {                                       // a caption, note or banner by its words
      const want = norm(t.any);
      el = [...scope.querySelectorAll('[data-testid="stCaptionContainer"],[data-testid="stAlert"],.lv-banner,[data-testid="stMarkdownContainer"]')]
        .find((e) => visible(e) && norm(e.textContent).includes(want));
    } else if (t.label) {
      const want = norm(t.label);
      const lab = [...scope.querySelectorAll('[data-testid="stWidgetLabel"]')].find((l) => visible(l) && norm(l.textContent).includes(want));
      el = lab && lab.closest('[data-testid^="st"][data-testid$="Input"],[data-testid="stTextArea"],[data-testid="stSelectbox"],[data-testid="stMultiSelect"],[data-testid="stRadio"],[data-testid="stNumberInput"]');
    } else if (t.text) {
      const want = norm(t.text);
      const all = [...scope.querySelectorAll(CLICKABLE)].filter((e) => visible(e) && !e.closest("#lv-auto-panel"));
      const pool = t.exact ? all.filter((e) => norm(e.textContent).replace(/^[a-z_]+\s(?=.)/, "") === want || norm(e.textContent) === want)
                           : all;
      el = pool.find((e) => norm(e.textContent) === want) || pool.find((e) => norm(e.textContent).includes(want));
      if (t.last) el = [...pool].reverse().find((e) => norm(e.textContent) === want || norm(e.textContent).includes(want)) || el;
      if (el && t.nth) el = pool.filter((e) => norm(e.textContent).includes(want))[t.nth] || el;
    }
    return el && visible(el) ? el : (el && !t.css ? el : null);
  }
  async function locate(t, optional) {
    const limit = optional ? 1500 : 8000, t0 = Date.now();
    while (Date.now() - t0 < limit) {
      await gate();
      if (state.stopped) return null;
      const el = find(t);
      if (el) return el;
      await sleep(120);
    }
    return null;
  }

  // ------------------------------------------------------------------ acting like a person
  function fire(el) {
    const r = el.getBoundingClientRect();
    const o = { bubbles: true, cancelable: true, clientX: r.x + r.width / 2, clientY: r.y + r.height / 2, button: 0, pointerType: "mouse", isPrimary: true };
    for (const t of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) {
      el.dispatchEvent(t.startsWith("pointer") ? new PointerEvent(t, o) : new MouseEvent(t, o));
    }
  }
  function point(el) {
    const r = el.getBoundingClientRect();
    cursor.style.left = r.x + Math.min(r.width / 2, 120) + "px";
    cursor.style.top = r.y + r.height / 2 + "px";
    Object.assign(ring.style, { display: "block", left: r.x - 4 + "px", top: r.y - 4 + "px", width: r.width + 8 + "px", height: r.height + 8 + "px" });
  }
  async function bring(el) {                                // scroll like a user would, only when the target is out of view
    if (el.closest('header,[data-testid="stHeader"],[data-testid="stPopoverBody"],[class*="st-key-demo_bar"]')) return;   // fixed bars never scroll
    const r = el.getBoundingClientRect();
    if (r.top >= 70 && r.bottom <= innerHeight - 140) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    await wait(T.scroll);
    let last = -1;
    for (let i = 0; i < 20 && el.getBoundingClientRect().top !== last; i++) { last = el.getBoundingClientRect().top; await sleep(60); }
  }
  async function aim(el) { await bring(el); point(el); }

  const unpoint = () => { tracked = null; ring.style.display = "none"; tag.style.display = "none"; };
  let tracked = null;
  function showRowTag(row, label) {                         // a pill beside the row so the eye lands on the field
    tag.textContent = label || "Look here";
    tag.style.background = "";                              // back to the default colour unless the caller sets one
    tracked = row;
    follow();
  }
  function follow() {                                       // the ring and pill stay on the row while the page settles or scrolls
    if (!tracked) return;
    if (!tracked.isConnected) { tracked = null; return; }
    const r = tracked.getBoundingClientRect();
    Object.assign(ring.style, { display: "block", left: r.x - 4 + "px", top: r.y - 4 + "px", width: r.width + 8 + "px", height: r.height + 8 + "px" });
    Object.assign(tag.style, { display: "block", left: Math.min(r.right - 96, innerWidth - 110) + "px", top: r.top - 12 + "px" });
  }
  const follower = setInterval(follow, 80);
  async function doClick(el) { await aim(el); fire(el); await hard(T.click); await idle(); unpoint(); }

  async function doType(el, value) {
    await aim(el);
    el.focus();
    const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const set = Object.getOwnPropertyDescriptor(proto, "value").set;
    const put = (v) => { set.call(el, v); el.dispatchEvent(new Event("input", { bubbles: true })); };
    put("");
    const step = state.fast ? 0 : Math.max(3, Math.min(T.type_char_ms, 600 / Math.max(value.length, 1))) / spd();
    for (const ch of String(value)) { put(el.value + ch); await sleep(step); }
    // Streamlit takes the value on Enter or when the field loses focus. Synthetic events are used because the browser
    // does not fire real focus events while the window is not the active one (a demo on a second screen, for example).
    const area = el.tagName === "TEXTAREA";
    el.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", keyCode: 13, which: 13, ctrlKey: area, bubbles: true }));
    el.dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
    el.blur();
    await hard(T.type);
    // Skipping ahead: a field's value travels with the next click, so there is no need to wait for each re-run in between.
    if (state.fast) await sleep(40); else await idle();
    unpoint();
  }

  async function chooseOption(widget, optionText) {         // Streamlit's dropdowns: open, then click the option by its words
    const inp = widget.querySelector("input[role=combobox]");
    if (!inp) return false;
    await aim(inp.closest('[role="group"]') || inp);
    const btn = (inp.closest('[role="group"]') || widget).querySelector("button");
    const want = norm(optionText);
    let opt = null;
    for (let attempt = 0; attempt < 3 && !opt; attempt++) {       // the list is drawn lazily: open it again if it did not appear
      if (!D.querySelector('[role="listbox"]')) fire(btn || inp);
      for (let i = 0; i < 20 && !opt; i++) {
        await sleep(80);
        const opts = [...D.querySelectorAll('[role="option"]')].filter(visible);
        opt = opts.find((o) => norm(o.textContent) === want) || opts.find((o) => norm(o.textContent).includes(want));
      }
    }
    if (!opt) return false;
    point(opt);
    fire(opt);
    if (widget.matches('[data-testid="stMultiSelect"]')) {          // a multi-select stays open after a pick: close it
      await sleep(300);
      inp.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", code: "Escape", keyCode: 27, bubbles: true }));
    }
    await hard(T.click);
    if (state.fast && !widget.closest('[class*="st-key-role_pop_"]') && !document.querySelector('[data-testid="stPopoverBody"]')) await sleep(60);
    else await idle();
    return true;
  }

  // ------------------------------------------------------------------ operations
  function say(o) {
    showFlag(o.kind || "how", o.text, o.backed);
    const ms = state.fast ? 120 : Math.min(T.max_flag_ms, Math.max(T.min_flag_ms, (o.text || "").length * T.read_ms_per_char));
    return wait(ms);
  }

  const OPS = {
    async flag(o) {
      if (o.field) {                                          // show the claim: scroll to the field's row and point at it
        const sel = 'tr[data-field="' + o.field + '"]';
        if (![...D.querySelectorAll(sel)].some(visible)) await OPS.reveal({ until: sel, open: "Details" });   // open the request if it is folded away
        const row = await locate({ css: sel }, true);
        if (row) {
          row.scrollIntoView({ behavior: "smooth", block: "center" });          // the scroll is part of the proof: let it be seen
          await wait(T.scroll + 400);
          await aim(row);
          const shown = row.querySelector("td").textContent.trim();
          const seen = /hidden for your role/i.test(shown) ? "hidden" : shown.includes("*") ? "masked" : "full";
          showRowTag(row, { masked: "Masked, not hidden", hidden: "Hidden", full: "Full value" }[seen]);
          tag.style.background = { masked: "#E76500", hidden: "#556B82", full: "#256F3A" }[seen];
          await wait(900);
          o.text += ' On this screen the row reads "' + shown + '".';
          if (o.expect && o.expect !== seen) o.pass = false;      // what is drawn must agree with what the engine says
        }
      }
      if (o.kind !== "test" || typeof o.pass !== "boolean") return say(o);       // a check already decided by the engine
      if (!state.replay) { o.pass ? state.passed++ : (state.failed++, state.missed.push(o.text)); }   // a replay must not count a check twice
      showFlag("test", (o.pass ? "PASS: " : "FAIL: ") + o.text, o.backed, o.pass ? "test-pass" : "test-fail");
      return wait(state.fast ? 120 : Math.min(T.max_flag_ms, Math.max(T.min_flag_ms, o.text.length * T.read_ms_per_char)));
    },
    async wait(o) { await wait(o.ms); },
    async look(o) {                                         // scroll to something on the page, ring it and explain it
      const el = await locate(o.t, o.optional);
      if (!el) return !o.optional && fail("Could not find " + JSON.stringify(o.t));
      await aim(el);
      showRowTag(el, o.label);
      const cell = el.querySelector && el.querySelector("td");                 // {row}: what this table row actually reads
      const box = el.querySelector && el.querySelector("input");                // {value}: what a dropdown currently shows
      await say({ kind: o.kind || "how", text: o.text.replace("{row}", cell ? cell.textContent.trim() : "").replace("{value}", box ? box.value : "") });
      clearRow();
    },
    async compare(o) {                                      // walk down every side-by-side table and point at each difference
      await idle();
      const tables = [...D.querySelectorAll("table.lv-compare")].filter(visible);
      if (!tables.length) return !o.optional && fail("No comparison table on screen");
      const cells = (r) => [...r.children].map((c) => c.textContent.trim());
      const title = (t) => {
        const s = t.closest('[data-testid="stExpander"]');
        return s ? s.querySelector("summary").textContent.replace(/^.*?Compare side by side with\s*/i, "").trim() : "the existing record";
      };
      const unknownText = (c) => /not on file|hidden for your role/i.test(c[2]);
      for (const table of tables) {
        const rows = [...table.querySelectorAll("tr[data-result]")];
        const bad = rows.filter((r) => r.dataset.result === "bad");
        const unknown = bad.filter((r) => unknownText(cells(r)));            // nothing to compare against is not a difference
        const differ = bad.filter((r) => !unknownText(cells(r)));
        const same = rows.filter((r) => r.dataset.result !== "bad");
        await aim(table);
        for (const r of bad) Object.assign(r.style, { outline: "2px solid #AA0808", outlineOffset: "-2px", background: "#FDECEC" });   // all the mismatches at once
        const names = (list) => list.map((r) => cells(r)[0]).join(", ") || "nothing";
        await say({ kind: "how", text: (o.text || "").replace("{diff}", names(differ)).replace("{unknown}", names(unknown)).replace("{same}", names(same)).replace("{record}", title(table)) });
        for (const r of bad) Object.assign(r.style, { outline: "", outlineOffset: "", background: "" });
      }
    },
    async scroll(o) {
      if (o.t) { const el = await locate(o.t, o.optional); if (el) { el.scrollIntoView({ behavior: "smooth", block: "center" }); await wait(T.scroll); } return; }
      window.scrollBy({ top: o.px || 400, behavior: "smooth" });
      const main = D.querySelector('[data-testid="stMain"]');
      if (main) main.scrollBy({ top: o.px || 400, behavior: "smooth" });
      await wait(T.scroll);
    },
    async click(o) {
      const el = await locate(o.t, o.optional);
      if (!el) return !o.optional && fail("Could not find " + JSON.stringify(o.t));
      if (o.unless && norm(el.textContent).includes(norm(o.unless))) return;
      await doClick(interactive(el));
    },
    async type(o) {
      const w = await locate(o.t, o.optional);
      if (!w) return !o.optional && fail("Could not find the field " + JSON.stringify(o.t));
      const box = (x) => (x.matches("input,textarea") ? x : x.querySelector("input:not([role=combobox]),textarea"));
      const el = box(w);
      if (!el) return fail("No text box inside " + JSON.stringify(o.t));
      await doType(el, o.value);
      if (o.all && o.t.keyp) {                              // the same kind of box repeated, one per similar record
        for (const other of [...D.querySelectorAll('[class*="st-key-' + o.t.keyp + '"]')]) {
          const e = box(other);
          if (e && e !== el && !e.value) await doType(e, o.value);
        }
      }
    },
    async select(o) {
      const w = await locate(o.t, o.optional);
      if (!w) return !o.optional && fail("Could not find the dropdown " + JSON.stringify(o.t));
      if (!(await chooseOption(w, o.value))) fail("Option not found: " + o.value);
    },
    async radio(o) {
      const open = () => [...D.querySelectorAll('[data-testid="stRadio"] label')]
        .filter((l) => visible(l) && norm(l.textContent).includes(norm(o.value)) && !(l.querySelector("input") || {}).checked);
      let lab = await locate({ text: o.value }, o.optional);
      if (!lab) return !o.optional && fail("Could not find the choice " + o.value);
      await doClick(lab);
      for (let i = 0; o.all && i < 8 && open().length; i++) await doClick(open()[0]);       // one answer per similar record
    },
    async expand(o) {
      const sum = await locate({ text: o.text }, o.optional);
      if (!sum) return !o.optional && fail("Could not find the section " + o.text);
      const det = sum.closest("details");
      if (det && det.open) return;
      await doClick(sum);
    },
    async reveal(o) {                                       // open request cards one by one until the wanted row is on screen
      await idle();
      const have = () => [...D.querySelectorAll(o.until)].find(visible);
      const closed = () => [...D.querySelectorAll("summary")].filter((s) => visible(s) && norm(s.textContent).includes(norm(o.open)) && !s.closest("details").open);
      for (let i = 0; i < 12 && !have() && closed().length; i++) await doClick(closed()[0]);
    },
    async collapse(o) {
      const sum = await locate({ text: o.text, last: true }, o.optional);
      const det = sum && sum.closest("details");
      if (det && det.open) await doClick(sum);
    },
    async json(o) {                                         // point at fields of the technical JSON on screen and explain each
      await idle();
      const boxes = [...D.querySelectorAll('[data-testid="stJson"]')].filter(visible);
      const box = boxes[boxes.length - 1];
      if (!box) return !o.optional && fail("No JSON on screen");
      await aim(box);
      const rows = () => [...box.querySelectorAll(".object-key-val,.variable-row")];
      for (const item of o.items) {
        let row;
        if (item.has) {                                      // a row by its words, the smallest one that holds them
          const hits = rows().filter((r) => norm(r.textContent).includes(norm(item.has)));
          row = hits.sort((a, b) => a.textContent.length - b.textContent.length)[0];
        } else {
          const want = '"' + item.key.toLowerCase() + '"';
          row = rows().find((r) => norm(r.textContent).replace(/^[^"]*/, "").startsWith(want));
        }
        if (!row) continue;
        await aim(row);
        showRowTag(row, item.label || "Field");
        await say({ kind: "how", text: (item.key || item.has) + ": " + item.text });
        clearRow();
      }
    },
    async nav(o) {
      const href = o.path === "home" ? "/" : "/" + o.path;
      const link = () => [...D.querySelectorAll('a[data-testid="stTopNavLink"]')].find((x) => new URL(x.href).pathname === href);
      await idle();
      for (let i = 0; i < 150 && !link(); i++) {           // the top bar draws its links after measuring itself: nudge it if it is slow
        if (i % 30 === 29) window.dispatchEvent(new Event("resize"));
        await sleep(100);
      }
      const a = link();
      if (!a) {                                             // the top bar never drew its links: move within the app the way the router does
        if (new URL(location.href).pathname === href) return;
        history.pushState({}, "", href);
        window.dispatchEvent(new PopStateEvent("popstate", { state: {} }));
        await idle();
        return;
      }
      if (new URL(location.href).pathname === href) return;
      await doClick(a);
    },
    async role(o) {                                         // a click elsewhere (say on Fast-forward) closes an open dropdown: try again
      const want = norm("Role: " + o.short + (o.fn ? " (" + o.fn + ")" : ""));
      state.lockSpeed = true;
      try {
        for (let attempt = 0; attempt < 3; attempt++) {
          state.hush = attempt < 2;
          await OPS.roleInner(o);
          const trig = D.querySelector('[class*="st-key-role_pop_"] button');
          if (trig && norm(trig.textContent).includes(want)) break;
          await sleep(400);
        }
      } finally { state.lockSpeed = false; state.hush = false; }
    },
    async roleInner(o) {
      const trig = () => D.querySelector('[class*="st-key-role_pop_"] button');
      const want = "Role: " + o.short + (o.fn ? " (" + o.fn + ")" : "");
      if (norm(trig().textContent).includes(norm(want))) return;
      const boxes = () => [...D.querySelectorAll('[data-testid="stPopoverBody"] [data-testid="stSelectbox"]')];
      const open = async (n) => {
        if (!boxes().length) await doClick(trig());          // a click on an open popover would close it again
        for (let i = 0; i < 30 && !boxes().length; i++) await sleep(80);
        return boxes()[n];
      };
      if (!norm(trig().textContent).includes(norm("Role: " + o.short))) {
        const first = await open(0);
        if (!first || !(await chooseOption(first, o.label))) return fail("Could not switch to " + o.label);
      }
      if (o.fn && !norm(trig().textContent).includes(norm(want))) {
        const second = await open(1);
        if (!second || !(await chooseOption(second, o.fn))) fail("Could not pick the approval step " + o.fn);
      }
    },
    async reset() {
      const pop = D.querySelector('[class*="st-key-story_pop_"] button');
      await doClick(pop);
      const b = await locate({ key: "demo_reset" });
      if (b) await doClick(interactive(b));
    },
    async test(o) {
      await idle();
      await sleep(250);
      const text = norm(D.body.innerText);                  // the whole page, so short-lived toasts count too
      const has = (s) => text.includes(norm(s));
      const ok = (o.has || []).every(has) && !(o.absent || []).some(has);
      if (!state.replay) { ok ? state.passed++ : (state.failed++, state.missed.push(o.text)); }
      showFlag("test", (ok ? "PASS: " : "FAIL: ") + o.text, o.backed, ok ? "test-pass" : "test-fail");
      await wait(state.fast ? 120 : Math.min(T.max_flag_ms, Math.max(T.min_flag_ms, o.text.length * T.read_ms_per_char)));
    },
  };
  function fail(msg) {
    console.warn("[autopilot]", msg);
    if (state.replay || state.fast || state.hush) return sleep(200);          // a replay or a skip-ahead stays quiet about things it did not need
    showFlag("test", "Skipped: " + msg, "", "test-fail");
    return sleep(900);
  }

  // ------------------------------------------------------------------ the run
  // Steps that change data (keying a form, answering, approving, applying a setting) are not repeated when a step is replayed.
  const CHANGES = /^(intake_|approve_|reject_|sync_|hook_|adm_|wf_|cmt_|type_|in_)/;
  function changesData(op) {
    const k = (op.t && (op.t.key || op.t.keyp || op.t.inp)) || "";
    if (op.op === "type" || op.op === "radio" || op.op === "reset") return true;
    if (op.op === "select") return !/^(an_|inbox_lens)/.test(k);
    if (op.op === "click") return CHANGES.test(k);
    return false;
  }

  async function runOp(op) {
    try { await OPS[op.op](op); } catch (e) { if (e === JUMP || e === STOP) throw e; console.warn("[autopilot]", op, e); }
  }

  async function runStep(i, o) {
    const { startOp = 0, replay = false } = o || {};
    const { seg, step } = flat[i];
    setProgress(i);
    where.textContent = seg.title + "  |  " + step.title + (replay ? "  (replay)" : "");
    hideFlag();
    clearRow();
    state.replay = replay;
    const ops = step.ops;
    for (let k = 0; k < startOp; k++) if (ops[k].op === "role" || ops[k].op === "nav") await runOp(ops[k]);   // be that role, on that page, again
    for (let k = startOp; k < ops.length; k++) {
      state.curOp = k;
      if (replay && changesData(ops[k])) continue;
      await gate();
      await runOp(ops[k]);
    }
    state.replay = false;
  }

  async function play() {
    await sleep(900);
    await idle();
    let i = 0, resume = 0;
    while (i < flat.length) {
      try {
        await runStep(i, { startOp: resume });
        i++;
        resume = 0;
      } catch (e) {
        if (e === STOP) return;
        if (e !== JUMP) throw e;
        const j = state.jump, keep = state.curOp || 0;
        state.jump = null;
        state.busy = true;
        try {
          const target = j.kind === "next" ? i + 1 : j.kind === "back" ? i - 1 : j.to;
          if (target > i) {                                     // skip ahead: finish this step and any between quickly, so nothing is left undone
            state.fast = true;
            await runStep(i, { startOp: keep });
            for (i++; i < target; i++) await runStep(i);
            state.fast = false;
            resume = 0;
          } else if (target >= 0 && target < i) {               // rewind: show the earlier step again, then carry on where we were
            await runStep(target, { replay: true });
            resume = keep;
          } else {
            resume = keep;
          }
        } finally {
          state.busy = false;
          state.fast = false;
          state.replay = false;
          state.jump = null;
        }
      }
    }
    setProgress(flat.length);
    ring.style.display = "none";
    cursor.style.left = "-40px";
    const tally = state.passed + state.failed ? " Checks passed: " + state.passed + " of " + (state.passed + state.failed) + "." : "";
    const missed = state.missed.length ? " Did not pass: " + state.missed.join(" | ") : "";
    showFlag("how", "That was the whole story, played hands-free on the real screens." + tally + missed);
    where.textContent = "Finished";
  }

  const api = {
    runId: DATA.runId,
    stop() {
      state.stopped = true;
      clearInterval(follower);
      [cursor, ring, flagBox, panel, tag, style].forEach((e) => e.remove());
      if (window.__lvAuto === api) window.__lvAuto = null;
    },
  };
  window.__lvAuto = api;
  play();
})();
