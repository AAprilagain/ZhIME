# Source identities and reuse

- VimeML: https://github.com/Voltline/VimeML, commit `751392b9c623283eb470081049394d55f5b70a89`. The joint-tokenization scorer adapts `src/vimeml/benchmarks/zh13_lm_scores.py` to the ZhIME model interface. The GPL-3.0-or-later license, additional permission and notice are in [licenses/vimeml](licenses/vimeml).
- rime-ice: https://github.com/iDvel/rime-ice, commit `99662ba01e3affa6a0f009174ca530b887bac3b6`. The dictionary compiler converts pinned Chinese tables into the local lexicon. See [licenses/rime-ice](licenses/rime-ice) and the pinned source table headers for third-party attribution.
- Librime: https://github.com/rime/librime. The vendored BSD-licensed [API header](third_party/rime-api) is used by the Rime benchmark harness.
- Windows TSF interfaces: Microsoft Windows SDK. The native adapter is implemented in this project using the SDK interfaces; Rime is not a runtime dependency.

Model and dataset rights remain separate from these software notices. The local corpus export does not by itself prove provenance or redistribution rights.
