#!/usr/bin/env bash
set -euo pipefail
stage=${1:?staging directory}
container=${2:?container}
target=${3:?container data directory}
exec 9>"$stage/bridge.lock"
flock -n 9
exec >>"$stage/bridge.log" 2>&1
trap 'code=$?; if (( code != 0 )); then printf "failed exit=%s\n" "$code" >"$stage/bridge.status"; fi' EXIT
printf 'copying\n' >"$stage/bridge.status"
docker exec "$container" mkdir -p "$target"
python3 - "$stage/inventory.json" >"$stage/inventory.tsv" <<'PY'
import json
import re
import sys

with open(sys.argv[1], encoding='utf-8-sig') as stream:
    for item in json.load(stream):
        name = item['Name']
        if not re.fullmatch(r'[a-z0-9_-]+\.parquet', name):
            raise ValueError(name)
        print(name, int(item['Length']), sep='\t')
PY
while IFS=$'\t' read -r name size; do
    source="$stage/parquet/$name"
    deadline=$((SECONDS + 3600))
    until [[ -f "$source" ]]; do
        if (( SECONDS >= deadline )); then printf 'Timed out waiting for %s\n' "$name"; exit 4; fi
        sleep 2
    done
    [[ $(stat -c %s "$source") == "$size" ]]
    if ! docker exec "$container" test -f "$target/$name"; then
        docker cp "$source" "$container:$target/$name.partial"
        docker exec "$container" mv -- "$target/$name.partial" "$target/$name"
    fi
    printf '%s %s bytes copied\n' "$name" "$size"
done <"$stage/inventory.tsv"
printf 'complete\n' >"$stage/bridge.status"
