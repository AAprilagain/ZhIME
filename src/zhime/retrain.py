"""Train fresh weights on a fixed-tokenizer corpus without holding it in RAM."""
import argparse
import hashlib
import itertools
import json
import math
import time
from pathlib import Path

import numpy as np
import sentencepiece as spm
import torch

from .model import LM


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def encode_corpus(corpus, tokenizer, output):
    tokens = output / 'tokens.u16'
    partial = tokens.with_suffix('.partial')
    started = time.monotonic()
    with corpus.open(encoding='utf-8') as source, partial.open('wb') as destination:
        while lines := list(itertools.islice(source, 2048)):
            encoded = tokenizer.encode([line.rstrip('\n') for line in lines],
                                       out_type=int, num_threads=6, add_eos=True)
            np.fromiter(itertools.chain.from_iterable(encoded), dtype='<u2').tofile(destination)
    partial.replace(tokens)
    print(json.dumps({'stage': 'encoding_complete', 'seconds': time.monotonic() - started,
                      'corpus_tokens': tokens.stat().st_size // 2}), flush=True)
    return tokens


def atomic_save(payload, path):
    partial = path.with_suffix('.partial')
    torch.save(payload, partial)
    partial.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--tokenizer', type=Path, required=True)
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--batch', type=int, default=64)
    parser.add_argument('--seed', type=int, default=20261112)
    parser.add_argument('--save-every', type=int, default=500)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
        raise RuntimeError('Bind exactly one available GPU before training.')
    if not torch.cuda.is_bf16_supported():
        raise RuntimeError('This training configuration needs CUDA BF16 support.')
    if args.batch < 1 or args.save_every < 1:
        parser.error('--batch and --save-every must be positive')
    torch.set_num_threads(4)
    torch.manual_seed(args.seed)
    torch.set_float32_matmul_precision('high')
    args.out.mkdir(parents=True, exist_ok=True)
    metadata_path = args.out / 'run.json'
    state_path = args.out / 'resume.pt'
    if not args.resume and metadata_path.exists():
        raise FileExistsError(f'{metadata_path} exists. Use --resume or a new output directory.')
    manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
    data_hash = sha256(args.data)
    if data_hash != manifest['train_sha256']:
        raise ValueError('Training text does not match the sampling manifest.')
    tokenizer = spm.SentencePieceProcessor(model_file=str(args.tokenizer))
    reference = torch.load(args.reference, map_location='cpu', weights_only=True)
    config = reference['config']
    if tokenizer.vocab_size() != config['vocab']:
        raise ValueError('The tokenizer and reference model vocabulary sizes differ.')
    del reference
    model = LM(**config).cuda()
    parameter_count = sum(parameter.numel() for parameter in model.parameters())
    if parameter_count != 14_880_000:
        raise ValueError(f'Expected 14,880,000 parameters, found {parameter_count}.')
    identity = {'data_sha256': data_hash, 'tokenizer_sha256': sha256(args.tokenizer),
                'reference_sha256': sha256(args.reference), 'seed': args.seed, 'batch': args.batch,
                'config': config, 'parameters': parameter_count, 'precision': 'bf16 autocast',
                'gpu': torch.cuda.get_device_name(0)}
    tokens_path = args.out / 'tokens.u16'
    if args.resume:
        saved_metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
        if identity != {key: saved_metadata[key] for key in identity}:
            raise ValueError('Resume settings or input files differ from the original run.')
    else:
        tokens_path = encode_corpus(args.data, tokenizer, args.out)
    tokens = np.memmap(tokens_path, dtype='<u2', mode='r')
    context = config['context']
    sequence_count = (len(tokens) - 1) // context
    if sequence_count < args.batch:
        raise ValueError('Too few complete sequences for one batch.')
    order = np.random.default_rng(args.seed).permutation(sequence_count).astype(np.int32)
    steps = math.ceil(sequence_count / args.batch)
    metadata = {**identity, 'corpus_tokens': len(tokens), 'steps': steps,
                'planned_training_tokens': sequence_count * context, 'corpus_passes': 1}
    if args.resume and metadata != saved_metadata:
        raise ValueError('Resume token count differs from the original run.')
    if not args.resume:
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    print(json.dumps({'stage': 'training', **metadata}), flush=True)

    optimizer = torch.optim.AdamW(model.parameters(), lr=3e-4, weight_decay=.1, fused=True)
    first_step, prior_seconds = 1, 0.
    if args.resume:
        state = torch.load(state_path, map_location='cuda', weights_only=True)
        model.load_state_dict(state['model'])
        optimizer.load_state_dict(state['optimizer'])
        first_step, prior_seconds = state['step'] + 1, state['seconds']
        del state
    started = time.monotonic()
    offsets = np.arange(context + 1, dtype=np.int64)
    warmup = min(500, max(1, steps // 10))
    for step in range(first_step, steps + 1):
        progress = max(0, step - warmup) / max(1, steps - warmup)
        scale = step / warmup if step <= warmup else .1 + .9 * (.5 + .5 * math.cos(math.pi * progress))
        optimizer.param_groups[0]['lr'] = 3e-4 * scale
        windows = order[(step - 1) * args.batch:step * args.batch].astype(np.int64)
        batch = np.asarray(tokens[windows[:, None] * context + offsets], dtype=np.int64)
        ids = torch.from_numpy(batch).cuda()
        optimizer.zero_grad(set_to_none=True)
        with torch.autocast('cuda', dtype=torch.bfloat16):
            _, loss = model(ids[:, :-1], ids[:, 1:])
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.)
        optimizer.step()
        if step == first_step or step % 100 == 0 or step == steps:
            record = {'step': step, 'steps': steps, 'loss': loss.item(),
                      'seconds': prior_seconds + time.monotonic() - started,
                      'training_tokens': min(step * args.batch, sequence_count) * context,
                      'gpu_peak_allocated_bytes': torch.cuda.max_memory_allocated()}
            if not math.isfinite(record['loss']):
                raise RuntimeError(f'Nonfinite training loss at step {step}.')
            print(json.dumps(record), flush=True)
            with (args.out / 'train.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(record) + '\n')
        if step % args.save_every == 0 or step == steps:
            checkpoint = {'model': model.state_dict(), 'config': config, 'step': step, 'seed': args.seed}
            atomic_save(checkpoint, args.out / 'model.pt')
            atomic_save({**checkpoint, 'optimizer': optimizer.state_dict(),
                         'seconds': prior_seconds + time.monotonic() - started}, state_path)


if __name__ == '__main__':
    main()
