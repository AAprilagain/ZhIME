#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
run=retrain-23b
mkdir -p "$run/benchmark"
exec 9>"$run/pipeline.lock"
flock -n 9
exec >>"$run/pipeline.log" 2>&1
trap 'code=$?; if (( code != 0 )); then printf "failed exit=%s\n" "$code" >"$run/status"; fi' EXIT

export PYTHONPATH="$PWD/$run/deps${PYTHONPATH:+:$PYTHONPATH}"
printf 'preparing_tokens\n' >"$run/status"
if [[ ! -s "$run/tokens/manifest.json" ]]; then
    python -u -m zhime.prepare_ddp --input "$run/parquet" --output "$run/tokens" \
        --inventory "$run/inventory.json" --tokenizer artifacts/tokenizer.model \
        --heldout "$run/heldout.jsonl" --external "$run/external.jsonl" --wait-input 3600
fi
printf 'checking_gpus\n' >"$run/status"
IFS=, read -r -a gpus <<<"${CUDA_VISIBLE_DEVICES:?Bind the four verified GPUs by UUID}"
if (( ${#gpus[@]} != 4 )); then exit 2; fi
for gpu in "${gpus[@]}"; do
    nvidia-smi -i "$gpu" --query-gpu=uuid,name,memory.used,utilization.gpu --format=csv,noheader
    active=$(nvidia-smi -i "$gpu" --query-compute-apps=pid --format=csv,noheader,nounits)
    if [[ -n "$active" ]]; then
        printf 'GPU %s has compute jobs: %s\n' "$gpu" "$active"
        exit 3
    fi
done
printf 'training\n' >"$run/status"
options=()
if [[ -s "$run/artifacts/resume.pt" ]]; then options+=(--resume); fi
torchrun --standalone --nproc-per-node=4 -m zhime.retrain_ddp \
    --manifest "$run/tokens/manifest.json" --tokenizer artifacts/tokenizer.model \
    --reference artifacts/model.pt --out "$run/artifacts" "${options[@]}"
sha256sum "$run/artifacts/model.pt" artifacts/tokenizer.model >"$run/weights.sha256"

export CUDA_VISIBLE_DEVICES=${gpus[0]}
for split in dev test; do
    printf 'scoring_%s\n' "$split" >"$run/status"
    if [[ ! -s "$run/benchmark/$split-results-v2.json" ]]; then
        python -u -m zhime.score_v2 --root "$run/benchmark" \
            --input "benchmark-v2/frozen-$split.jsonl" --split "$split" \
            --checkpoint "$run/artifacts/model.pt" --tokenizer artifacts/tokenizer.model --device cuda
    fi
done
python -m zhime.compare_retrain --before retrain-1b/benchmark --after "$run/benchmark" --output "$run/comparison.json"
printf 'complete\n' >"$run/status"
