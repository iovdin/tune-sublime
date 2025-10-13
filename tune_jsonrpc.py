import json
import os
import queue
import shutil
import subprocess
import threading
import time
from typing import Any, Callable, Dict, Optional

# Lightweight JSON-RPC 2.0 client over stdio with newline-delimited JSON

class JsonRpcClient:
    def __init__(self, cmd, exports: Optional[Dict[str, Callable]] = None, cwd: Optional[str] = None):
        self.cmd = cmd
        self.exports = exports or {}
        self.cwd = cwd
        self.process: Optional[subprocess.Popen] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None
        self._write_lock = threading.Lock()
        self._id = 1
        self._callbacks: Dict[int, Callable] = {}
        self._iters: Dict[int, Callable] = {}
        self._closing = False
        self._errbuf = []

    @property
    def is_running(self):
        return self.process is not None and self.process.poll() is None

    def start(self) -> Optional[str]:
        try:
            self.process = subprocess.Popen(
                self.cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=1,
                universal_newlines=True,
                cwd=self.cwd,
            )
        except Exception as e:
            return str(e)

        self._reader_thread = threading.Thread(target=self._read_stdout, name="tune-rpc-stdout", daemon=True)
        self._reader_thread.start()
        self._stderr_thread = threading.Thread(target=self._read_stderr, name="tune-rpc-stderr", daemon=True)
        self._stderr_thread.start()
        return None

    def stop(self):
        self._closing = True
        try:
            if self.process and self.is_running:
                self.process.terminate()
                # Give it a moment, then kill if needed
                try:
                    self.process.wait(timeout=0.5)
                except Exception:
                    self.process.kill()
        finally:
            self.process = None

    # Dynamic RPC method: client.<method>(params, stream=False, callback)
    def __getattr__(self, name: str):
        def _call(params: Any = None, stream: bool = False, callback: Optional[Callable] = None):
            self._call(name, params, stream, callback)
        return _call

    def _call(self, method: str, params: Any, stream: bool, callback: Optional[Callable]):
        if not self.is_running:
            if callback:
                callback({"message": "process not running"}, None)
            return
        msg_id = self._id
        self._id += 1
        if callback:
            if stream:
                self._iters[msg_id] = callback
            else:
                self._callbacks[msg_id] = callback
        payload = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": method,
            "params": params,
            "stream": bool(stream),
        }
        self._write_json(payload)

    def _write_json(self, payload: Dict[str, Any]):
        if not self.is_running:
            return
        data = json.dumps(payload, ensure_ascii=False)
        with self._write_lock:
            try:
                assert self.process and self.process.stdin
                self.process.stdin.write(data + "\n")
                self.process.stdin.flush()
            except Exception:
                pass

    def _read_stdout(self):
        assert self.process and self.process.stdout
        for line in self.process.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception:
                continue

            # responses
            if isinstance(msg, dict) and "id" in msg and ("result" in msg or "error" in msg or "done" in msg):
                msg_id = msg.get("id")
                cb = self._callbacks.pop(msg_id, None)
                it = self._iters.get(msg_id)
                if cb is not None:
                    try:
                        cb(msg.get("error"), msg.get("result"))
                    except Exception:
                        pass
                elif it is not None:
                    done = bool(msg.get("done"))
                    try:
                        it(msg.get("error"), {"value": msg.get("result"), "done": done})
                    except Exception:
                        pass
                    if done:
                        self._iters.pop(msg_id, None)
                continue

            # requests from the server
            if isinstance(msg, dict) and msg.get("method"):
                method = msg["method"]
                req_id = msg.get("id")
                params = msg.get("params")
                result = None
                error = None
                func = self.exports.get(method)
                if func is None:
                    error = f"Method not found: {method}"
                else:
                    try:
                        result = func(params)
                    except Exception as e:
                        error = f"{e}"
                if req_id is not None:
                    if error is not None:
                        self._write_json({"jsonrpc": "2.0", "id": req_id, "error": {"message": error}})
                    else:
                        self._write_json({"jsonrpc": "2.0", "id": req_id, "result": result})

        # process ended: reject callbacks
        if not self._closing:
            err_text = "\n".join(self._errbuf)
            for _, cb in list(self._callbacks.items()):
                try:
                    cb({"message": err_text or "process exited"}, None)
                except Exception:
                    pass
            self._callbacks.clear()
            for _, it in list(self._iters.items()):
                try:
                    it({"message": err_text or "process exited"}, {"value": "", "done": True})
                except Exception:
                    pass
            self._iters.clear()

    def _read_stderr(self):
        assert self.process and self.process.stderr
        for line in self.process.stderr:
            self._errbuf.append(line.rstrip())


def _get_tune_sdk_path():
    """
    Get the tune-sdk executable path.
    Priority:
    1. Setting from Chat.sublime-settings
    2. Check if 'tune-sdk' exists in PATH
    3. Return 'tune-sdk' as fallback
    """
    # try:
    import sublime

    # First check user preferences
    user_settings = sublime.load_settings("Preferences.sublime-settings")
    sdk_path = user_settings.get("tune-sdk-path")

    # If not found, check package settings
    if not sdk_path:
        package_settings = sublime.load_settings("Chat.sublime-settings")
        sdk_path = package_settings.get("tune-sdk-path")
    print("sdk_path", sdk_path)
    
    if sdk_path:
        # Expand ~ to home directory
        sdk_path = os.path.expanduser(sdk_path)
        # If it's a relative or absolute path that exists, use it
        if os.path.isabs(sdk_path) and os.path.isfile(sdk_path):
            return sdk_path
        # If it's just a command name, check if it's in PATH
        if shutil.which(sdk_path):
            return sdk_path
        # If the setting exists but is not found, still try to use it
        # (it might be available at runtime)
        return sdk_path
    # except Exception:
    #     
    #     pass
    
    # Fallback: check if tune-sdk is in PATH
    which_result = shutil.which("tune-sdk")
    if which_result:
        return which_result
    
    # Final fallback
    return "tune-sdk"


def spawn_tune(exports: Optional[Dict[str, Callable]] = None, cwd: Optional[str] = None):
    env_path = os.environ.get("TUNE_PATH", "")
    tune_sdk = _get_tune_sdk_path()
    cmd = [tune_sdk, "rpc"]
    if env_path:
        cmd += ["--path", env_path]
    client = JsonRpcClient(cmd, exports=exports, cwd=cwd)
    err = client.start()
    if err:
        # Provide helpful error message
        error_msg = f"Failed to start tune-sdk: {err}\n\n"
        error_msg += "Setup instructions:\n\n"
        error_msg += "1. Install tune-sdk:\n"
        error_msg += "   npm install -g tune-sdk\n\n"
        error_msg += "2. Initialize configuration:\n"
        error_msg += "   tune-sdk init\n\n"
        error_msg += "3. Configure API keys in ~/.tune/.env:\n"
        error_msg += "   OPENAI_KEY=your_key_here\n\n"
        error_msg += "4. If tune-sdk is installed but not found:\n"
        error_msg += "   - Find its location: which tune-sdk\n"
        error_msg += "   - Add to Preferences.sublime-settings:\n"
        error_msg += '     "tune-sdk-path": "/path/to/tune-sdk"\n'
        return None, error_msg
    # advertise exports
    try:
        client.init(["resolve", "read"], False, lambda _e, _r: None)
    except Exception:
        pass
    return client, None
