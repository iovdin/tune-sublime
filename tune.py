import os
import threading
from typing import Dict, Optional, Tuple, List

import sublime
import sublime_plugin

from .tune_jsonrpc import spawn_tune, JsonRpcClient

# State per view
# Use type comments for compatibility with older Python runtimes in Sublime
_current_clients = {}  # type: Dict[int, JsonRpcClient]
_highlight_key = "tune_generating_text"

# Shared client for suggestions
_shared_client = None  # type: Optional[JsonRpcClient]
_shared_lock = threading.Lock()


def plugin_loaded():
    global _shared_client
    with _shared_lock:
        if _shared_client is None:
            client, err = spawn_tune(
                exports={
                    "resolve": _ctx_resolve,
                    "read": _ctx_read,
                },
                cwd=_get_project_folder()
            )
            if err:
                print("tune: failed to start shared rpc:", err)
            else:
                _shared_client = client


def plugin_unloaded():
    for v_id, client in list(_current_clients.items()):
        try:
            client.stop()
        except Exception:
            pass
        _current_clients.pop(v_id, None)
    global _shared_client
    if _shared_client is not None:
        try:
            _shared_client.stop()
        except Exception:
            pass
        _shared_client = None


# Context exports matching tune.context in nvim

def _get_active_view() -> Optional[sublime.View]:
    win = sublime.active_window()
    if not win:
        return None
    return win.active_view()


def _get_project_folder() -> Optional[str]:
    """Get the first open folder from the active window, or None."""
    win = sublime.active_window()
    if not win:
        return None
    folders = win.folders()
    if folders and len(folders) > 0:
        return folders[0]
    return None


def _ctx_resolve(params):
    name = params.get("name") if isinstance(params, dict) else None
    tbl = {
        "editor/filename": {"name": "filename", "fullname": "editor/filename", "type": "text"},
        "editor/buffer": {"name": "buffer", "fullname": "editor/buffer", "type": "text"},
        "editor/buffers": {"name": "buffers", "fullname": "editor/buffers", "type": "text"},
        "editor/selection": {"name": "selection", "fullname": "editor/selection", "type": "text"},
    }
    return tbl.get(name, {"error": "not found"})


def _ctx_read(params):
    view = _get_active_view()
    if view is None:
        return ""
    name = params.get("name") if isinstance(params, dict) else None
    if name == "editor/filename":
        return view.file_name() or (view.name() or "")
    if name == "editor/buffer":
        return view.substr(sublime.Region(0, view.size()))
    if name == "editor/buffers":
        lines = []
        for w in sublime.windows():
            for v in w.views():
                fn = v.file_name() or v.name() or "untitled"
                lines.append(f"{v.id()} {fn}")
        return "\n".join(lines)
    if name == "editor/selection":
        sel = view.sel()
        if not sel:
            return ""
        parts = []
        for r in sel:
            parts.append(view.substr(r))
        return "\n".join(parts)
    return {"error": "not found"}


# Helpers

def _get_line_regions(view: sublime.View, line_index: int) -> sublime.Region:
    pt = view.text_point(line_index, 0)
    return view.line(pt)


def _get_line_count(view: sublime.View) -> int:
    # approximate by last row of end point
    return view.rowcol(view.size())[0] + 1


def _compute_split_bounds(view: sublime.View, cursor_row: int) -> Tuple[int, int, int]:
    # Mimic Lua logic: s_start, s_mid, s_end within the buffer
    roles = {
        "c", "comment", "s", "system", "u", "user", "a", "assistant",
        "tc", "tool_call", "tr", "tool_result", "err", "error"
    }
    total_lines = _get_line_count(view)
    s_start = 0  # 0-based row index
    s_mid: Optional[int] = None
    s_end = total_lines

    # Walk lines to find boundaries
    import re
    dash_header = re.compile(r"\s*\-\-\-.*")
    for idx in range(total_lines):
        line_region = view.full_line(view.text_point(idx, 0))
        line_text = view.substr(line_region).rstrip("\n")
        role = None
        content = None
        if ":" in line_text:
            prefix, rest = line_text.split(":", 1)
            role = prefix.strip()
            content = rest
        if role and role in roles:
            if s_mid is None and idx > cursor_row:
                s_mid = idx
            if role in ("comment", "c") and content is not None and dash_header.match(content):
                if idx < cursor_row:
                    s_start = idx + 1
                if idx > cursor_row and s_end == total_lines:
                    s_end = idx
    if s_mid is None:
        s_mid = s_end
    return s_start, s_mid, s_end


