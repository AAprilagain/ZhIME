"""Run the Windows backend for as long as a foreground ZhIME client is present."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys
import threading
import time


def main():
    root = Path.cwd()
    runtime = root / 'runtime'
    lease = runtime / 'foreground.lease'
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    kernel.CreateMutexW.restype = wintypes.HANDLE
    mutex = kernel.CreateMutexW(None, False, 'Local\\ZhIME.Autoservice.18770')
    if not mutex:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:
        return

    def watch():
        while True:
            try:
                fresh = time.time() - lease.stat().st_mtime <= 3.
            except OSError:
                fresh = False
            if not fresh:
                # Exiting the dedicated process also destroys the CUDA context.
                os._exit(0)
            time.sleep(.25)

    threading.Thread(target=watch, daemon=True).start()
    with (runtime / 'automatic-engine.log').open('w', encoding='utf-8', buffering=1) as log:
        sys.stdout = sys.stderr = log
        config = root / 'artifacts' / 'deployment-local-gpu.json'
        profile = json.loads(config.read_text(encoding='utf-8'))
        sys.argv = [sys.argv[0], '--config', str(config), '--checkpoint', profile['checkpoint'], '--automatic']
        from .independent_server import main as serve
        try:
            serve()
        except Exception:
            import traceback
            traceback.print_exc(file=log)
            log.flush()
            raise


if __name__ == '__main__':
    main()
