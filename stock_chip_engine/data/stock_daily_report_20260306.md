# 2026-03-06 ???????????????vs 2026-03-05 / 2026-03-04?

?????
- `output/integration/stock_chip/stock_batch_results_20260306.csv`
- `output/integration/stock_chip/stock_batch_results_20260305.csv`
- `output/integration/stock_chip/stock_batch_results_20260304.csv`

???????????????????????????????
- 8/8 ??????`state_init=prev_state`??????
- ?? 2026-03-05?`profit_ratio` ?? +4.14?`ofi_daily_z` ?? +1.036?`vwap_dev_z` ?? +1.298?????????????? `vs_z<0`????????T???????

A??T?????? T+1???????????????T??????????????????????????? T ??????????T????

## ????????????
- `000625.SZ`?????T?????????T??=T_BUY_OK?PR 11.45 (+6.65) | OFI_z 1.174 (+1.160) | VWAP_z 1.654 (+1.934)?buy_near_support=10.860 / sell_near_resistance=11.130 ; caution=queue_pressure
- `002074.SZ`??????T??=NO_T_BUY?PR 6.31 (-1.10) | OFI_z 0.965 (+0.675) | VWAP_z -0.039 (+2.961)?avoid_chasing; use_resistance_for_trim if available
- `003040.SZ`??????T??=NO_T_BUY?PR 11.35 (+5.57) | OFI_z 1.594 (+0.836) | VWAP_z 1.818 (+1.548)?avoid_chasing; use_resistance_for_trim if available
- `300771.SZ`??????T??=T_ONLY_SELL_ON_RALLY?PR 17.34 (+8.28) | OFI_z 0.967 (+0.972) | VWAP_z 1.580 (+1.976)?sell_near_resistance=15.210 / buyback_near_support=14.290
- `600201.SH`?????T?????????T??=T_BUY_OK?PR 13.19 (+9.25) | OFI_z 3.000 (+5.412) | VWAP_z 1.444 (+2.678)?buy_near_support=14.900 / sell_near_resistance=15.860
- `600693.SH`??????T??=NO_T_BUY?PR 6.50 (-1.74) | OFI_z 0.609 (-0.036) | VWAP_z 0.029 (-0.135)?avoid_chasing; use_resistance_for_trim if available
- `601933.SH`??????T??=T_ONLY_SELL_ON_RALLY?PR 12.03 (+5.98) | OFI_z 1.308 (+0.628) | VWAP_z 1.358 (+1.446)?sell_near_resistance=4.520 / buyback_near_support=4.130
- `601988.SH`????????????T/??????T??=T_SELL_FIRST?PR 67.87 (+0.27) | OFI_z -0.917 (-1.362) | VWAP_z 0.121 (-2.021)?sell_into_strength / buyback_near_support=5.350

## ?????????? / ????? / ??????
### 000625.SZ
- ???PR 11.45% (d1 +6.65, d2 +8.62)?ASR 0.213 (d1 +0.056, d2 +0.093)
- ???Top3?0306 resistance:11.130(2.08%)?support:10.860(1.03%)?resistance:11.860(0.49%)?0305 resistance:11.130(2.16%)?resistance:10.860(0.88%)?resistance:11.860(0.51%)?0304 resistance:11.130(2.25%)?resistance:10.880(0.66%)?resistance:11.870(0.53%)
- ???OFI_z 1.174 (d1 +1.160, d2 +1.174)?VWAP_z 1.654 (d1 +1.934, d2 +1.654)?Kyle_z NA?RV_z 0.401?vs_z -0.500?queue_pressure -5385?VPIN_rank NA
- ?T?T_BUY_OK?buy_near_support=10.860 / sell_near_resistance=11.130 ; caution=queue_pressure

### 002074.SZ
- ???PR 6.31% (d1 -1.10, d2 +4.95)?ASR 0.309 (d1 -0.046, d2 +0.178)
- ???Top3?0306 resistance:37.290(0.35%)?resistance:40.580(0.12%)?0305 resistance:37.280(0.36%)?resistance:40.580(0.12%)?0304 resistance:37.280(0.37%)?resistance:40.580(0.13%)
- ???OFI_z 0.965 (d1 +0.675, d2 +0.646)?VWAP_z -0.039 (d1 +2.961, d2 +2.961)?Kyle_z -1.311?RV_z -0.355?vs_z -0.760?queue_pressure -35?VPIN_rank NA
- ?T?NO_T_BUY?avoid_chasing; use_resistance_for_trim if available

