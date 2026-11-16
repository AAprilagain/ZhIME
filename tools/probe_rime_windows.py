"""Isolated Windows librime smoke test using a fake spool reranker, no desktop changes."""
import argparse
import ctypes as c
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time

class Traits(c.Structure):
    _fields_ = [('data_size', c.c_int), ('shared_data_dir', c.c_char_p), ('user_data_dir', c.c_char_p),
                ('distribution_name', c.c_char_p), ('distribution_code_name', c.c_char_p), ('distribution_version', c.c_char_p),
                ('app_name', c.c_char_p), ('modules', c.c_void_p), ('min_log_level', c.c_int), ('log_dir', c.c_char_p),
                ('prebuilt_data_dir', c.c_char_p), ('staging_dir', c.c_char_p)]
class Candidate(c.Structure):
    _fields_ = [('text', c.c_char_p), ('comment', c.c_char_p), ('reserved', c.c_void_p)]
class Iterator(c.Structure):
    _fields_ = [('ptr', c.c_void_p), ('index', c.c_int), ('candidate', Candidate)]

def main():
    p = argparse.ArgumentParser(); p.add_argument('--weasel', default=r'C:\Program Files\Rime\weasel-0.17.4'); p.add_argument('--real', action='store_true'); a = p.parse_args()
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='zhime-rime-') as tmp:
        home = Path(tmp); user = home / 'profile'; user.mkdir(); (user / 'lua').mkdir()
        for name in ('zhime.lua', 'zhime_clear.lua'): shutil.copy(root / 'rime' / name, user / 'lua' / name)
        shutil.copy(root / 'rime' / 'luna_pinyin.custom.yaml', user / 'luna_pinyin.custom.yaml')
        old = os.environ.get('LOCALAPPDATA'); os.environ['LOCALAPPDATA'] = tmp
        spool = home / 'ZhIME' / 'spool'; spool.mkdir(parents=True)
        stop = threading.Event(); observed = []
        def worker():
            while not stop.is_set():
                (spool / 'ready').write_text(str(int(time.time())), encoding='ascii')
                for req in spool.glob('*.req'):
                    try:
                        fields = req.read_text(encoding='ascii').strip().split('\t'); req.unlink()
                        observed.append(fields)
                        output = ','.join(str(i) for i in range(len(fields)-2, 0, -1)) + '\n'
                        staging = req.with_suffix('.reply')
                        staging.write_text(output, encoding='ascii')
                        os.replace(staging, req.with_suffix('.res'))
                    except OSError: pass
                stop.wait(.002)
        if a.real:
            import types
            import sentencepiece as spm
            import torch
            from zhime.model import LM
            from zhime import server
            torch.set_num_threads(2)
            ckpt = torch.load(root / 'artifacts/model.pt', map_location='cpu', weights_only=True)
            model = LM(**ckpt['config']); model.load_state_dict(ckpt['model']); model.eval()
            srv = types.SimpleNamespace(model=model, sp=spm.SentencePieceProcessor(model_file=str(root / 'artifacts/tokenizer.model')),
                                        alpha=.5, length_exponent=0, preserve_length=True, lock=threading.Lock())
            original_rank = server.rank_line
            def track(s, line):
                answer = original_rank(s, line)
                if answer: observed.append((line.decode().strip().split('\t'), answer.decode().strip()))
                return answer
            server.rank_line = track
            thread = threading.Thread(target=server.spool_worker, args=(srv, spool, stop), daemon=True)
        else:
            thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        dll_dir = os.add_dll_directory(a.weasel); dll = c.CDLL(str(Path(a.weasel) / 'rime.dll'))
        def bind(name, restype, *argtypes):
            f = getattr(dll, 'Rime' + name); f.restype = restype; f.argtypes = list(argtypes); return f
        setup = bind('Setup', None, c.POINTER(Traits)); initialize = bind('Initialize', None, c.POINTER(Traits))
        maintenance = bind('StartMaintenance', c.c_int, c.c_int); join = bind('JoinMaintenanceThread', None)
        create = bind('CreateSession', c.c_size_t); select = bind('SelectSchema', c.c_int, c.c_size_t, c.c_char_p)
        key = bind('ProcessKey', c.c_int, c.c_size_t, c.c_int, c.c_int)
        begin = bind('CandidateListBegin', c.c_int, c.c_size_t, c.POINTER(Iterator))
        next_ = bind('CandidateListNext', c.c_int, c.POINTER(Iterator)); end = bind('CandidateListEnd', None, c.POINTER(Iterator))
        destroy = bind('DestroySession', c.c_int, c.c_size_t); finalize = bind('Finalize', None)
        t = Traits(); t.data_size = c.sizeof(Traits)-c.sizeof(c.c_int); t.shared_data_dir = str(Path(a.weasel)/'data').encode(); t.user_data_dir = str(user).encode(); t.app_name = b'rime.zhime_probe'
        try:
            setup(c.byref(t)); initialize(c.byref(t))
            if maintenance(1): join()
            session = create(); assert session and select(session, b'luna_pinyin')
            for ch in 'nihao': key(session, ord(ch), 0)
            it = Iterator(); items = []; comments = []
            if begin(session, c.byref(it)):
                while len(items) < 20 and next_(c.byref(it)):
                    items.append(it.candidate.text.decode('utf-8'))
                    comments.append((it.candidate.comment or b'').decode('utf-8'))
                end(c.byref(it))
            destroy(session); finalize()
            assert observed, 'filter did not request a rerank'
            if a.real:
                fields, answer = observed[-1]
                original = [bytes.fromhex(s).decode('utf-8') for s in fields[2:]]
                expected = [original[int(i)-1] for i in answer.split(',')]
            else:
                fields = observed[-1]; original = [bytes.fromhex(s).decode('utf-8') for s in fields[2:]]
                expected = list(reversed(original))
            assert items[:len(original)] == expected, 'Rime candidates do not match the service response'
            assert '[模型]' in comments[0], 'Model status marker is missing'
            print('Windows librime Lua filter and ' + ('model service' if a.real else 'fake service') + ' reranking passed; candidates:', len(original))
        finally:
            stop.set(); thread.join(timeout=2); dll_dir.close()
            if old is None: os.environ.pop('LOCALAPPDATA', None)
            else: os.environ['LOCALAPPDATA'] = old

if __name__ == '__main__': main()
