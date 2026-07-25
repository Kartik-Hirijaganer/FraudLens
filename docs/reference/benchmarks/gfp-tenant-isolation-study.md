# GFP tenant-isolation study (offline; public synthetic data)

> Generated from the committed study JSON — do not edit by hand. Offline-only:
> no scope of these graph features may serve (ADR-017).

Adding complete GFP graph features moved holdout PR-AUC from 0.3156 to 0.5009 on ibm-aml (+0.1853, 95% CI [0.1218, 0.1965]). Serving is deferred under strict tenant isolation (ADR-017).

- Run: `gfp-c41b1fbb266f44d4` | engine: snapml 1.17.2 | seed: 1729
- Protocol: `config/gfp-benchmark.yaml` (sha256 `8a479c80f065...`)
- Libraries: numpy 2.4.6, pandas 3.0.3, scikit-learn 1.9.0, xgboost 3.2.0

## Datasets

| Source | Context | Source rows | Context edges | Targets | Illicit ratio | Hash fraction |
|---|---|---|---|---|---|---|
| ibm-aml | full | 5078345 | 5054380 | 5054380 | 0.001024 | n/a |
| ibm-aml-hi-medium | node_induced | 31898238 | 2550078 | 1000000 | 0.000863 | 1/4 |
| ibm-aml-li-medium | node_induced | 31251483 | 2314977 | 1000000 | 0.000372 | 1/4 |

## ibm-aml — arm metrics

| Arm | Scope | PR-AUC | Norm. lift | ROC-AUC | Brier | ECE | P@0.1% | R@0.1% | Min. F1 |
|---|---|---|---|---|---|---|---|---|---|
| A | shared | 0.3156 | 177.6 | 0.9467 | 0.00143 | 0.0005 | 0.500 | 0.281 | 0.365 |
| B | global | 0.4932 | 277.6 | 0.9643 | 0.00115 | 0.0002 | 0.746 | 0.420 | 0.539 |
| C | global | 0.5009 | 281.9 | 0.9646 | 0.00113 | 0.0003 | 0.758 | 0.427 | 0.543 |
| B | per_tenant | 0.4866 | 273.9 | 0.9643 | 0.00116 | 0.0002 | 0.735 | 0.414 | 0.531 |
| C | per_tenant | 0.4988 | 280.7 | 0.9642 | 0.00114 | 0.0002 | 0.753 | 0.424 | 0.542 |

## ibm-aml-hi-medium — arm metrics

| Arm | Scope | PR-AUC | Norm. lift | ROC-AUC | Brier | ECE | P@0.1% | R@0.1% | Min. F1 |
|---|---|---|---|---|---|---|---|---|---|
| A | shared | 0.0416 | 29.2 | 0.8812 | 0.00140 | 0.0004 | 0.110 | 0.077 | 0.074 |
| B | global | 0.2209 | 155.0 | 0.9203 | 0.00124 | 0.0002 | 0.395 | 0.277 | 0.310 |
| C | global | 0.2190 | 153.7 | 0.9227 | 0.00124 | 0.0001 | 0.385 | 0.270 | 0.296 |
| B | per_tenant | 0.2020 | 141.8 | 0.9220 | 0.00126 | 0.0002 | 0.345 | 0.242 | 0.259 |
| C | per_tenant | 0.2135 | 149.8 | 0.9198 | 0.00125 | 0.0002 | 0.380 | 0.267 | 0.308 |

## ibm-aml-li-medium — arm metrics

