#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
run=retrain-23b-200m
manifest=retrain-23b/tokens/manifest.json
mkdir -p "$run/benchmark"
exec 9>"$run/pipeline.lock"
flock -n 9
exec >>"$run/pipeline.log" 2>&1
trap 'code=$?; if (( code != 0 )); then printf "failed exit=%s\n" "$code" >"$run/status"; fi' EXIT

printf 'waiting_for_tokens\n' >"$run/status"
deadline=$((SECONDS + 7200))
until [[ -s "$manifest" ]]; do
    if (( SECONDS >= deadline )); then echo 'Token manifest did not become ready'; exit 4; fi
    sleep 10
done

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
    --manifest "$manifest" --tokenizer artifacts/tokenizer.model --reference artifacts/model.pt \
    --out "$run/artifacts" --model-size 200m --batch 32 "${options[@]}"
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
printf 'scoring_continuation\n' >"$run/status"
python -u -m zhime.score_continuation --items retrain-23b/external.jsonl \
    --overlap "$run/standard-training-overlap.json" --tokenizer artifacts/tokenizer.model \
    --old retrain-1b/artifacts/model.pt --new "$run/artifacts/model.pt" --output "$run/continuation"
printf 'complete\n' >"$run/status"
