#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
run=retrain-1b
mkdir -p "$run/benchmark"
exec 9>"$run/pipeline.lock"
flock -n 9
exec >>"$run/pipeline.log" 2>&1
trap 'code=$?; if (( code != 0 )); then printf "failed exit=%s\n" "$code" >"$run/status"; fi' EXIT

printf 'training\n' >"$run/status"
printf 'GPU UUID %s\n' "${CUDA_VISIBLE_DEVICES:?Set CUDA_VISIBLE_DEVICES to one GPU UUID}"
nvidia-smi -i "$CUDA_VISIBLE_DEVICES" --query-gpu=uuid,name,memory.used,utilization.gpu --format=csv
train_options=()
if [[ ${1:-} == --resume ]]; then
    train_options+=(--resume)
fi
python -u -m zhime.retrain --data "$run/train.txt" --manifest "$run/manifest.json" \
    --tokenizer artifacts/tokenizer.model --reference artifacts/model.pt \
    --out "$run/artifacts" "${train_options[@]}"
sha256sum "$run/artifacts/model.pt" artifacts/tokenizer.model >"$run/weights.sha256"

for split in dev test; do
    printf 'scoring_%s\n' "$split" >"$run/status"
    if [[ ! -s "$run/benchmark/$split-results-v2.json" ]]; then
        python -u -m zhime.score_v2 --root "$run/benchmark" \
            --input "benchmark-v2/frozen-$split.jsonl" --split "$split" \
            --checkpoint "$run/artifacts/model.pt" --tokenizer artifacts/tokenizer.model --device cuda
    fi
done
python -m zhime.compare_retrain --before benchmark-v2 --after "$run/benchmark" --output "$run/comparison.json"
printf 'complete\n' >"$run/status"