def _replace_lines(view: sublime.View, start_row: int, end_row: int, new_text: str) -> int:
    # Replace lines in [start_row, end_row) with new_text split by \n
    # Normalize end_row in case buffer shrank
    total = _get_line_count(view)
    start_row = max(0, min(start_row, total))
    end_row = max(0, min(end_row, total))

    if start_row > end_row:
        start_row, end_row = end_row, start_row

    start_pt = view.text_point(start_row, 0)
    if end_row >= total:
        end_pt = view.size()
        region = sublime.Region(start_pt, end_pt)
    else:
        end_pt = view.text_point(end_row, 0)
        region = sublime.Region(start_pt, end_pt)

    view.run_command("tune_replace_region", {"a": start_pt, "b": end_pt, "text": new_text})

    added_lines = 0
    if new_text:
        added_lines = new_text.count("\n") + 1
    return start_row + added_lines


def _highlight_rows(view: sublime.View, start_row: int, end_row: int):
    regions = []
    total = _get_line_count(view)
    start_row = max(0, min(start_row, total))
    end_row = max(0, min(end_row, total))
    for row in range(start_row, end_row):
        line = view.full_line(view.text_point(row, 0))
        regions.append(line)
    view.add_regions(
        _highlight_key,
        regions,
        scope="markup.changed",  # similar to DiffChange
        flags=(sublime.DRAW_NO_FILL | sublime.DRAW_NO_OUTLINE | sublime.DRAW_SOLID_UNDERLINE),
    )


class TuneReplaceRegionCommand(sublime_plugin.TextCommand):
    def run(self, edit, a: int, b: int, text: str):
        region = sublime.Region(a, b)
        self.view.replace(edit, region, text)


class TuneNewCommand(sublime_plugin.WindowCommand):
    def run(self, args: str = ""):
        # Create a new buffer
        v = self.window.new_file()
        v.set_name("")
        v.set_scratch(True)
        # Use correct package resource path
        v.assign_syntax("Packages/tune/syntaxes/Chat.sublime-syntax")
        initial_text = []
        if args:
            initial_text = [f"system: @@{args}", "user:", ""]
        else:
            initial_text = ["user:", ""]
        v.run_command("append", {"characters": "\n".join(initial_text)})
        # Move caret to last line, col 0
        v.sel().clear()
        last_row = max(0, len(initial_text) - 1)
        v.sel().add(sublime.Region(v.text_point(last_row, 0)))
        self.window.focus_view(v)
        v.run_command("enter_insert_mode") if hasattr(v, "run_command") else None


class TuneKillCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        vid = self.view.id()
        client = _current_clients.get(vid)
        if client is not None:
            client.stop()
            _current_clients.pop(vid, None)
        self.view.erase_regions(_highlight_key)


class TuneSaveCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        # If already has a filename, simply inform
        if self.view.file_name():
            sublime.status_message(f"Buffer already has a name: {self.view.file_name()}")
            return

        client, err = spawn_tune(
            exports={
                "resolve": _ctx_resolve,
                "read": _ctx_read,
            },
            cwd=_get_project_folder()
        )
        if err or not client:
            sublime.error_message(f"Tune: error starting RPC: {err}")
            return

        def cb(e, result):
            if e:
                sublime.error_message(f"Tune: error giving name: {e}")
                client.stop()
                return
            filename = (result or {}).get("filename") if isinstance(result, dict) else None
            if filename:
                # Set as tab name and mark as non-scratch
                self.view.set_name(filename)
                self.view.set_scratch(False)
                self.view.settings().set("tune.suggested_filename", filename)
            client.stop()

        params = {"filename": "editor-filename.chat", "stop": "assistant", "response": "json"}
        client.file2run(params, False, cb)


