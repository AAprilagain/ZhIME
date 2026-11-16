import argparse
import json
import random
import time
from pathlib import Path

import numpy as np
import sentencepiece as spm
import torch
from .model import LM

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--data', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--steps', type=int, default=20000)
    p.add_argument('--batch', type=int, default=32)
    p.add_argument('--seed', type=int, default=20261110)
    p.add_argument('--save-every', type=int, default=1000)
    a = p.parse_args()
    torch.manual_seed(a.seed); random.seed(a.seed); np.random.seed(a.seed)
    torch.set_num_threads(4)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    def log(record):
        line = json.dumps(record)
        print(line, flush=True)
        with (out / 'train.jsonl').open('a', encoding='utf-8') as f:
            f.write(line + '\n')
    prefix = str(out / 'tokenizer')
    if not (out / 'tokenizer.model').exists():
        spm.SentencePieceTrainer.train(input=a.data, model_prefix=prefix, vocab_size=8192, model_type='unigram', character_coverage=0.9995, input_sentence_size=2000000, shuffle_input_sentence=True, num_threads=4, hard_vocab_limit=False, bos_id=1, eos_id=2, unk_id=0)
    sp = spm.SentencePieceProcessor(model_file=prefix + '.model')
    tokens_path = out / 'tokens.npy'
    if not tokens_path.exists():
        ids = []
        with open(a.data, encoding='utf-8') as src:
            for line in src:
                ids.extend(sp.encode(line.strip(), out_type=int)); ids.append(sp.eos_id())
        np.save(tokens_path, np.asarray(ids, dtype=np.uint16))
    tokens = np.load(tokens_path, mmap_mode='r')
    if len(tokens) < 256:
        raise ValueError('not enough tokens')
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    model = LM(vocab=sp.vocab_size()).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=3e-4, weight_decay=.1)
    log({'device': device, 'gpu': torch.cuda.get_device_name() if device == 'cuda' else None, 'params': sum(p.numel() for p in model.parameters()), 'tokens': len(tokens), 'config': model.config})
    start = time.time()
    model.train()
    for step in range(1, a.steps + 1):
        offsets = np.random.randint(0, len(tokens) - 129, size=a.batch)
        batch = np.stack([tokens[i:i+129].astype(np.int64) for i in offsets])
        x = torch.from_numpy(batch).to(device)
        optimizer.zero_grad(set_to_none=True)
        _, loss = model(x[:, :-1], x[:, 1:])
        loss.backward(); torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0); optimizer.step()
        if step % 100 == 0 or step == 1:
            log({'step': step, 'loss': loss.item(), 'seconds': round(time.time()-start, 2)})
        if step % a.save_every == 0 or step == a.steps:
            torch.save({'model': model.state_dict(), 'config': model.config, 'step': step, 'seed': a.seed}, out / 'model.pt')

if __name__ == '__main__': main()
