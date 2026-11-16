import torch
from torch import nn
from torch.nn import functional as F

class Block(nn.Module):
    def __init__(self, width, heads, causal_sdpa=False):
        super().__init__()
        self.ln1 = nn.LayerNorm(width)
        self.attn = nn.MultiheadAttention(width, heads, batch_first=True)
        self.ln2 = nn.LayerNorm(width)
        self.mlp = nn.Sequential(nn.Linear(width, width * 4), nn.GELU(), nn.Linear(width * 4, width))
        self.heads = heads
        self.causal_sdpa = causal_sdpa

    def forward(self, x):
        y = self.ln1(x)
        if self.causal_sdpa:
            batch, length, width = y.shape
            qkv = F.linear(y, self.attn.in_proj_weight, self.attn.in_proj_bias)
            q, k, v = qkv.view(batch, length, 3, self.heads, width // self.heads).permute(2, 0, 3, 1, 4).unbind()
            attended = F.scaled_dot_product_attention(q, k, v, is_causal=True)
            update = self.attn.out_proj(attended.transpose(1, 2).reshape(batch, length, width))
        else:
            n = x.size(1)
            mask = torch.ones(n, n, dtype=torch.bool, device=x.device).triu(1)
            update = self.attn(y, y, y, attn_mask=mask, need_weights=False)[0]
        x = x + update
        return x + self.mlp(self.ln2(x))

class LM(nn.Module):
    def __init__(self, vocab=8192, width=400, layers=6, heads=8, context=128, causal_sdpa=False):
        super().__init__()
        self.config = dict(vocab=vocab, width=width, layers=layers, heads=heads, context=context)
        if causal_sdpa:
            self.config['causal_sdpa'] = True
        self.token = nn.Embedding(vocab, width)
        self.position = nn.Embedding(context, width)
        self.blocks = nn.ModuleList(Block(width, heads, causal_sdpa) for _ in range(layers))
        self.norm = nn.LayerNorm(width)

    def encode(self, ids):
        x = self.token(ids) + self.position(torch.arange(ids.size(1), device=ids.device))
        for block in self.blocks:
            x = block(x)
        return self.norm(x)

    def project(self, hidden):
        return F.linear(hidden, self.token.weight)

    def forward(self, ids, targets=None):
        logits = self.project(self.encode(ids))
        loss = None if targets is None else F.cross_entropy(logits.reshape(-1, logits.size(-1)), targets.reshape(-1), ignore_index=-100)
        return logits, loss
