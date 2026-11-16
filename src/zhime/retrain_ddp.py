"""One corpus pass on a single node with rank-disjoint batches and resumable DDP."""
import argparse
import json
import math
import os
from pathlib import Path
import time

import numpy as np
import torch
import torch.distributed as dist
from torch.distributed.algorithms.ddp_comm_hooks import default_hooks
from torch.nn.parallel import DistributedDataParallel

from .model import LM
from .retrain import atomic_save, sha256


MODEL_200M = dict(vocab=8192, width=1024, layers=15, heads=16, context=128, causal_sdpa=True)


def initialize_large(model):
    with torch.no_grad():
        for name, parameter in model.named_parameters():
            if parameter.ndim < 2:
                continue
            std = .02
            if name.endswith(('attn.out_proj.weight', 'mlp.2.weight')):
                std /= math.sqrt(2 * len(model.blocks))
            torch.nn.init.normal_(parameter, std=std)


class Corpus:
    def __init__(self, path, context):
        manifest = json.loads(path.read_text())
        self.context = context
        self.offsets = np.arange(context + 1, dtype=np.int64)
        self.data, self.ends = [], []
        end = 0
        for shard in manifest['shards']:
            if Path(shard['file']).name != shard['file']:
                raise ValueError('Unexpected token shard path')
            file = path.parent / shard['file']
            if file.stat().st_size != 2 * shard['tokens']:
                raise ValueError(f'Size mismatch for {file}')
            count = max(0, (shard['tokens'] - 1) // context)
            if count:
                self.data.append(np.memmap(file, dtype='<u2', mode='r'))
                end += count
                self.ends.append(end)
        self.windows = end
        self.ends = np.asarray(self.ends, dtype=np.int64)

    def batch(self, windows):
        shard_ids = np.searchsorted(self.ends, windows, side='right')
        batch = np.empty((len(windows), self.context + 1), dtype=np.int64)
        for shard in np.unique(shard_ids):
            selected = shard_ids == shard
            start = self.ends[shard - 1] if shard else 0
            positions = (windows[selected] - start) * self.context
            batch[selected] = self.data[shard][positions[:, None] + self.offsets]
        return batch


def train(args, rank, world, device):
    torch.set_num_threads(2)
    torch.manual_seed(args.seed)
    torch.set_float32_matmul_precision('high')
    reference = torch.load(args.reference, map_location='cpu', weights_only=True)
    config = reference['config']
    del reference
    if args.model_size == '200m':
        config = MODEL_200M
    model = LM(**config).to(device)
    if args.model_size == '200m' and not args.resume:
        initialize_large(model)
    count = sum(p.numel() for p in model.parameters())
    expected_size = 14_880_000 if args.model_size == '15m' else 197_465_088
    if count != expected_size or config['vocab'] != 8192:
        raise ValueError('Unexpected model structure')
    manifest = json.loads(args.manifest.read_text())
    if manifest['tokenizer_sha256'] != sha256(args.tokenizer):
        raise ValueError('Token shards and tokenizer differ')
    context = config['context']
    corpus = Corpus(args.manifest, context)
    batch = args.batch * world
    steps = corpus.windows // batch
    if not steps:
        raise ValueError('Not enough tokens for a global batch')
    metadata = {'manifest_sha256': sha256(args.manifest), 'tokenizer_sha256': manifest['tokenizer_sha256'],
                'reference_sha256': sha256(args.reference), 'config': config, 'parameters': count,
                'world_size': world, 'batch_per_gpu': args.batch, 'global_batch': batch,
                'seed': args.seed, 'steps': steps, 'training_tokens': steps * batch * context,
                'corpus_tokens': manifest['totals']['tokens'], 'precision': 'BF16 autocast and BF16 gradient communication',
                'shuffle_block_steps': 64}
    if args.model_size == '200m':
        metadata['initialization'] = 'normal std 0.02, residual outputs scaled by sqrt(2 * layers)'
    args.out.mkdir(parents=True, exist_ok=True)
    run = args.out / 'run.json'
    state_path = args.out / 'resume.pt'
    if args.resume:
        if json.loads(run.read_text()) != metadata:
            raise ValueError('Resume identity mismatch')
    elif rank == 0:
        if run.exists():
            raise FileExistsError('Use --resume or a new output directory.')
        run.write_text(json.dumps(metadata, indent=2))
        print(json.dumps({'stage': 'training', **metadata}), flush=True)
    ddp = DistributedDataParallel(model, device_ids=[device.index], gradient_as_bucket_view=True)
    ddp.register_comm_hook(None, default_hooks.bf16_compress_hook)
    optimizer = torch.optim.AdamW(model.parameters(), lr=3e-4, weight_decay=.1, fused=True)
    first_step, previous_seconds = 1, 0.
    if args.resume:
        state = torch.load(state_path, map_location=device, weights_only=True)
        model.load_state_dict(state['model'])
        optimizer.load_state_dict(state['optimizer'])
        first_step, previous_seconds = state['step'] + 1, state['seconds']
        del state
    block_steps = metadata['shuffle_block_steps']
    full_blocks = steps // block_steps
    block_order = np.random.default_rng(args.seed).permutation(full_blocks)
    warmup = min(500, max(1, steps // 10))
    started = time.monotonic()
    stop = min(steps, args.stop_after) if args.stop_after else steps
    for step in range(first_step, stop + 1):
        block, within = divmod(step - 1, block_steps)
        first = (int(block_order[block]) if block < full_blocks else block) * block_steps * batch
        if within == 0 or step == first_step:
            size = min(block_steps, steps - block * block_steps) * batch
            shuffle = np.random.default_rng([args.seed, block]).permutation(size)
        lo = within * batch + rank * args.batch
        ids = torch.from_numpy(corpus.batch(first + shuffle[lo:lo + args.batch])).to(device)
        progress = max(0, step - warmup) / max(1, steps - warmup)
        scale = step / warmup if step <= warmup else .1 + .45 * (1 + math.cos(math.pi * progress))
        optimizer.param_groups[0]['lr'] = 3e-4 * scale
        optimizer.zero_grad(set_to_none=True)
        with torch.autocast('cuda', dtype=torch.bfloat16):
            _, loss = ddp(ids[:, :-1], ids[:, 1:])
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.)
        optimizer.step()
        if step == first_step or step % 100 == 0 or step == stop:
            average = loss.detach().float()
            dist.all_reduce(average, op=dist.ReduceOp.SUM)
            record = {'step': step, 'steps': steps, 'loss': average.item() / world,
                      'seconds': previous_seconds + time.monotonic() - started,
                      'training_tokens': step * batch * context,
                      'rank0_peak_allocated_bytes': torch.cuda.max_memory_allocated(device)}
            if not math.isfinite(record['loss']):
                raise RuntimeError(f'Nonfinite training loss at step {step}')
            if rank == 0:
                print(json.dumps(record), flush=True)
                with (args.out / 'train.jsonl').open('a') as stream:
                    stream.write(json.dumps(record) + '\n')
        if step % args.save_every == 0 or step == stop:
            if rank == 0:
                checkpoint = {'model': model.state_dict(), 'config': config, 'step': step, 'seed': args.seed}
                atomic_save(checkpoint, args.out / 'model.pt')
                atomic_save({**checkpoint, 'optimizer': optimizer.state_dict(),
                             'seconds': previous_seconds + time.monotonic() - started}, state_path)
            dist.barrier()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ('manifest', 'tokenizer', 'reference', 'out'):
        parser.add_argument('--' + arg, required=True, type=Path)
    parser.add_argument('--batch', type=int, default=64)
    parser.add_argument('--model-size', choices=['15m', '200m'], default='15m')
    parser.add_argument('--seed', type=int, default=20261112)
    parser.add_argument('--save-every', type=int, default=1000)
    parser.add_argument('--stop-after', type=int)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    rank = int(os.environ['LOCAL_RANK'])
    world = int(os.environ['WORLD_SIZE'])
    if world != 4 or torch.cuda.device_count() != world or args.batch < 1 or args.save_every < 1:
        raise ValueError('Expected four visible GPUs and positive batch/checkpoint settings')
    device = torch.device('cuda', rank)
    torch.cuda.set_device(device)
    if not torch.cuda.is_bf16_supported() or torch.ones(2, device=device).sum().item() != 2:
        raise RuntimeError(f'CUDA BF16 preflight failed on {device}')
    dist.init_process_group('nccl')
    try:
        train(args, rank, world, device)
    finally:
        dist.destroy_process_group()


if __name__ == '__main__':
    main()