### 003040.SZ
- ???PR 11.35% (d1 +5.57, d2 +10.67)?ASR 0.438 (d1 +0.094, d2 +0.394)
- ???Top3?0306 resistance:18.150(0.75%)?resistance:19.960(0.21%)?0305 resistance:18.150(0.78%)?resistance:19.960(0.22%)?support:17.380(0.09%)?0304 resistance:18.140(0.83%)?resistance:19.970(0.23%)?resistance:17.330(0.10%)
- ???OFI_z 1.594 (d1 +0.836, d2 +2.317)?VWAP_z 1.818 (d1 +1.548, d2 +2.676)?Kyle_z -0.271?RV_z -0.227?vs_z -0.904?queue_pressure 0?VPIN_rank NA
- ?T?NO_T_BUY?avoid_chasing; use_resistance_for_trim if available

### 300771.SZ
- ???PR 17.34% (d1 +8.28, d2 +11.99)?ASR 0.201 (d1 +0.120, d2 +0.138)
- ???Top3?0306 resistance:15.210(0.84%)?resistance:15.620(0.75%)?support:14.290(0.21%)?0305 resistance:15.210(0.87%)?resistance:15.620(0.78%)?support:14.290(0.22%)?0304 resistance:15.210(0.91%)?resistance:15.620(0.81%)?resistance:14.290(0.24%)
- ???OFI_z 0.967 (d1 +0.972, d2 +0.446)?VWAP_z 1.580 (d1 +1.976, d2 +0.943)?Kyle_z -1.289?RV_z -0.335?vs_z -2.484?queue_pressure -138?VPIN_rank NA
- ?T?T_ONLY_SELL_ON_RALLY?sell_near_resistance=15.210 / buyback_near_support=14.290

### 600201.SH
- ???PR 13.19% (d1 +9.25, d2 +7.78)?ASR 0.247 (d1 +0.124, d2 +0.102)
- ???Top3?0306 resistance:15.860(0.59%)?support:14.900(0.28%)?resistance:17.480(0.14%)?0305 resistance:15.870(0.62%)?resistance:14.940(0.21%)?resistance:17.470(0.15%)?0304 resistance:15.870(0.65%)?resistance:14.990(0.20%)?resistance:17.460(0.16%)
- ???OFI_z 3.000 (d1 +5.412, d2 +3.327)?VWAP_z 1.444 (d1 +2.678, d2 +1.872)?Kyle_z 0.170?RV_z -0.797?vs_z 0.053?queue_pressure -288?VPIN_rank NA
- ?T?T_BUY_OK?buy_near_support=14.900 / sell_near_resistance=15.860

### 600693.SH
- ???PR 6.50% (d1 -1.74, d2 +3.40)?ASR 0.233 (d1 -0.015, d2 +0.097)
- ???Top3?0306 resistance:13.470(0.26%)?resistance:13.160(0.25%)?resistance:15.360(0.23%)?0305 resistance:13.480(0.27%)?resistance:15.360(0.23%)?resistance:17.150(0.11%)?0304 resistance:13.500(0.28%)?resistance:15.350(0.24%)?resistance:17.150(0.11%)
- ???OFI_z 0.609 (d1 -0.036, d2 -0.161)?VWAP_z 0.029 (d1 -0.135, d2 -0.266)?Kyle_z -0.851?RV_z -0.480?vs_z -1.177?queue_pressure 0?VPIN_rank NA
- ?T?NO_T_BUY?avoid_chasing; use_resistance_for_trim if available

### 601933.SH
- ???PR 12.03% (d1 +5.98, d2 +10.62)?ASR 0.165 (d1 +0.031, d2 +0.091)
- ???Top3?0306 resistance:4.520(1.15%)?support:4.130(0.83%)?resistance:4.990(0.46%)?0305 resistance:4.520(1.18%)?resistance:4.130(0.72%)?resistance:4.990(0.47%)?0304 resistance:4.520(1.21%)?resistance:4.170(0.57%)?resistance:4.990(0.48%)
- ???OFI_z 1.308 (d1 +0.628, d2 +1.836)?VWAP_z 1.358 (d1 +1.446, d2 +1.690)?Kyle_z 3.000?RV_z -1.257?vs_z -0.791?queue_pressure 0?VPIN_rank NA
- ?T?T_ONLY_SELL_ON_RALLY?sell_near_resistance=4.520 / buyback_near_support=4.130

### 601988.SH
- ???PR 67.87% (d1 +0.27, d2 +19.51)?ASR 0.671 (d1 +0.010, d2 -0.112)
- ???Top3?0306 support:5.350(4.65%)?0305 support:5.340(4.62%)?0304 support:5.340(4.57%)
- ???OFI_z -0.917 (d1 -1.362, d2 +0.088)?VWAP_z 0.121 (d1 -2.021, d2 -2.262)?Kyle_z 3.000?RV_z -2.030?vs_z -0.039?queue_pressure 0?VPIN_rank 0.455
- ?T?T_SELL_FIRST?sell_into_strength / buyback_near_support=5.350
