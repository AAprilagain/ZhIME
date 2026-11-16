"""Loopback-only line protocol for the Rime Lua filter."""
import argparse
import socketserver
import os
import tempfile
import threading
import time
from pathlib import Path
import sentencepiece as spm
import torch
from .model import LM
from .rerank import score_candidates, ranked_indices

def rank_line(server, line):
    if len(line) > 32768 or not line.endswith(b'\n'):
        return None
    fields = line.decode('ascii').strip().split('\t')
    if len(fields) < 3 or fields[0] != 'R1' or len(fields) > 22:
        return None
    context = bytes.fromhex(fields[1]).decode('utf-8')[-128:]
    candidates = [bytes.fromhex(x).decode('utf-8') for x in fields[2:]]
    if any(not x or len(x) > 64 for x in candidates): return None
    with server.lock:
        scores = score_candidates(server.model, server.sp, context, candidates)
    order = ranked_indices(scores, server.alpha, server.length_exponent, candidates, server.preserve_length)
    return (','.join(str(i + 1) for i in order) + '\n').encode('ascii')

class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(1)
        try:
            result = rank_line(self.server, self.rfile.readline(32769))
            if result: self.wfile.write(result)
        except (ValueError, UnicodeError, OSError): pass

def spool_worker(server, folder, stop):
    folder.mkdir(parents=True, exist_ok=True)
    ready = folder / 'ready'
    try:
        heartbeat = 0
        while not stop.is_set():
            if time.monotonic() - heartbeat > .5:
                ready.write_text(str(int(time.time())), encoding='ascii'); heartbeat = time.monotonic()
            for path in folder.glob('*.req'):
                response = path.with_suffix('.res')
                try:
                    if time.time() - path.stat().st_mtime > 2:
                        path.unlink(); continue
                    line = path.read_bytes(); path.unlink()
                    result = rank_line(server, line)
                    if result:
                        fd, tmp = tempfile.mkstemp(dir=folder, suffix='.tmp')
                        with os.fdopen(fd, 'wb') as f: f.write(result)
                        os.replace(tmp, response)
                except (ValueError, UnicodeError, OSError): pass
            for path in folder.glob('*.res'):
                try:
                    if time.time() - path.stat().st_mtime > 10: path.unlink()
                except OSError: pass
            stop.wait(.01)
    finally:
        ready.unlink(missing_ok=True)

class Server(socketserver.TCPServer):
    allow_reuse_address = True

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--checkpoint', required=True); p.add_argument('--tokenizer', required=True)
    p.add_argument('--port', type=int, default=18765); p.add_argument('--alpha', type=float, default=.5)
    p.add_argument('--length-exponent', type=float, default=1.0)
    p.add_argument('--preserve-length', action='store_true')
    p.add_argument('--spool', default=os.path.join(os.environ.get('LOCALAPPDATA', tempfile.gettempdir()), 'ZhIME', 'spool'))
    a = p.parse_args()
    torch.set_num_threads(2)
    ckpt = torch.load(a.checkpoint, map_location='cpu', weights_only=True)
    model = LM(**ckpt['config']); model.load_state_dict(ckpt['model']); model.eval()
    sp = spm.SentencePieceProcessor(model_file=a.tokenizer)
    # Initialize PyTorch CPU kernels before accepting timing-sensitive Rime requests.
    score_candidates(model, sp, '输入上下文', ['候选', '后选'])
    with Server(('127.0.0.1', a.port), Handler) as server:
        server.model = model; server.sp = sp
        server.alpha = a.alpha; server.length_exponent = a.length_exponent
        server.preserve_length = a.preserve_length
        server.lock = threading.Lock(); stop = threading.Event()
        worker = threading.Thread(target=spool_worker, args=(server, Path(a.spool), stop), daemon=True); worker.start()
        print(f'Listening on 127.0.0.1:{a.port}; spool: {a.spool}', flush=True)
        try: server.serve_forever()
        finally: stop.set(); worker.join(timeout=2)

if __name__ == '__main__': main()
