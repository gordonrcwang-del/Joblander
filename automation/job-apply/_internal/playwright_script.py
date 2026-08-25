"""
playwright_script.py — single-process browser automation helper for job applications
(originally built and proven against Workday; also run against Eightfold.ai — the
actions here are generic ARIA-role automation, not Workday-specific, despite the
Workday-flavored comments below).

WHY THIS EXISTS
Workday application flows require sign-in, and OAuth sign-in (Google/Apple/LinkedIn)
blocks automated browsers ("This browser or app may not be secure"). So a real,
visible (non-headless) browser window has to stay open for the human to sign in
manually, while the automation still needs to check state and later fill the form.

Running a SECOND process that reconnects via connect_over_cdp() to check on that
browser causes a hang: two independent CDP clients fight over control of the same
target. The fix is to never have a second client. This script is the ONE AND ONLY
process that ever touches the browser. It holds the Page object for the entire
session and services commands via a simple file-based queue:

  - Write a JSON command to command.json (in --dir)
  - This script picks it up, executes it, writes the result to result.json,
    deletes command.json
  - Repeat

USAGE
    python3 playwright_script.py <start_url> <profile_dir> --dir <working_dir>

--dir is required (not optional). command.json/result.json/screenshots always go
there — pass a fresh subfolder under ~/.job-apply-sessions/<job-id>/ per
application so runs don't collide and are easy to find after the fact. This lives
outside the project folder deliberately — it's disposable runtime state (browser
profile + command logs), not project content.

SCOPING TO A REPEATED SECTION (group)
Workday exposes each repeated block (Education 1, Education 2, Work Experience 2, ...)
as an ARIA role="group" with an accessible name matching its heading. Add "group":
"Education 1" to click/fill/type_into/select/choose_option/scroll commands to search
only within that section instead of the whole page — this avoids nth-index drift when
sections are added/deleted (deleting Work Experience 2 shifts every nth index after it).

COMMAND FORMAT (command.json)
    {"action": "get_state"}
    {"action": "list_fields"}                                   -- fast text dump of all labeled fields + current values, instead of a screenshot
    {"action": "screenshot", "path": "screenshot.png"}
    {"action": "goto", "url": "https://..."}
    {"action": "click", "role": "button", "name": "Apply", "exact": true}
    {"action": "click", "text": "Some Link Text", "group": "Education 1"}
    {"action": "fill", "label": "First Name", "value": "Jui Che"}
    {"action": "fill", "placeholder": "Search Location", "value": "Taiwan"}
    {"action": "type_into", "label": "How Did You Hear About Us?", "value": "Campus"}  -- for custom comboboxes; fill() alone won't register
    {"action": "select", "label": "Country", "option": "Taiwan"}                        -- native <select> only
    {"action": "choose_option", "role": "button", "name": "Degree Select One Required", "option": "Bachelors of Arts or Science (Bachelors)", "group": "Education 2"}
                                                                 -- Workday custom dropdown/listbox: opens trigger, tries a direct
                                                                    option click first (works even for long off-screen lists —
                                                                    Playwright auto-scrolls), falls back to bounded arrow-key
                                                                    hunting only if that fails
    {"action": "scroll", "label": "Field of Study"}
    {"action": "press_key", "key": "ArrowDown"}
    {"action": "upload_file", "role": "button", "name": "Select files", "file": "/abs/path/resume.pdf"}
    {"action": "wait", "seconds": 3}
    {"action": "wait_for_text", "text": "Legal Name", "timeout": 10000}   -- waits INSIDE Playwright (native
                                                                    DOM wait, not a guessed sleep) for text that
                                                                    only appears once the next page/section has
                                                                    actually rendered. Put this as the LAST
                                                                    sub-command in a batch, right after the click
                                                                    that triggers a page transition — collapses
                                                                    the whole transition into one round trip
                                                                    instead of polling get_state/list_fields
                                                                    from outside afterward. Pick text that can
                                                                    only appear after real content loads (a field
                                                                    label), not static chrome/button text — those
                                                                    render before content does and give a false
                                                                    "ready" signal.
    {"action": "start_recording"}       -- logs every click/change the HUMAN makes in the visible
                                            browser window (role, accessible name, value) to
                                            recording.jsonl in --dir, plus page-navigation events.
                                            For exploring a brand-new platform: instead of the
                                            agent guessing selectors via list_fields/get_state
                                            trial-and-error, the user clicks through the real form
                                            themselves once, and that log becomes the raw material
                                            for the SOP's batch commands. Safe to call again on the
                                            same page (truncates the log fresh).
    {"action": "stop_recording"}        -- returns how many events were logged and the file path;
                                            recording itself doesn't need to be "stopped" to be
                                            read, this is just a checkpoint/summary call
    {"action": "batch", "commands": [{"action": "fill", ...}, {"action": "click", ...}]}
                                                                 -- runs sub-commands in ONE round trip (one Bash call, one
                                                                    command.json/result.json cycle) instead of one per field;
                                                                    stops at the first sub-command that returns ok:false and
                                                                    reports which index it stopped at
    {"action": "stop"}   -- cleanly closes the browser and exits the loop

RESULT FORMAT (result.json)
    {"ok": true, "action": "get_state", "url": "...", "title": "...", "text": "..."}
    {"ok": true, "action": "fill", "filled_value": "...", "matches": true}
    {"ok": true, "action": "choose_option", "method": "direct" | "keyboard_hunt"}
    {"ok": false, "action": "click", "error": "..."}

KNOWN WORKDAY GOTCHAS (see reference_workday_ats_quirks memory for the full list)
    - OAuth sign-in (Google/Apple/LinkedIn) blocks automated browsers; use "Sign in
      with email" instead — do not try to work around this.
    - Custom comboboxes silently ignore .fill(); use type_into.
    - get_by_label(..., exact=True) fails on required fields whose accessible name
      includes the red "*" — but exact=True IS needed when one label is a substring
      of another (e.g. "Address Line 1" vs "Address Line 1 - Chinese"). Judge per field.
    - The Workday session does NOT persist across a script/browser restart even with
      the same profile_dir — re-authentication is required every time the process
      is relaunched.
"""

