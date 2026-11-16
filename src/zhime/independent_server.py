"""Standalone IME engine/model HTTP service. No Rime imports or dependency."""
import argparse
import gc
import hashlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse,parse_qs
from .engine import Engine,FUZZY

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass  # Do not log input keys or context URLs.
    def do_GET(self):
        path=urlparse(self.path); args=parse_qs(path.query,keep_blank_values=True)
        if path.path=='/health':
            self.respond(json.dumps({'ready':True,'model':self.server.model is not None,'protocol':1,'suggestions':True,'abbreviated_pinyin':True,'single_character_paging':True,'configuration':getattr(self.server,'configuration',{})}).encode(),'application/json'); return
        if path.path not in ('/query','/suggest'): self.send_error(404); return
        try:
            keys=args.get('keys',[''])[0]
            request_id=int(args.get('id',['0'])[0]); mask=int(args.get('fuzzy',['0'])[0])
            session=args.get('session',['stateless'])[0]
            if len(session)>128 or not session.isascii():raise ValueError('session identifier')
            if not 0<=mask<64 or len(keys)>256:raise ValueError('request limits')
            context=bytes.fromhex(args.get('context',[''])[0]).decode('utf-8')[-128:]
            if path.path=='/suggest':
                self.suggest(request_id, session, context, args.get('format',[''])[0])
                return
            rank=args.get('rank',['0'])[0]=='1'
            enabled=tuple(name for i,name in enumerate(FUZZY) if mask&(1<<i))
            abbreviated=args.get('abbreviated',['1'])[0]=='1'
            candidates=list(self.server.engine.query(keys,enabled,abbreviated=abbreviated))
            status='dictionary'
            search_guided=False
            guided_weight=getattr(self.server,'guided_lm_weight',None)
            if rank and guided_weight is not None and self.server.model is not None:
                from .guided_search import scorer as search_scorer
                from .joint_score import ScoringFallback
                if self.server.lock.acquire(blocking=False):
                    try:
                        guided=self.server.engine.guided_query(
                            keys,enabled,search_scorer(self.server.model,self.server.sp,context,
                                                     self.server.device,budget=self.server.guided_budget),guided_weight)
                        if guided and (not candidates or guided[0].consumed>=candidates[0].consumed):
                            candidates=guided
                            status='model'
                            search_guided=True
                    except (ScoringFallback,RuntimeError):
                        pass
                    finally:
                        self.server.lock.release()
            if rank and len(candidates)>1 and status!='model':
                if self.server.model is None: status='fallback:model_unavailable'
                else:
                    from .joint_score import score_joint,score_separate_strict,order_pool,order_weighted_pool,ScoringFallback
                    try:
                        with self.server.lock:
                            scorer=score_joint if self.server.scorer=='joint' else score_separate_strict
                            scores,_=scorer(self.server.model,self.server.sp,context,[c.text for c in candidates],
                                            getattr(self.server,'device','cpu'))
                        coverage=[c.consumed for c in candidates]
                        weight=getattr(self.server,'lm_weight',None)
                        if weight is None:
                            order=order_pool(scores,coverage,self.server.alpha)
                        else:
                            order=order_weighted_pool(scores,coverage,[c.score for c in candidates],weight)
                        candidates=[candidates[i] for i in order]; status='model'
                    except ScoringFallback as e:status='fallback:'+str(e)
                    except RuntimeError:status='fallback:model_error'
            candidates.extend(self.server.engine.single_character_tail(keys,enabled,candidates))
            if args.get('format',[''])[0]=='json':
                from dataclasses import asdict
                payload={'session':session,'request_id':request_id,'status':status,
                         'candidates':[asdict(c) for c in candidates]}
                if guided_weight is not None:
                    payload['search_guided']=search_guided
                data=json.dumps(payload,ensure_ascii=False).encode()
                self.respond(data,'application/json')
            else:
                data=f'{request_id}\t{status}\n'+''.join(f'{c.text}\t{c.consumed}\n' for c in candidates)
                self.respond(data.encode('utf-8'),'text/plain; charset=utf-8')
        except (ValueError,UnicodeError):self.send_error(400,'Invalid request')
    def suggest(self, request_id, session, context, output_format):
        phrases = []
        status = 'fallback:model_unavailable'
        if self.server.model is not None:
            status = 'fallback:model_busy'
            if self.server.lock.acquire(blocking=False):
                try:
                    from .suggest import suggest
                    phrases = suggest(self.server.model, self.server.sp, context)
                    status = 'suggestion'
                except RuntimeError:
                    status = 'fallback:model_error'
                finally:
                    self.server.lock.release()
        if output_format == 'json':
            data = {'session': session, 'request_id': request_id, 'status': status, 'suggestions': phrases}
            self.respond(json.dumps(data, ensure_ascii=False).encode(), 'application/json')
        else:
            body = f'{request_id}\t{status}\n' + ''.join(f'{phrase}\t0\n' for phrase in phrases)
            self.respond(body.encode(), 'text/plain; charset=utf-8')
    def respond(self,data,kind):
        try:
            self.send_response(200); self.send_header('Content-Type',kind); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
        except (BrokenPipeError,ConnectionResetError):pass

