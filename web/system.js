// diehard-pushraker: the System section of pushraker's settings page.
//
// The page (python/pushraker/web/index.html) loads this through a
// <script> tag that tools/vendor.py's PATCHES add; it lives in the
// plugin's web/ folder, served at /pushraker-updates/ next to the update
// confirm page. It adds a "System" group above "Custom" in the
// notifications table: the software update notices
// (moonraker/components/pushraker_updates.py), on or off in the Push
// column, and their schedule under Edit. Save & Apply saves it too;
// Discard changes puts it back.
//
// Talks to GET/POST /server/pushraker_updates/settings and
// POST /server/pushraker_updates/check, like the page talks to Moonraker.
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

    let data = null;      // the last GET (or POST) answer
    let open = false;     // Edit panel shown

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

    function markDirty() {
        const row = $("dp-row");
        if (row) row.classList.toggle("nt-chg", !!dirty());
        const note = $("dp-unsaved");
        if (note) note.style.display = dirty() ? "" : "none";
    }

    function render() {
        let body = $("dp-system");
        if (!body) {
            const crows = $("nt-crows");
            if (!crows) return;
            body = document.createElement("tbody");
            body.id = "dp-system";
            crows.parentNode.insertBefore(body, crows);
        }
        if (!data) {
            body.innerHTML = "";
            return;
        }
        const st = data.settings;
        const next = st.enabled ? `Next check ${esc(when(data.next_check))}` : "Off";
        const waiting = data.updates ? ` · ${data.updates} update${data.updates === 1 ? "" : "s"} waiting` : "";
        let html = `<tr class="nt-group"><td colspan="4">System</td></tr>
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
        body.innerHTML = html;
        body.querySelectorAll("input, select").forEach(i => {
            i.addEventListener("change", markDirty);
            i.addEventListener("input", markDirty);
        });
        $("dp-edit").onclick = () => {
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
        try {
            data = await api("/server/pushraker_updates/settings");
        } catch (e) {
            data = null;   // no update notices on this printer: no section
        }
        render();
    }

    async function save() {
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