import sys
import os
import json
import time
from playwright.sync_api import sync_playwright


def load_command(cmd_path):
    """Returns (command_dict_or_None, parse_error_or_None). A parse_error means the
    file existed but wasn't valid JSON — distinct from "no command file yet" so a
    malformed write doesn't just silently vanish and leave the caller hanging."""
    if not os.path.exists(cmd_path):
        return None, None
    try:
        with open(cmd_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data, None
    except Exception as e:
        return None, str(e)


def write_result(result_path, result):
    tmp_path = result_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, result_path)


def get_state(page):
    text = page.inner_text("body")
    return {
        "url": page.url,
        "title": page.title(),
        "text": text[:6000],
    }


LIST_FIELDS_JS = """
() => {
    const out = [];
    const els = document.querySelectorAll('input, textarea, select, button[aria-haspopup]');
    for (const el of els) {
        if (el.offsetParent === null) continue;  // skip hidden elements
        let label = el.getAttribute('aria-label') || '';
        if (!label && el.id) {
            const lbl = document.querySelector(`label[for="${el.id}"]`);
            if (lbl) label = lbl.innerText;
        }
        if (!label) continue;
        let value = '';
        if (el.tagName === 'SELECT') {
            value = el.options[el.selectedIndex] ? el.options[el.selectedIndex].text : '';
        } else if (el.tagName === 'BUTTON') {
            value = el.innerText;
        } else {
            value = el.value;
        }
        out.push({label: label.trim().slice(0, 120), tag: el.tagName.toLowerCase(), value: (value || '').trim().slice(0, 200)});
    }
    return out;
}
"""