class TuneChatCommand(sublime_plugin.TextCommand):
    def run(self, edit, stop: str = "step"):
        # Kill any existing client for this view
        vid = self.view.id()
        if vid in _current_clients:
            try:
                _current_clients[vid].stop()
            except Exception:
                pass
            _current_clients.pop(vid, None)

        # Compute split bounds
        cursor = self.view.sel()[0].begin() if len(self.view.sel()) else 0
        cursor_row, cursor_col = self.view.rowcol(cursor)
        s_start, s_mid, s_end = _compute_split_bounds(self.view, cursor_row)

        # Clear previous highlights
        self.view.erase_regions(_highlight_key)

        # Render output helper
        state = {"s_mid": s_mid, "s_end": s_end, "res": ""}

        def render_output(completion: str):
            self.view.erase_regions(_highlight_key)
            new_text = completion
            # If inserting at a position that is not preceded by a newline,
            # make sure we start on a new line so we don't append to the
            # user's last line.
            try:
                start_pt = self.view.text_point(state["s_mid"], 0)
                if start_pt > 0:
                    prev = self.view.substr(sublime.Region(start_pt - 1, start_pt))
                    if prev != "\n":
                        new_text = "\n" + new_text
            except Exception:
                pass
            s_end_new = _replace_lines(self.view, state["s_mid"], state["s_end"], new_text)
            state["s_end"] = s_end_new
            _highlight_rows(self.view, state["s_mid"], state["s_end"])
            # move caret to end
            end_pt = self.view.text_point(state["s_end"], 0)
            self.view.sel().clear()
            self.view.sel().add(sublime.Region(end_pt))
            self.view.show(end_pt)

        # Spawn client
        filename = self.view.file_name() or (self.view.name() or "")
        # Grab beginning text [s_start, s_mid)
        begin_region = sublime.Region(self.view.text_point(s_start, 0), self.view.text_point(s_mid, 0))
        begin_text = self.view.substr(begin_region)

        client, err = spawn_tune(
            exports={
                "resolve": _ctx_resolve,
                "read": _ctx_read,
            },
            cwd=_get_project_folder()
        )
        if err or not client:
            render_output("err: \n" + f"tune: failed to start rpc: {err}")
            return

        _current_clients[vid] = client

        params = {
            "text": begin_text.rstrip("\n"),
            "stop": stop,
            "filename": filename,
            "response": "chat",
        }

        render_output("...")

        def on_chunk(e, chunk):
            if vid not in _current_clients or _current_clients[vid] is not client:
                return
            if e:
                msg = e.get("stack") or e.get("message") or str(e)
                r = state["res"] if state["res"] else ""
                if r:
                    render_output(r + "\nerr: \n" + msg)
                else:
                    render_output("err: \n" + msg)
                try:
                    client.stop()
                except Exception:
                    pass
                _current_clients.pop(vid, None)
                sublime.set_timeout(lambda: self.view.erase_regions(_highlight_key), 50)
                return
            if not chunk:
                return
            done = bool(chunk.get("done"))
            state["res"] = chunk.get("value") or ""
            sublime.set_timeout(lambda: render_output(state["res"]), 0)
            if done:
                try:
                    client.stop()
                except Exception:
                    pass
                _current_clients.pop(vid, None)
                sublime.set_timeout(lambda: self.view.erase_regions(_highlight_key), 50)

        client.file2run(params, True, on_chunk)


