"""Exercise the real loopback service with a model; print latency distribution."""
import argparse
import json
import socket
import subprocess
import sys
import time

def main():
    p = argparse.ArgumentParser(); p.add_argument('--checkpoint', required=True); p.add_argument('--tokenizer', required=True)
    p.add_argument('--port', type=int, default=18766); p.add_argument('--repeat', type=int, default=20); a = p.parse_args()
    child = subprocess.Popen([sys.executable, '-m', 'zhime.server', '--checkpoint', a.checkpoint, '--tokenizer', a.tokenizer, '--port', str(a.port)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 60
        while True:
            try:
                with socket.create_connection(('127.0.0.1', a.port), timeout=.5): break
            except OSError:
                if child.poll() is not None: raise RuntimeError(child.stderr.read())
                if time.monotonic() > deadline: raise TimeoutError('service did not start')
                time.sleep(.1)
        context = '我今天去'; candidates = ['北京', '背景', '北境', '被惊', '倍镜']
        request = '\t'.join(['R1'] + [s.encode().hex() for s in [context] + candidates]) + '\n'
        timings = []
        for _ in range(a.repeat):
            start = time.perf_counter()
            with socket.create_connection(('127.0.0.1', a.port), timeout=10) as conn:
                conn.sendall(request.encode('ascii')); data = conn.makefile('rb').readline().decode().strip()
            assert sorted(map(int, data.split(','))) == list(range(1, len(candidates)+1))
            timings.append((time.perf_counter()-start)*1000)
        timings.sort()
        print(json.dumps({'platform': sys.platform, 'calls': len(timings), 'candidate_count': len(candidates), 'min_ms': timings[0], 'p50_ms': timings[len(timings)//2], 'p95_ms': timings[min(len(timings)-1, int(len(timings)*.95))], 'max_ms': timings[-1]}, indent=2))
    finally:
        child.terminate()
        try: child.wait(timeout=5)
        except subprocess.TimeoutExpired: child.kill(); child.wait()

if __name__ == '__main__': main()