# Injected during start_recording. Best-effort: logs every click/change with enough
# info (role, accessible name, value) to reconstruct a working command afterward — not
# meant to be a perfect accessibility tree, just enough for a human/agent to turn into
# SOP JSON without re-deriving selectors from scratch. Re-runs on every new document via
# page.add_init_script; also evaluated once immediately for the already-loaded page.
RECORDER_JS = """
(() => {
  if (window.__afRecorderInstalled) return;
  window.__afRecorderInstalled = true;

  function describe(el) {
    if (!el || el.nodeType !== 1) return null;
    const tag = el.tagName.toLowerCase();
    let role = el.getAttribute('role');
    if (!role) {
      if (tag === 'button') role = 'button';
      else if (tag === 'a') role = 'link';
      else if (tag === 'select') role = 'combobox';
      else if (tag === 'textarea') role = 'textbox';
      else if (tag === 'input') {
        const t = (el.getAttribute('type') || 'text').toLowerCase();
        role = (t === 'checkbox') ? 'checkbox' : (t === 'radio') ? 'radio' : 'textbox';
      } else {
        role = tag;
      }
    }
    let name = el.getAttribute('aria-label') || '';
    if (!name) {
      const labelledby = el.getAttribute('aria-labelledby');
      if (labelledby) {
        name = labelledby.split(/\\s+/).map(id => {
          const n = document.getElementById(id);
          return n ? n.textContent.trim() : '';
        }).join(' ').trim();
      }
    }
    if (!name && el.id) {
      const lbl = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (lbl) name = lbl.textContent.trim();
    }
    if (!name) name = el.getAttribute('placeholder') || '';
    if (!name && (tag === 'button' || tag === 'a')) name = (el.textContent || '').trim().slice(0, 120);
    if (!name) name = el.getAttribute('name') || '';
    return {tag: tag, role: role, name: name};
  }

  function valueOf(el) {
    const tag = el.tagName.toLowerCase();
    if (tag === 'input') {
      const t = (el.getAttribute('type') || 'text').toLowerCase();
      return (t === 'checkbox' || t === 'radio') ? el.checked : el.value;
    }
    if (tag === 'textarea' || tag === 'select') return el.value;
    return null;
  }

  document.addEventListener('click', (e) => {
    const el = e.target.closest('button, a, [role], input, textarea, select');
    const info = describe(el);
    if (!info) return;
    window.record_event({type: 'click', ...info, value: valueOf(el)});
  }, true);

  document.addEventListener('change', (e) => {
    const info = describe(e.target);
    if (!info) return;
    window.record_event({type: 'change', ...info, value: valueOf(e.target)});
  }, true);
})();
"""


def _scope(page, cmd):
    """Resolve the search root: a named ARIA group (repeated Workday section) or the whole page."""
    if cmd.get("group"):
        return page.get_by_role("group", name=cmd["group"], exact=cmd.get("group_exact", True))
    return page


def _locator_for(root, cmd):
    """Shared locator resolution for click/fill/type_into/select/choose_option/scroll."""
    if cmd.get("role"):
        return root.get_by_role(cmd["role"], name=cmd.get("name"), exact=cmd.get("exact", False))
    if cmd.get("text"):
        return root.get_by_text(cmd["text"], exact=cmd.get("exact", False))
    if cmd.get("label"):
        return root.get_by_label(cmd["label"], exact=cmd.get("exact", False))
    if cmd.get("placeholder"):
        return root.get_by_placeholder(cmd["placeholder"], exact=cmd.get("exact", False))
    if cmd.get("selector"):
        return root.locator(cmd["selector"])
    return None


