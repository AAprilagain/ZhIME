# Independent ZhIME

Select **ZhIME 自研全拼** with Win+Space in the application where you want to type. The backend starts automatically when the editor gains focus. Switching away expires the foreground lease after about three seconds and exits the GPU backend. After a DLL update, reopen any application that already loaded ZhIME.

To build a portable installer, rebuild the TSF DLL and run `python tools/package_native.py --python-zip <python-3.10.11-embed-amd64.zip> --output ../zhime-local/deliverables/ZhIME-Windows-x64 --zip` using the CUDA environment. Extract the resulting ZIP and run `Install-ZhIME.cmd`. The folder includes the Python runtime, GPU libraries, model and dictionary.

This engine and Windows x64 TSF adapter do not use Rime at runtime. Rime remains an isolated benchmark baseline. The frozen model is unchanged.

## Build and install

The source checkout and runtime are sibling directories. On a fresh machine, create `../zhime-local` and prepare `.venv-cuda` there with Python 3.10, CUDA PyTorch and SentencePiece. Copy `src/zhime` into that directory as `zhime-local/zhime`. The runtime directory needs an empty `automatic-start.enabled` file and a `runtime/` directory for the foreground lease and logs. Place the local checkpoint and matching tokenizer under `artifacts/` at the paths listed in the [model card](../MODEL_CARD.md). Copy `configs/deployment-local-gpu.json` to `artifacts/deployment-local-gpu.json`.

Run `powershell -ExecutionPolicy Bypass -File .\fetch-sources.ps1` from the source checkout to fetch pinned upstream sources into `../zhime-local/third_party`. From the runtime directory, compile the dictionary with `python -m zhime.lexicon --source third_party/rime-ice --output artifacts/lexicon`, then run `python -m zhime.compile_lexicon_snapshot`. Use a fresh output directory when recreating an existing frozen dictionary. See [third-party sources](../THIRD_PARTY.md).

The build uses VS2019 C++ and Windows SDK 10.0.16299.0. Run `powershell -ExecutionPolicy Bypass -File .\build-native.ps1` from the checkout. Output goes to `../zhime-local/build-dev` by default.

In an **Administrator PowerShell**, run `powershell -ExecutionPolicy Bypass -File .\install-native.ps1`. Registration adds the independent ZhIME language profile. Open an editor and switch to **ZhIME 自研全拼** with Win+Space.

## Controls

The local 197.47M model uses the RTX 3060 through the separate `.venv-cuda` environment and the profile in `artifacts/deployment-local-gpu.json`. The backend serves dictionary candidates while it verifies and warms up the model. For developer tests, `start-native.ps1 -Port 18771` runs an isolated service, `-Deployment` selects an explicit profile, and `-Legacy` selects the original CPU model. The native TSF client uses port 18770.

The local CUDA environment uses the Windows PyTorch 2.6.0 CUDA 12.4 wheel. To reproduce service timing, run `python tools/measure_gpu_ime.py --output runtime/gpu-200m-performance.json`. It measures warmed loopback requests, excluding application keystroke and rendering time.

When the deployment specifies `lm_weight`, candidates of equal input coverage are ordered by the beam score plus `lm_weight` times the model's joint conditional log-probability sum. The beam score already includes the word-frequency prior and input matching penalties. The first candidate pool, model fallback behavior, and later single-character pages keep their separate roles. See `benchmark-v2/retrain-200m/lm-weight-grid.json` for the development grid and fixed test comparison.

- Full pinyin, syllable initials, and mixed input are supported. Try `nh` or `nhao` for 你好 and `jtwsh` for 今天晚上. A complete full-pinyin match takes priority. Use `v` for ü and apostrophe to force a syllable boundary.
- Space: selected candidate. 1–5: candidate on the current page. Up/Down: selection. PageUp/PageDown or -/=: page. Mouse click: select.
- For one complete pinyin syllable, later pages include the remaining single characters stored under that reading. The model reranks the initial candidate pool, followed by the additional characters in dictionary order.
- Left/Right/Home/End: edit pinyin. Backspace/Delete: remove character. Escape: cancel. Enter: commit raw pinyin.
- Shift alone switches Chinese/English when no composition is active.
- After a complete Chinese candidate is committed, the model may show up to three short continuations in a separate **联想** list. Press the displayed digit or click a phrase to insert it. Each accepted phrase can trigger another suggestion list with the updated context. Esc dismisses the list, and typing another pinyin letter starts ordinary composition. Right-click the candidate window and toggle **上屏后联想** to turn this off for the current IME instance.
- Right-click an open candidate window to switch Chinese/English punctuation, clear recent committed context, or independently toggle z/zh, c/ch, s/sh, n/l, an/ang, en/eng. All fuzzy rules start off. The popup shows Chinese/English punctuation state.
- Reserved-key combinations are also registered, but some applications handle their own accelerators first. The tested acceptance paths are Shift and the candidate window menu; avoid relying on Ctrl+Space or Ctrl+Shift+P in editors that assign them to formatting actions.

The popup shows dictionary candidates as soon as the dictionary worker returns. The ranking worker handles its own requests, so a model pass for an older composition cannot delay the dictionary request for new keys. Both workers discard results for previous compositions. Navigation locks the current list until the next edit. The dictionary also caches relative syllable matches across edits, using the spelling window and fuzzy rules as the cache key. The Python backend loads its read-only trie before serving requests and keeps the startup objects out of cyclic garbage collection.

Focus changes clear the session history. Continuations use only recent text submitted by ZhIME and share its loaded model. The continuation request runs on a separate worker and is discarded after typing, focus change, or context clear. An unavailable or busy model leaves the continuation list closed. Dictionary candidates remain available on a model timeout or while the model warms up. If the entire engine backend is unavailable, the popup reports the connection failure and Enter can commit raw pinyin. There is no double pinyin, user learning or general typo correction in this version.

To remove the native profile, run `install-native.ps1 -Uninstall` as Administrator. Close applications using it before replacing the DLL. No current Rime configuration needs removal.

## Verification status

See the [project report](report.md) and [Windows acceptance record](../native/acceptance.json) for measured results and pending cases.

The continuation build was exercised in a Chromium textarea. Committing `今天晚上` showed the continuation list, and pressing `1` produced `今天晚上就开始`. Continued typing, clearing context and focus loss dismissed the list. This is an interaction smoke test. The existing reranking benchmark does not measure continuation quality or keystroke savings.