class TuneCompletions(sublime_plugin.EventListener):
    def on_query_completions(self, view: sublime.View, prefix: str, locations: List[int]):
        # Only in Chat files
        if view.match_selector(locations[0], "text.chat") is False and view.match_selector(locations[0], "text.prompt") is False:
            return None

        row, col = view.rowcol(locations[0])
        line_region = view.line(locations[0])
        line_text = view.substr(line_region)
        # Use buffer point offset within the line, not the visual column value
        before_cursor = line_text[: locations[0] - line_region.begin()]

        # Snippet-like completions when entire line is just 'u'/'s'/'c'
        if before_cursor in ("u", "s", "c") and (locations[0] - line_region.begin()) == 1:
            items = [
                ("user:\tSnippet", "user:\n"),
                ("system:\tSnippet", "system:\n"),
                ("c: -----------------------------------------\tSnippet", "c: -----------------------------------------\n"),
            ]
            return (items, sublime.INHIBIT_WORD_COMPLETIONS | sublime.INHIBIT_EXPLICIT_COMPLETIONS)

        # @mention completion
        import re
        m = re.search(r"@[^@\s]*$", before_cursor)
        if not m:
            return None
        query = before_cursor[m.start()+1:]

        clist = sublime.CompletionList()

        def fill():
            client = _shared_client
            if client is None:
                # try start on-demand
                plugin_loaded()
            client = _shared_client
            if client is None:
                clist.set_completions([], 0)
                return

            def cb(e, result):
                if e:
                    clist.set_completions([], 0)
                    return
                items = []
                if isinstance(result, list):
                    seen = set()
                    deduped = []
                    for raw in result:
                        # Determine label (name) for de-duplication
                        label = raw.get("name") if isinstance(raw, dict) else str(raw)
                        if not label or label in seen:
                            continue
                        seen.add(label)
                        deduped.append(raw)
                    for item in deduped:
                        label = item.get("name") if isinstance(item, dict) else str(item)
                        typ = item.get("type") if isinstance(item, dict) else None
                        src = item.get("source") if isinstance(item, dict) else None
                        ann_parts = []
                        if typ:
                            ann_parts.append(str(typ))
                        if src:
                            ann_parts.append("[{}]".format(src))
                        menu = " ".join(ann_parts)
                        items.append(sublime.CompletionItem.command_completion(
                            trigger=label,
                            annotation=menu,
                            command="insert", args={"characters": label},
                            kind=(sublime.KIND_ID_VARIABLE, "@", "tune"),
                        ))
                clist.set_completions(items, 0)

            client.suggest({"query": query}, False, cb)

        threading.Thread(target=fill, daemon=True).start()
        return clist

    def on_modified_async(self, view: sublime.View):
        try:
            # Only trigger in Chat syntax
            sel = view.sel()
            if len(sel) != 1:
                return
            caret = sel[0].end()
            if caret == 0:
                return
            # Require the file to be Chat
            if not view.match_selector(caret, "text.chat"):
                return
            # If AC already showing, don't interfere
            if view.is_auto_complete_visible():
                return
            # Check the just-typed character and that it's the first char on the line
            ch = view.substr(sublime.Region(caret - 1, caret))
            if ch not in ("u", "s"):
                return
            line_region = view.line(caret)
            # Ensure cursor is at column 1 (i.e., we just typed the first character)
            if caret - line_region.begin() != 1:
                return
            # Trigger autocomplete popup for our snippet completions
            view.run_command("auto_complete", {
                "disable_auto_insert": True,
                "api_completions_only": True,
                "next_completion_if_showing": False,
            })
        except Exception:
            pass


# Selection commands analogous to textobjects in nvim
class TuneSelectRoleCommand(sublime_plugin.TextCommand):
    def run(self, edit, inner: bool = False):
        start, end = self._find_role_bounds()
        if start is None:
            return
        if inner:
            start += 1
        a = self.view.text_point(start, 0)
        b = self.view.text_point(end, 0)
        self.view.sel().clear()
        self.view.sel().add(sublime.Region(a, self.view.line(b).end()))
        self.view.show(self.view.sel()[0])

    def _find_role_bounds(self) -> Tuple[Optional[int], int]:
        roles = {"c", "comment", "s", "system", "u", "user", "a", "assistant", "tc", "tool_call", "tr", "tool_result", "err", "error"}
        total = _get_line_count(self.view)
        cur = self.view.rowcol(self.view.sel()[0].begin())[0]
        lines = [self.view.substr(self.view.line(self.view.text_point(i, 0))) for i in range(total)]
        start_line = None
        end_line = total - 1
        for i in range(cur, -1, -1):
            line = lines[i]
            role = line.split(":", 1)[0] if ":" in line else None
            if role and role in roles:
                start_line = i
                break
        for i in range(cur + 1, total):
            line = lines[i]
            role = line.split(":", 1)[0] if ":" in line else None
            if role and role in roles:
                end_line = i - 1
                break
        return start_line, end_line