def execute(page, cmd, work_dir):
    action = cmd.get("action")

    if action == "get_state":
        return {"ok": True, **get_state(page)}

    if action == "list_fields":
        fields = page.evaluate(LIST_FIELDS_JS)
        return {"ok": True, "fields": fields[:200]}

    if action == "evaluate":
        # Diagnostic escape hatch — list_fields only surveys input/textarea/
        # select/button[aria-haspopup], so it misses plain <a> links (e.g. an
        # "Apply Now" anchor with an href/target the accessible-name-based
        # locators give no visibility into). Use to inspect raw attributes
        # when a click reports ok:true but nothing visibly changes.
        return {"ok": True, "result": page.evaluate(cmd["script"])}

    if action == "switch_page":
        # Some ATS "Apply" links open the actual application flow in a NEW
        # browser tab (target="_blank") rather than navigating the current
        # one. The engine only ever tracks one fixed `page` object (see
        # main()'s loop) — clicking such a link succeeds but every later
        # command keeps operating on the stale original tab, silently
        # showing unchanged content forever. Call this right after a click
        # that might have opened a new tab; it hands back the newest page
        # in the context and main() swaps its tracked `page` to it via the
        # "_new_page" key (stripped before the result is JSON-written).
        pages = page.context.pages
        if len(pages) < 2:
            return {"ok": False, "error": "no additional page/tab found (context has %d page(s)) — click likely navigated in place, not a new tab" % len(pages)}
        new_page = pages[-1]
        new_page.wait_for_load_state("domcontentloaded", timeout=15000)
        new_page.wait_for_timeout(1000)
        result = {"ok": True, "_new_page": new_page}
        result.update(get_state(new_page))
        return result

    if action == "screenshot":
        path = cmd.get("path", "screenshot.png")
        full_path = os.path.join(work_dir, path) if not os.path.isabs(path) else path
        page.screenshot(path=full_path, full_page=cmd.get("full_page", True))
        return {"ok": True, "path": full_path}

    if action == "goto":
        page.goto(cmd["url"], wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(1500)
        return {"ok": True, **get_state(page)}

    if action == "click":
        root = _scope(page, cmd)
        loc = _locator_for(root, cmd)
        if loc is None:
            return {"ok": False, "error": "click needs role+name, text, label, placeholder, or selector"}
        idx = cmd.get("nth", 0)
        loc.nth(idx).click(timeout=cmd.get("timeout", 10000))
        page.wait_for_timeout(cmd.get("wait_after_ms", 900))
        return {"ok": True, **get_state(page)}

    if action == "fill":
        value = cmd.get("value", "")
        root = _scope(page, cmd)
        loc = _locator_for(root, cmd)
        if loc is None:
            return {"ok": False, "error": "fill needs label, placeholder, or selector"}
        idx = cmd.get("nth", 0)
        target = loc.nth(idx)
        target.click(timeout=cmd.get("timeout", 10000))
        target.fill(value)
        actual = target.input_value()
        return {"ok": True, "filled_value": actual, "matches": actual == value}

    if action == "type_into":
        # for custom combobox widgets that need real keystrokes rather than .fill()
        root = _scope(page, cmd)
        loc = _locator_for(root, cmd)
        if loc is None:
            return {"ok": False, "error": "type_into needs label, placeholder, or selector"}
        idx = cmd.get("nth", 0)
        target = loc.nth(idx)
        target.click(timeout=cmd.get("timeout", 10000))
        page.keyboard.type(cmd.get("value", ""), delay=cmd.get("delay_ms", 80))
        page.wait_for_timeout(cmd.get("wait_after_ms", 700))
        return {"ok": True, **get_state(page)}

    if action == "press_key":
        if "key" not in cmd:
            return {"ok": False, "error": "press_key needs 'key'"}
        page.keyboard.press(cmd["key"])
        page.wait_for_timeout(cmd.get("wait_after_ms", 350))
        return {"ok": True}

    if action == "select":
        # native <select> only. For Workday's custom button+listbox dropdowns, use choose_option.
        root = _scope(page, cmd)
        loc = _locator_for(root, cmd)
        if loc is None:
            return {"ok": False, "error": "select needs label, text, role+name, or selector"}
        idx = cmd.get("nth", 0)
        target = loc.nth(idx)
        try:
            target.select_option(label=cmd["option"])
        except Exception as e:
            return {"ok": False, "error": f"select failed (native <select> only — for Workday's custom dropdowns use 'choose_option' instead): {e}"}
        return {"ok": True}

    if action == "choose_option":
        # Open a Workday-style custom dropdown/listbox and pick an option by visible text.
        # Fast path: click the option locator directly — Playwright auto-scrolls it into
        # view even if the listbox is scrolled elsewhere, so this works for long lists
        # too as long as all options are already in the DOM. Falls back to bounded
        # arrow-key hunting only if the fast path fails (e.g. a virtualized list that
        # only renders currently-visible rows).
        option_text = cmd.get("option")
        if not option_text:
            return {"ok": False, "error": "choose_option needs 'option'"}
        root = _scope(page, cmd)
        trigger = _locator_for(root, cmd)
        if trigger is None:
            return {"ok": False, "error": "choose_option needs a trigger locator (role+name, label, text, or selector)"}
        idx = cmd.get("nth", 0)
        trigger.nth(idx).click(timeout=cmd.get("timeout", 10000))
        page.wait_for_timeout(250)

        option_exact = cmd.get("option_exact", False)
        try:
            page.get_by_role("option", name=option_text, exact=option_exact).first.click(
                timeout=cmd.get("option_timeout", 3000)
            )
            page.wait_for_timeout(cmd.get("wait_after_ms", 400))
            # Some Workday dropdowns are two-level (a category that itself matches by
            # name, e.g. "Campus", which drills into leaf options like "Campus: Career
            # Board" instead of selecting anything). If role=option elements are still
            # visible after the click, we opened a submenu rather than finishing a
            # selection — report that honestly instead of a false-positive success.
            try:
                still_open = page.locator('[role="option"]').first.is_visible(timeout=500)
            except Exception:
                still_open = False
            if still_open:
                return {
                    "ok": False,
                    "method": "direct_drilldown",
                    "error": (
                        f"clicked '{option_text}' but a submenu is still open afterward — "
                        "it likely matched a category header, not a final leaf option. "
                        "Re-inspect visible options (e.g. via screenshot) and call "
                        "choose_option again with the specific leaf option's exact text."
                    ),
                }
            return {"ok": True, "method": "direct"}
        except Exception:
            pass  # fall through to keyboard hunt

        max_steps = cmd.get("max_hunt_steps", 300)
        target_lower = option_text.strip().lower()
        for _ in range(max_steps):
            page.keyboard.press("ArrowDown")
            page.wait_for_timeout(20)
            try:
                highlighted = page.locator('[role="option"][aria-selected="true"]').first.inner_text(timeout=500)
            except Exception:
                highlighted = ""
            if target_lower in highlighted.strip().lower():
                page.keyboard.press("Enter")
                page.wait_for_timeout(cmd.get("wait_after_ms", 400))
                return {"ok": True, "method": "keyboard_hunt", "matched": highlighted}
        return {"ok": False, "error": f"could not find option '{option_text}' after {max_steps} ArrowDown presses"}

    if action == "scroll":
        root = _scope(page, cmd)
        loc = _locator_for(root, cmd)
        if loc is not None:
            loc.nth(cmd.get("nth", 0)).scroll_into_view_if_needed(timeout=cmd.get("timeout", 10000))
        else:
            page.mouse.wheel(cmd.get("dx", 0), cmd.get("dy", 800))
        page.wait_for_timeout(cmd.get("wait_after_ms", 250))
        return {"ok": True}

    if action == "upload_file":
        file_path = cmd["file"]
        if cmd.get("role"):
            with page.expect_file_chooser(timeout=cmd.get("timeout", 15000)) as fc_info:
                page.get_by_role(cmd["role"], name=cmd.get("name"), exact=cmd.get("exact", False)).click()
            fc_info.value.set_files(file_path)
        elif cmd.get("selector"):
            page.locator(cmd["selector"]).set_input_files(file_path)
        else:
            return {"ok": False, "error": "upload_file needs role+name or selector"}
        page.wait_for_timeout(cmd.get("wait_after_ms", 3000))
        return {"ok": True}

    if action == "wait":
        page.wait_for_timeout(int(cmd.get("seconds", 1)) * 1000)
        return {"ok": True}

    if action == "wait_for_text":
        # Waits INSIDE Playwright for text that only exists once the next page/section
        # has actually rendered (native DOM wait, not a guessed timeout) — put this as
        # the last sub-command in a batch, right after the click that triggers a page
        # transition, so the whole transition resolves in one round trip instead of the
        # caller polling get_state/list_fields repeatedly from outside. Pick text that
        # only appears once real content is loaded (e.g. a field label), not text that's
        # part of the static page chrome/buttons — those render before content does and
        # produce a false-positive "ready" signal.
        text = cmd.get("text")
        if not text:
            return {"ok": False, "error": "wait_for_text needs 'text'"}
        try:
            page.get_by_text(text, exact=cmd.get("exact", False)).first.wait_for(
                state="visible", timeout=cmd.get("timeout", 10000)
            )
        except Exception as e:
            return {"ok": False, "error": f"wait_for_text timed out waiting for '{text}': {e}"}
        return {"ok": True, **get_state(page)}

    if action == "start_recording":
        # For exploring a brand-new platform: instead of guessing selectors via
        # list_fields/get_state trial-and-error, let the human click through the real
        # form themselves in the visible browser window while this logs every click/change
        # (role, accessible name, value) to a JSONL file — raw material to turn into SOP
        # batch commands afterward. Safe to call multiple times on the same page (a
        # second call is a no-op for the JS side; the log file is truncated fresh).
        log_path = os.path.join(work_dir, cmd.get("path", "recording.jsonl"))
        open(log_path, "w", encoding="utf-8").close()

        def _record_event(source, payload):
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")

        try:
            page.expose_binding("record_event", _record_event)
        except Exception:
            pass  # already exposed from an earlier start_recording call on this page

        def _on_navigated(frame):
            if frame == page.main_frame:
                _record_event(None, {"type": "navigation", "url": frame.url})
        page.on("framenavigated", _on_navigated)

        page.add_init_script(RECORDER_JS)  # applies to future navigations/documents
        page.evaluate(RECORDER_JS)          # inject into the already-loaded document now
        return {"ok": True, "recording_to": log_path}

    if action == "stop_recording":
        log_path = os.path.join(work_dir, cmd.get("path", "recording.jsonl"))
        count = 0
        if os.path.exists(log_path):
            with open(log_path, encoding="utf-8") as f:
                count = sum(1 for _ in f)
        return {"ok": True, "recorded_events": count, "path": log_path}

    if action == "batch":
        # Run several independent sub-commands in ONE round trip instead of one
        # command.json write per step — this is the main lever for cutting down the
        # number of write/poll/read cycles on a long multi-field form. Stops at the
        # first sub-command that fails (ok: False) so a broken step doesn't silently
        # cascade into the next ones; whatever succeeded before that is still reported.
        sub_commands = cmd.get("commands", [])
        results = []
        for i, sub_cmd in enumerate(sub_commands):
            try:
                sub_result = execute(page, sub_cmd, work_dir)
            except Exception as e:
                sub_result = {"ok": False, "error": str(e)}
            sub_result["action"] = sub_cmd.get("action")
            results.append(sub_result)
            if not sub_result.get("ok", False):
                return {"ok": False, "results": results, "stopped_at": i}
        return {"ok": True, "results": results}

    return {"ok": False, "error": f"unknown action: {action}"}


def main():
    if len(sys.argv) < 3 or "--dir" not in sys.argv:
        print("usage: python3 playwright_script.py <start_url> <profile_dir> --dir <working_dir>")
        print("--dir is required: pass a fresh subfolder under ~/.job-apply-sessions/")
        print("per application (e.g. ~/.job-apply-sessions/acme-12345/).")
        sys.exit(1)

    start_url = sys.argv[1]
    profile_dir = sys.argv[2]

    dir_idx = sys.argv.index("--dir")
    if dir_idx + 1 >= len(sys.argv):
        print("error: --dir requires a path argument")
        sys.exit(1)
    work_dir = sys.argv[dir_idx + 1]
    os.makedirs(work_dir, exist_ok=True)

    cmd_path = os.path.join(work_dir, "command.json")
    result_path = os.path.join(work_dir, "result.json")

    # clear stale files from a previous run
    for p in (cmd_path, result_path):
        if os.path.exists(p):
            os.remove(p)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            profile_dir,
            headless=False,
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport=None,
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(start_url, wait_until="domcontentloaded", timeout=45000)

        print(f"[workday_agent] ready. watching {cmd_path}", flush=True)

        while True:
            cmd, parse_error = load_command(cmd_path)

            if parse_error is not None:
                try:
                    os.remove(cmd_path)
                except FileNotFoundError:
                    pass
                write_result(result_path, {"ok": False, "error": f"malformed command.json: {parse_error}"})

            elif cmd is not None:
                # remove command file first so we never double-execute
                try:
                    os.remove(cmd_path)
                except FileNotFoundError:
                    pass

                if cmd.get("action") == "stop":
                    write_result(result_path, {"ok": True, "action": "stop"})
                    break

                try:
                    result = execute(page, cmd, work_dir)
                    if isinstance(result, dict) and "_new_page" in result:
                        page = result.pop("_new_page")
                except Exception as e:
                    result = {"ok": False, "error": str(e)}
                result["action"] = cmd.get("action")
                write_result(result_path, result)

            # Polling a local file every 150ms costs nothing on a local machine but
            # cuts fixed per-command latency by up to ~850ms versus the old 1s poll —
            # across a multi-field form that adds up to real seconds saved.
            time.sleep(0.15)

        context.close()


if __name__ == "__main__":
    main()