| Arm | Scope | PR-AUC | Norm. lift | ROC-AUC | Brier | ECE | P@0.1% | R@0.1% | Min. F1 |
|---|---|---|---|---|---|---|---|---|---|
| A | shared | 0.0060 | 11.2 | 0.8688 | 0.00054 | 0.0001 | 0.010 | 0.019 | 0.011 |
| B | global | 0.0239 | 44.3 | 0.9074 | 0.00053 | 0.0000 | 0.055 | 0.102 | 0.070 |
| C | global | 0.0197 | 36.5 | 0.9146 | 0.00054 | 0.0000 | 0.050 | 0.093 | 0.066 |
| B | per_tenant | 0.0166 | 30.8 | 0.9225 | 0.00054 | 0.0001 | 0.055 | 0.102 | 0.043 |
| C | per_tenant | 0.0167 | 31.0 | 0.9164 | 0.00054 | 0.0001 | 0.065 | 0.120 | 0.073 |

## Arm deltas (paired bootstrap 95% CI)

| Dataset | From | To | Scope | ΔPR-AUC | 95% CI |
|---|---|---|---|---|---|
| ibm-aml | A | B | global | +0.1777 | [+0.1095, +0.1819] |
| ibm-aml | B | C | global | +0.0076 | [+0.0023, +0.0255] |
| ibm-aml | A | C | global | +0.1853 | [+0.1218, +0.1965] |
| ibm-aml | A | B | per_tenant | +0.1711 | [+0.1136, +0.1823] |
| ibm-aml | B | C | per_tenant | +0.0121 | [+0.0004, +0.0196] |
| ibm-aml | A | C | per_tenant | +0.1832 | [+0.1247, +0.1946] |
| ibm-aml-hi-medium | A | B | global | +0.1793 | [+0.1322, +0.2300] |
| ibm-aml-hi-medium | B | C | global | -0.0019 | [-0.0138, +0.0103] |
| ibm-aml-hi-medium | A | C | global | +0.1774 | [+0.1282, +0.2255] |
| ibm-aml-hi-medium | A | B | per_tenant | +0.1604 | [+0.1185, +0.2031] |
| ibm-aml-hi-medium | B | C | per_tenant | +0.0115 | [-0.0014, +0.0265] |
| ibm-aml-hi-medium | A | C | per_tenant | +0.1718 | [+0.1277, +0.2156] |
| ibm-aml-li-medium | A | B | global | +0.0179 | [+0.0065, +0.0457] |
| ibm-aml-li-medium | B | C | global | -0.0042 | [-0.0180, +0.0008] |
| ibm-aml-li-medium | A | C | global | +0.0137 | [+0.0058, +0.0319] |
| ibm-aml-li-medium | A | B | per_tenant | +0.0106 | [+0.0042, +0.0233] |
| ibm-aml-li-medium | B | C | per_tenant | +0.0001 | [-0.0034, +0.0042] |
| ibm-aml-li-medium | A | C | per_tenant | +0.0107 | [+0.0034, +0.0239] |

## Tenant isolation (signed)

| Dataset | Δ B (global - per-tenant) | Δ C | Lost graph lift | Retained share |
|---|---|---|---|---|
| ibm-aml | +0.0066 | +0.0021 | +0.0021 | 0.989 |

For ibm-aml, the Arm-C cost of isolation is +0.0021 PR-AUC.
| ibm-aml-hi-medium | +0.0189 | +0.0055 | +0.0055 | 0.969 |

For ibm-aml-hi-medium, the Arm-C cost of isolation is +0.0055 PR-AUC.
| ibm-aml-li-medium | +0.0073 | +0.0030 | +0.0030 | 0.784 |

For ibm-aml-li-medium, the Arm-C cost of isolation is +0.0030 PR-AUC.

## Curated visual

3 motifs (report sha256 `13f42a924007...`): scatter_gather, intra_tenant_cycle, cross_tenant_cycle.

## Disclosures

- GFP transforms are batch-causal (128-edge batches), not strict row-at-a-time serving parity; the existing anti-skew evidence covers only Arm A's 19 served features.
- Node-induced medium samples omit paths crossing discarded nodes, biasing graph-pattern counts downward on those datasets.
- Paired bootstrap intervals use 200 deterministic stratified replicates over a fixed <=250,000-row holdout subset per dataset.