class TuneSelectChatCommand(sublime_plugin.TextCommand):
    def run(self, edit, inner: bool = False):
        start, end, header = self._find_chat_bounds()
        if inner:
            a = self.view.text_point(start, 0)
        else:
            a = self.view.text_point(header if header is not None else start, 0)
        b = self.view.text_point(end, 0)
        self.view.sel().clear()
        self.view.sel().add(sublime.Region(a, self.view.line(b).end()))
        self.view.show(self.view.sel()[0])

    def _find_chat_bounds(self) -> Tuple[int, int, Optional[int]]:
        total = _get_line_count(self.view)
        cur = self.view.rowcol(self.view.sel()[0].begin())[0]
        header_line = None
        start_line = 0
        end_line = total - 1
        import re
        for i in range(cur, -1, -1):
            text = self.view.substr(self.view.line(self.view.text_point(i, 0)))
            if ":" in text:
                r, c = text.split(":", 1)
                if r.strip() in ("c", "comment") and re.match(r"\s*\-\-\-.*", c):
                    start_line = i + 1
                    header_line = i
                    break
        for i in range(cur + 1, total):
            text = self.view.substr(self.view.line(self.view.text_point(i, 0)))
            if ":" in text:
                r, c = text.split(":", 1)
                if r.strip() in ("c", "comment") and re.match(r"\s*\-\-\-.*", c):
                    end_line = i - 1
                    break
        return start_line, end_line, header_line


class TuneSelectTailCommand(sublime_plugin.TextCommand):
    def run(self, edit, inner: bool = False):
        start, end = self._find_tail_bounds(inner)
        a = self.view.text_point(start, 0)
        b = self.view.text_point(end, 0)
        self.view.sel().clear()
        self.view.sel().add(sublime.Region(a, self.view.line(b).end()))
        self.view.show(self.view.sel()[0])

    def _find_tail_bounds(self, inner: bool) -> Tuple[int, int]:
        roles = {"c", "comment", "s", "system", "u", "user", "a", "assistant", "tc", "tool_call", "tr", "tool_result", "err", "error"}
        total = _get_line_count(self.view)
        cur = self.view.rowcol(self.view.sel()[0].begin())[0]
        start_line = cur
        for i in range(cur, -1, -1):
            text = self.view.substr(self.view.line(self.view.text_point(i, 0)))
            role = text.split(":", 1)[0] if ":" in text else None
            if role and role.strip() in roles:
                start_line = i
                break
        # find end chat boundary
        import re
        end_line = total - 1
        for i in range(cur + 1, total):
            text = self.view.substr(self.view.line(self.view.text_point(i, 0)))
            if ":" in text:
                r, c = text.split(":", 1)
                if r.strip() in ("c", "comment") and re.match(r"\s*\-\-\-.*", c):
                    end_line = i - 1
                    break
        if inner:
            start_line = start_line + 1
        return start_line, end_line


class TuneAutoSaveCommand(sublime_plugin.TextCommand):
    """Command that intercepts save and suggests filename if needed."""
    def run(self, edit):
        # If view already has a filename, just save normally
        if self.view.file_name():
            self.view.run_command("save")
            return
        
        # Otherwise, get a suggested filename
        client, err = spawn_tune(
            exports={
                "resolve": _ctx_resolve,
                "read": _ctx_read,
            },
            cwd=_get_project_folder()
        )
        if err or not client:
            # Fall back to normal save dialog
            self.view.window().run_command("save_as")
            return

        def cb(e, result):
            if e:
                client.stop()
                # Fall back to normal save dialog
                sublime.set_timeout(lambda: self.view.window().run_command("save_as"), 0)
                return
            
            filename = (result or {}).get("filename") if isinstance(result, dict) else None
            client.stop()
            
            if not filename:
                # No suggestion available, use normal save dialog
                sublime.set_timeout(lambda: self.view.window().run_command("save_as"), 0)
                return
            
            # Set the suggested name temporarily so it appears in the save dialog
            project_folder = _get_project_folder()
            if project_folder:
                suggested_path = os.path.join(project_folder, filename)
            else:
                suggested_path = filename
            
            # Set the name and open save dialog
            self.view.set_name(filename)
            sublime.set_timeout(lambda: self.view.window().run_command("save_as"), 0)

        params = {"filename": "editor-filename.chat", "stop": "assistant", "response": "json"}
        client.file2run(params, False, cb)


class TuneCleanupListener(sublime_plugin.EventListener):
    def on_close(self, view: sublime.View):
        vid = view.id()
        client = _current_clients.get(vid)
        if client is not None:
            try:
                client.stop()
            except Exception:
                pass
            _current_clients.pop(vid, None)
        view.erase_regions(_highlight_key)