def load_model(a,config):
    device=config.get('device','cpu')
    precision=config.get('precision','float32')
    model=sp=None
    if Path(a.checkpoint).exists():
        for key,path in [('model_sha256',a.checkpoint),('tokenizer_sha256',a.tokenizer)]:
            if key in config:
                digest=hashlib.sha256()
                with Path(path).open('rb') as stream:
                    for chunk in iter(lambda:stream.read(4*1024*1024),b''):
                        digest.update(chunk)
                if digest.hexdigest()!=config[key]:raise ValueError('Frozen deployment identity mismatch: '+key)
        import torch
        import sentencepiece as spm
        from .model import LM
        from .joint_score import score_joint,ScoringFallback
        torch.set_num_threads(2)
        if device.startswith('cuda') and not torch.cuda.is_available():
            raise RuntimeError('CUDA is required for the selected deployment.')
        dtype={'float32':torch.float32,'float16':torch.float16}[precision]
        ckpt=torch.load(a.checkpoint,map_location='cpu',weights_only=True,mmap=True)
        with torch.device('meta'):
            model=LM(**ckpt['config'])
        model.load_state_dict(ckpt['model'],assign=True)
        model.to(device=device,dtype=dtype).eval()
        del ckpt
        sp=spm.SentencePieceProcessor(model_file=a.tokenizer)
        try:
            score_joint(model,sp,'今天',['去北京','去背景'],device)
            if device.startswith('cuda'):
                from .suggest import suggest
                suggest(model,sp,'今天晚上',budget_seconds=1.)
        except ScoringFallback:pass
    elif device.startswith('cuda'):
        raise FileNotFoundError(a.checkpoint)
    return model,sp


def main():
    p=argparse.ArgumentParser(); p.add_argument('--lexicon',default='artifacts/lexicon/lexicon.sqlite'); p.add_argument('--checkpoint',default='artifacts/model.pt'); p.add_argument('--tokenizer',default='artifacts/tokenizer.model'); p.add_argument('--port',type=int,default=18770); p.add_argument('--config',default='artifacts/deployment.json'); p.add_argument('--automatic',action='store_true'); a=p.parse_args()
    config=json.loads(Path(a.config).read_text(encoding='utf-8')) if Path(a.config).exists() else {'beam':64,'fuzzy_penalty':3,'alpha':.5}
    start=time.monotonic()
    # The trie is acyclic and stays resident. Skip cyclic collection while
    # loading it, then keep the startup heap out of future collection scans.
    gc_enabled=gc.isenabled()
    gc.disable()
    try:
        if a.automatic and config.get('lexicon_snapshot_sha256'):
            engine=Engine.from_snapshot(Path(a.lexicon).with_suffix('.marshal'),config['lexicon_snapshot_sha256'],
                                        config['beam'],config['fuzzy_penalty'])
        else:
            engine=Engine(a.lexicon,beam=config['beam'],fuzzy_penalty=config['fuzzy_penalty'])
        gc.freeze()
    finally:
        if gc_enabled:
            gc.enable()
    model,sp=(None,None) if a.automatic else load_model(a,config)
    server=ThreadingHTTPServer(('127.0.0.1',a.port),Handler)
    server.engine=engine; server.model=model; server.sp=sp; server.alpha=config['alpha']; server.scorer=config.get('scorer','joint'); server.lock=threading.Lock();server.configuration=config
    server.device=config.get('device','cpu')
    server.lm_weight=config.get('lm_weight')
    server.guided_lm_weight=config.get('guided_lm_weight')
    server.guided_budget=config.get('guided_search_budget_ms',120)/1000
    print(json.dumps({'ready':True,'port':a.port,'startup_seconds':time.monotonic()-start,'config':config}),flush=True)
    if a.automatic:
        def warm_model():
            try:
                loaded,tokenizer=load_model(a,config)
                with server.lock:
                    server.sp=tokenizer
                    server.model=loaded
                print(json.dumps({'model_ready':loaded is not None,'seconds':time.monotonic()-start}),flush=True)
            except Exception:
                import traceback
                traceback.print_exc()
        threading.Thread(target=warm_model,daemon=True).start()
    try:server.serve_forever()
    finally:server.server_close()

if __name__=='__main__':main()
