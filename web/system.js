// diehard-pushraker: the "DIEHARDave's plugins" section of pushraker's
// settings page, where this plugin's additions to pushraker live, apart
// from pushraker's own rows.
//
// The page (python/pushraker/web/index.html) loads this through a
// <script> tag that tools/vendor.py's PATCHES add; it lives in the
// plugin's web/ folder, served at /pushraker-updates/ next to the update
// confirm page. It adds the section below "Custom" in the notifications
// table (all from moonraker/components/pushraker_updates.py):
//   System: the software update notices, on or off in the Push column,
//     their schedule under Edit
//   Power: "Printer on" and "Klipper restarted", Push and Email
//   Console: what pushraker prints in the Klipper console after a send
// Save & Apply saves it too; Discard changes puts it back.
//
// Talks to GET/POST /server/pushraker_updates/settings,
// POST /server/pushraker_updates/check and GET/POST
// /server/pushraker_addons/power, like the page talks to Moonraker.
//
// This file may be distributed under the terms of the GNU GPLv3 license.
(function () {
    "use strict";
    const DAYS = [["mon", "Mon"], ["tue", "Tue"], ["wed", "Wed"], ["thu", "Thu"],
                  ["fri", "Fri"], ["sat", "Sat"], ["sun", "Sun"]];
    const $ = id => document.getElementById(id);
    const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g,
        c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    const apiKey = () => { try { return localStorage.getItem("pushraker.apiKey") || ""; } catch (e) { return ""; } };

    let data = null;      // the last GET (or POST) answer: update notices
    let power = null;     // the same for the power rows
    let open = false;     // Edit panel shown
    const POWER = [["printer_on", "🟢", "Printer on", "The first time Klipper is ready after the printer boots"],
                   ["klipper_restart", "🔄", "Klipper restarted", "RESTART, FIRMWARE_RESTART, saving the config"]];
    const CONSOLE = [["all", "Sent and failed"], ["failures", "Only failures"], ["none", "Nothing"]];
    const currentConsole = () => { const c = $("dp-console"); return c ? c.value : power && power.console; };

    async function api(path, method, body) {
        const headers = body ? { "Content-Type": "application/json" } : {};
        if (apiKey()) headers["X-Api-Key"] = apiKey();
        const r = await fetch(path, { method: method || "GET", headers, credentials: "same-origin",
                                      body: body ? JSON.stringify(body) : undefined });
        let j = null;
        try { j = await r.json(); } catch (e) { /* not JSON */ }
        if (!r.ok) throw new Error((j && j.error && j.error.message) || `HTTP ${r.status}`);
        return j ? j.result : null;
    }

    function days(st) {
        return st.check_days === "daily" ? DAYS.map(d => d[0])
            : st.check_days.split(",").map(d => d.trim()).filter(Boolean);
    }

    function summary(st) {
        const d = days(st);
        const when = d.length === 7 ? "Daily" : d.length === 5 && !d.includes("sat") && !d.includes("sun")
            ? "Weekdays" : d.map(x => x[0].toUpperCase() + x.slice(1)).join(", ");
        return `${when} at ${st.check_time} · ${st.notify === "always" ? "every check" : "only when something new is out"}`;
    }

    function when(iso) {
        if (!iso) return "—";
        const t = new Date(iso);
        return isNaN(t) ? iso : t.toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit", month: "short", day: "numeric" });
    }

    // What the section shows now, as settings
    function current() {
        const st = Object.assign({}, data.settings);
        const en = document.querySelector("[data-dp-enabled]");
        if (en) st.enabled = en.checked;
        if (open) {
            st.check_time = $("dp-time").value.trim();
            const on = DAYS.map(d => d[0]).filter(d => $("dp-day-" + d).checked);
            st.check_days = on.length === 7 ? "daily" : on;
            st.notify = $("dp-notify").value;
            st.timezone = $("dp-tz").value.trim();
            st.confirm = $("dp-confirm").checked;
            st.other_services = $("dp-others").checked;
            st.ignore = [...document.querySelectorAll("[data-dp-ignore]")]
                .filter(b => b.checked).map(b => b.dataset.dpIgnore);
        }
        return st;
    }

    const same = (a, b) => JSON.stringify(normal(a)) === JSON.stringify(normal(b));
    function normal(st) {
        const d = Array.isArray(st.check_days) ? (st.check_days.length === 7 ? "daily" : st.check_days.join(", ")) : st.check_days;
        return [!!st.enabled, String(st.check_time).replace(/\s+/g, ""), String(d).replace(/\s+/g, ""), st.notify,
                st.timezone || "", !!st.confirm, !!st.other_services, [...(st.ignore || [])].sort()];
    }
    const dirty = () => data && !same(current(), data.settings);

    // The power rows as shown now, and as saved
    function currentPower() {
        const out = {};
        POWER.forEach(([rid]) => {
            const saved = (power && power.rows[rid]) || {};
            const p = document.querySelector(`[data-dp-power="${rid}"]`);
            const e = document.querySelector(`[data-dp-power-email="${rid}"]`);
            out[rid] = { push: p ? p.checked : !!saved.push, email: e ? e.checked : !!saved.email };
        });
        return out;
    }
    const savedPower = rid => ({ push: !!power.rows[rid].push, email: !!power.rows[rid].email });
    const powerChanged = (cur, rid) => cur[rid].push !== savedPower(rid).push || cur[rid].email !== savedPower(rid).email;
    const powerDirty = () => {
        if (!power) return false;
        const cur = currentPower();
        return POWER.some(([rid]) => powerChanged(cur, rid)) || currentConsole() !== power.console;
    };

    function markDirty() {
        const row = $("dp-row");
        if (row) row.classList.toggle("nt-chg", !!dirty());
        const note = $("dp-unsaved");
        if (note) note.style.display = dirty() ? "" : "none";
        if (power) {
            const cur = currentPower();
            POWER.forEach(([rid]) => {
                const r = document.querySelector(`[data-dp-prow="${rid}"]`);
                if (r) r.classList.toggle("nt-chg", powerChanged(cur, rid));
            });
            const c = $("dp-console-row");
            if (c) c.classList.toggle("nt-chg", currentConsole() !== power.console);
        }
    }

    function systemRows() {
        if (!data) return "";
        const st = data.settings;
        const next = st.enabled ? `Next check ${esc(when(data.next_check))}` : "Off";
        const waiting = data.updates ? ` · ${data.updates} update${data.updates === 1 ? "" : "s"} waiting` : "";
        let html = `<tr class="nt-group dp-sub"><td colspan="4">System</td></tr>
            <tr class="nt-crow" id="dp-row">
              <td><span class="nt-icon">⬆️</span><span class="nt-name">Software updates</span>
                <span class="nt-sub">${esc(summary(st))}</span>
                <span class="nt-sub">${next}${waiting} <span id="dp-unsaved" style="display:none;color:var(--accent)">· unsaved: Save &amp; Apply saves it</span></span></td>
              <td class="nt-c nt-col-push"><input type="checkbox" data-dp-enabled${st.enabled ? " checked" : ""} title="Discord card with an Update button per entry"></td>
              <td class="nt-c nt-col-email"><span class="nt-na" title="Set under Edit: also send to the other services">–</span></td>
              <td class="nt-data"><button type="button" class="btn btn-secondary nt-data-btn" id="dp-edit">${open ? "Close" : "Edit"}</button></td>
            </tr>`;
        if (open) {
            const d = days(st);
            const ign = new Set(st.ignore || []);
            html += `<tr class="dp-panel"><td colspan="4" style="padding:12px 10px">
              <div class="nt-grid" style="margin-bottom:12px">
                <div class="nt-field">
                  <label for="dp-time">Check at</label>
                  <input class="nt-input" id="dp-time" value="${esc(st.check_time)}" placeholder="09:00" autocomplete="off" spellcheck="false">
                  <div class="nt-hint">24-hour time, several with commas: 09:00, 21:00</div>
                </div>
                <div class="nt-field">
                  <label for="dp-notify">Send a card</label>
                  <select class="nt-input" id="dp-notify">
                    <option value="changes"${st.notify === "changes" ? " selected" : ""}>Only when something new is out</option>
                    <option value="always"${st.notify === "always" ? " selected" : ""}>At every check while updates wait</option>
                  </select>
                </div>
                <div class="nt-field" style="grid-column:1 / -1">
                  <label>On</label>
                  <div class="nt-inline" style="flex-wrap:wrap;gap:4px 14px">
                    ${DAYS.map(([k, n]) => `<label style="display:flex;gap:6px;align-items:center;margin:0;font-size:13px;color:inherit"><input type="checkbox" id="dp-day-${k}"${d.includes(k) ? " checked" : ""}>${n}</label>`).join("")}
                  </div>
                </div>
                <div class="nt-field">
                  <label for="dp-tz">Time zone</label>
                  <input class="nt-input" id="dp-tz" value="${esc(st.timezone)}" placeholder="${esc(data.timezone || "")}" autocomplete="off" spellcheck="false">
                  <div class="nt-hint">Empty: the one under Notification content (now ${esc(data.timezone || "the printer's")})</div>
                </div>
                <div class="nt-field">
                  <label>&nbsp;</label>
                  <label style="display:flex;gap:6px;align-items:center;font-size:13px;color:inherit"><input type="checkbox" id="dp-confirm"${st.confirm ? " checked" : ""}> Ask to confirm before an update (bot buttons)</label>
                  <label style="display:flex;gap:6px;align-items:center;font-size:13px;color:inherit;margin-top:6px"><input type="checkbox" id="dp-others"${st.other_services ? " checked" : ""}> Also send a text notice to the other services</label>
                </div>
                <div class="nt-field" style="grid-column:1 / -1">
                  <label>Leave out</label>
                  <div class="nt-inline" style="flex-wrap:wrap;gap:4px 14px">
                    ${(data.entries || []).map(n => `<label style="display:flex;gap:6px;align-items:center;margin:0;font-size:13px;color:inherit"><input type="checkbox" data-dp-ignore="${esc(n)}"${ign.has(n) ? " checked" : ""}>${esc(n)}</label>`).join("") || '<span class="nt-hint">No update manager entries found</span>'}
                  </div>
                  <div class="nt-hint">Ticked entries never show up in the card.</div>
                </div>
              </div>
              <div class="nt-inline" style="align-items:center">
                <button type="button" class="btn btn-secondary" id="dp-check">Check now</button>
                <span class="nt-hint" id="dp-msg" style="margin:0">Last check ${esc(when(data.last_check))}. Check now sends the card even if it was sent before.</span>
              </div>
            </td></tr>`;
        }
        return html;
    }

    function consoleRows(keep) {
        if (!power || !power.console) return "";
        const v = keep || power.console;
        return `<tr class="nt-group dp-sub"><td colspan="4">Console</td></tr>
            <tr class="nt-crow" id="dp-console-row">
              <td><span class="nt-icon">🖥️</span><span class="nt-name">Klipper console</span>
                <span class="nt-sub">What pushraker prints after a notification is sent</span></td>
              <td class="nt-c nt-col-push"><span class="nt-na">–</span></td>
              <td class="nt-c nt-col-email"><span class="nt-na">–</span></td>
              <td class="nt-data"><select class="nt-input" id="dp-console">${CONSOLE.map(([k, n]) =>
                  `<option value="${k}"${k === v ? " selected" : ""}>${n}</option>`).join("")}</select></td>
            </tr>`;
    }

    function powerRows(keep) {
        if (!power) return "";
        const lastName = power.last && (POWER.find(r => r[0] === power.last.row) || [])[2];
        const last = power.last && power.last.at
            ? `<span class="nt-sub">Last sent ${esc(when(power.last.at))}: ${esc(lastName || power.last.row)}${power.last.ok ? "" : " (not delivered)"}</span>` : "";
        return `<tr class="nt-group dp-sub"><td colspan="4">Power</td></tr>` + POWER.map(([rid, icon, name, sub], i) => {
            const v = (keep || {})[rid] || power.rows[rid];
            return `<tr class="nt-crow" data-dp-prow="${rid}">
              <td><span class="nt-icon">${icon}</span><span class="nt-name">${esc(name)}</span>
                <span class="nt-sub">${esc(sub)}</span>${i === 0 ? last : ""}</td>
              <td class="nt-c nt-col-push"><input type="checkbox" data-dp-power="${rid}"${v.push ? " checked" : ""}></td>
              <td class="nt-c nt-col-email"><input type="checkbox" data-dp-power-email="${rid}"${v.email ? " checked" : ""}></td>
              <td class="nt-data"></td>
            </tr>`;
        }).join("");
    }

    function render() {
        let body = $("dp-plugins");
        if (!body) {
            const crows = $("nt-crows");
            if (!crows) return;
            body = document.createElement("tbody");
            body.id = "dp-plugins";
            crows.parentNode.insertBefore(body, crows.nextSibling);
            const css = document.createElement("style");
            css.textContent = `.nt-table tr.dp-head td { padding-top: 18px; font-size: 13px; font-weight: 600; color: var(--accent); }
                .nt-table tr.dp-head a { font-weight: 400; font-size: 12px; margin-left: 8px; opacity: .8; }
                .nt-table tr.dp-sub td { padding-left: 12px; }`;
            document.head.appendChild(css);
        }
        if (!data && !power) {
            body.innerHTML = "";
            return;
        }
        // Ticks not saved yet survive a redraw (Edit / Close)
        const keepPower = power && body.querySelector("[data-dp-power]") ? currentPower() : null;
        const keepConsole = $("dp-console") ? $("dp-console").value : null;
        body.innerHTML = `<tr class="nt-group dp-head"><td colspan="4">DIEHARDave's plugins<a href="https://github.com/DIEHARDave/diehard-pushraker" target="_blank" rel="noopener">diehard-pushraker ↗</a></td></tr>`
            + systemRows() + powerRows(keepPower) + consoleRows(keepConsole);
        // After the page's own change marking, which clears every mark in
        // the table (ours too) on each change
        const later = () => setTimeout(markDirty, 0);
        body.querySelectorAll("input, select").forEach(i => {
            i.addEventListener("change", later);
            i.addEventListener("input", later);
        });
        const edit = $("dp-edit");
        if (edit) edit.onclick = () => {
            const keep = current();
            open = !open;
            render();
            restore(keep);
            markDirty();
        };
        const check = $("dp-check");
        if (check) check.onclick = checkNow;
    }

    // Values typed but not saved survive opening and closing Edit
    function restore(st) {
        const en = document.querySelector("[data-dp-enabled]");
        if (en) en.checked = !!st.enabled;
        if (!open) return;
        $("dp-time").value = st.check_time;
        const d = Array.isArray(st.check_days) ? st.check_days : days(st);
        DAYS.forEach(([k]) => { $("dp-day-" + k).checked = d.includes(k); });
        $("dp-notify").value = st.notify;
        $("dp-tz").value = st.timezone || "";
        $("dp-confirm").checked = !!st.confirm;
        $("dp-others").checked = !!st.other_services;
        const ign = new Set(st.ignore || []);
        document.querySelectorAll("[data-dp-ignore]").forEach(b => { b.checked = ign.has(b.dataset.dpIgnore); });
    }

    async function load() {
        const [d, p] = await Promise.all([
            // none: no System rows
            api("/server/pushraker_updates/settings").catch(() => null),
            // an older component: no Power rows
            api("/server/pushraker_addons/power").catch(() => null),
        ]);
        data = d;
        power = p;
        render();
    }

    async function savePower() {
        if (!powerDirty()) return;
        try {
            power = await api("/server/pushraker_addons/power", "POST",
                              Object.assign(currentPower(), { console: currentConsole() }));
        } catch (e) {
            const r = document.querySelector("[data-dp-prow]");
            const sub = r && r.querySelector(".nt-sub");
            if (sub) { sub.textContent = `Not saved: ${e.message}`; sub.style.color = "var(--error)"; }
            return;
        }
        render();
        markDirty();
    }

    async function save() {
        await savePower();
        if (!dirty()) return;
        const msg = $("dp-msg");
        try {
            data = await api("/server/pushraker_updates/settings", "POST", current());
            render();
            markDirty();
        } catch (e) {
            render();
            const row = $("dp-row");
            if (row) row.classList.add("nt-chg");
            const note = $("dp-unsaved");
            if (note) { note.style.display = ""; note.textContent = `· not saved: ${e.message}`; note.style.color = "var(--error)"; }
            if (msg) msg.textContent = e.message;
        }
    }

    async function checkNow() {
        const b = $("dp-check"), msg = $("dp-msg");
        b.disabled = true;
        msg.textContent = "Checking for updates (Moonraker asks each entry; this can take a minute)…";
        try {
            const r = await api("/server/pushraker_updates/check?force=true", "POST");
            const n = (r.updates || []).length;
            msg.textContent = r.sent ? `${n} update${n === 1 ? "" : "s"}: the card went to ${(r.to || []).join(", ")}.`
                : n ? `${n} update${n === 1 ? "" : "s"}, card not sent: ${r.why || "no Discord destination"}.`
                : "Everything is up to date.";
            const st = await api("/server/pushraker_updates/settings");
            data = Object.assign(st, { settings: data.settings });
        } catch (e) {
            msg.textContent = `Check failed: ${e.message}`;
        }
        b.disabled = false;
    }

    function start() {
        load();
        const saveBtn = $("pr-save");
        if (saveBtn) saveBtn.addEventListener("click", save, true);
        const discard = $("pr-close");
        if (discard) discard.addEventListener("click", () => { open = false; load(); }, true);
    }
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
    else start();
})();
