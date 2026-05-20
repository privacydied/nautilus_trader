# Family 2 Funding Crowding Reversal — Real Evaluation Run

- **Branch:** feat/edge-miner-offline-discovery-runner
- **Starting SHA:** `ad55c351f03b53b6633bf56a5fa81905e303209a`
- **Run ID:** funding_crowding_reversal_20260518T173814_429686_e7a56c
- **Generated at:** 2026-05-18T17:40:44.100921+00:00
- **Approved window:** 2020-06-29 00:00:00 UTC → 2026-04-30 16:00:00 UTC
- **Primary cost:** 50.0 bps
- **Diagnostic cost:** 6.0 bps (reported separately, never gates)
- **Seed:** 42
- **Null iterations:** 1000
- **FDR alpha:** 0.05, method: BY
- **Data source:** Binance Vision archive (cached; no re-fetch)

## Headline

- **Total cells evaluated:** 60

- **NEEDS_MORE_DATA:** 25
- **NO_NULL_WORTHY_CELLS:** 35

### No cells earned CANDIDATE_FOR_LONGER_OBSERVATION

## Full 60-Cell Verdict Table

| # | Cell ID | Valid | MeanNet | MedNet | WinRate | WorstD10 | BaselineΔ | Null? | THSameSign? | FDR? | Verdict |
|---|---------|-------|---------|--------|---------|----------|-----------|-------|-------------|------|---------|
| 1 | BTC/abs_funding_ge_5bp/h4/positive_funding_extreme | 272 | -50.1365370078345 | -57.41408335999814 | 0.34191176470588236 | -259.2003834158355 | -40.93472783021069 | ✗ | — | — | NO_NULL_WORTHY_CELLS |
| 2 | BTC/abs_funding_ge_5bp/h4/negative_funding_extreme | 5 | 234.1816961154351 | 230.59117705982845 | 0.6 | -261.63316837888914 | 168.56180667424275 | — | — | — | NEEDS_MORE_DATA |
| 3 | BTC/abs_funding_ge_5bp/h8/positive_funding_extreme | 272 | -57.10980971379752 | -40.50888225148947 | 0.4007352941176471 | -338.81688473513105 | -44.28962813760528 | ✗ | — | — | NO_NULL_WORTHY_CELLS |
| 4 | BTC/abs_funding_ge_5bp/h8/negative_funding_extreme | 5 | 267.75588304191507 | 428.72207066576897 | 0.6 | -218.39628152456217 | 143.58657158727897 | — | — | — | NEEDS_MORE_DATA |
| 5 | BTC/abs_funding_ge_5bp/h12/positive_funding_extreme | 272 | -43.34919741454033 | -46.289564213970635 | 0.4227941176470588 | -404.7768914162955 | -26.110223393952797 | ✗ | — | — | NO_NULL_WORTHY_CELLS |
| 6 | BTC/abs_funding_ge_5bp/h12/negative_funding_extreme | 5 | 295.11935764367377 | 248.16345230521512 | 0.8 | -190.60559736347108 | 134.05278966346134 | — | — | — | NEEDS_MORE_DATA |
| 7 | BTC/abs_funding_ge_5bp/h24/positive_funding_extreme | 272 | -66.61684393225288 | -42.49189859107922 | 0.4485294117647059 | -642.9424988960116 | -34.8470478580212 | ✗ | — | — | NO_NULL_WORTHY_CELLS |
| 8 | BTC/abs_funding_ge_5bp/h24/negative_funding_extreme | 5 | 668.984599070865 | 474.7389702162351 | 1.0 | 339.19299669892325 | 479.19718470566886 | — | — | — | NEEDS_MORE_DATA |
| 9 | BTC/abs_funding_ge_5bp/h48/positive_funding_extreme | 272 | -68.14965817423173 | -88.58257713481447 | 0.4117647058823529 | -803.8717601943584 | -14.467227216868835 | ✗ | — | — | NO_NULL_WORTHY_CELLS |
| 10 | BTC/abs_funding_ge_5bp/h48/negative_funding_extreme | 5 | 398.9622602455492 | 420.5381128059613 | 0.6 | -190.39945849942526 | 8.956621488891415 | — | — | — | NEEDS_MORE_DATA |
| 11 | BTC/abs_funding_ge_10bp/h4/positive_funding_extreme | 81 | -28.86707503693917 | -60.07117725305492 | 0.37037037037037035 | -236.32905223262048 | -6.505799120681534 | — | — | — | NEEDS_MORE_DATA |
| 12 | BTC/abs_funding_ge_10bp/h4/negative_funding_extreme | 2 | -15.520995659530342 | -15.520995659530342 | 0.5 | -261.63316837888914 | -108.20181348034365 | — | — | — | NEEDS_MORE_DATA |
| 13 | BTC/abs_funding_ge_10bp/h8/positive_funding_extreme | 81 | -42.848550114659915 | -18.217577372507204 | 0.4074074074074074 | -296.8750057436296 | -13.26580765722791 | — | — | — | NEEDS_MORE_DATA |
| 14 | BTC/abs_funding_ge_10bp/h8/negative_funding_extreme | 2 | 449.7704731142808 | 449.7704731142808 | 1.0 | 428.72207066576897 | 343.7075782444556 | — | — | — | NEEDS_MORE_DATA |
| 15 | BTC/abs_funding_ge_10bp/h12/positive_funding_extreme | 81 | -27.179175224091093 | -23.28054554755205 | 0.4444444444444444 | -392.14089436589205 | 7.868378202539677 | — | — | — | NEEDS_MORE_DATA |
| 16 | BTC/abs_funding_ge_10bp/h12/negative_funding_extreme | 2 | 309.061370495705 | 309.061370495705 | 1.0 | 248.16345230521512 | 44.72637562587698 | — | — | — | NEEDS_MORE_DATA |
| 17 | BTC/abs_funding_ge_10bp/h24/positive_funding_extreme | 81 | -40.356790680723144 | 24.43959060551947 | 0.5308641975308642 | -667.005013473656 | 14.080024121419804 | — | — | — | NEEDS_MORE_DATA |
| 18 | BTC/abs_funding_ge_10bp/h24/negative_funding_extreme | 2 | 671.6203088452228 | 671.6203088452228 | 1.0 | 339.19299669892325 | 334.84718087319 | — | — | — | NEEDS_MORE_DATA |
| 19 | BTC/abs_funding_ge_10bp/h48/positive_funding_extreme | 81 | -80.22011689447149 | -105.15722842738361 | 0.41975308641975306 | -863.113290034813 | 0.39539579450637063 | — | — | — | NEEDS_MORE_DATA |
| 20 | BTC/abs_funding_ge_10bp/h48/negative_funding_extreme | 2 | 331.2011526825993 | 331.2011526825993 | 0.5 | -8.261949248152831 | -265.68804544932493 | — | — | — | NEEDS_MORE_DATA |
| 21 | BTC/abs_funding_ge_25bp/h4/positive_funding_extreme | 0 | — | — | — | — | — | — | — | — | NEEDS_MORE_DATA |
| 22 | BTC/abs_funding_ge_25bp/h4/negative_funding_extreme | 0 | — | — | — | — | — | — | — | — | NEEDS_MORE_DATA |
| 23 | BTC/abs_funding_ge_25bp/h8/positive_funding_extreme | 0 | — | — | — | — | — | — | — | — | NEEDS_MORE_DATA |
| 24 | BTC/abs_funding_ge_25bp/h8/negative_funding_extreme | 0 | — | — | — | — | — | — | — | — | NEEDS_MORE_DATA |
| 25 | BTC/abs_funding_ge_25bp/h12/positive_funding_extreme | 0 | — | — | — | — | — | — | — | — | NEEDS_MORE_DATA |
| 26 | BTC/abs_funding_ge_25bp/h12/negative_funding_extreme | 0 | — | — | — | — | — | — | — | — | NEEDS_MORE_DATA |
| 27 | BTC/abs_funding_ge_25bp/h24/positive_funding_extreme | 0 | — | — | — | — | — | — | — | — | NEEDS_MORE_DATA |
| 28 | BTC/abs_funding_ge_25bp/h24/negative_funding_extreme | 0 | — | — | — | — | — | — | — | — | NEEDS_MORE_DATA |
| 29 | BTC/abs_funding_ge_25bp/h48/positive_funding_extreme | 0 | — | — | — | — | — | — | — | — | NEEDS_MORE_DATA |
| 30 | BTC/abs_funding_ge_25bp/h48/negative_funding_extreme | 0 | — | — | — | — | — | — | — | — | NEEDS_MORE_DATA |
| 31 | BTC/pct_funding_top_bottom_5pct/h4/positive_funding_extreme | 180 | -46.797569239506196 | -54.9460943059991 | 0.2388888888888889 | -182.36384345253308 | -36.5698492687445 | ✗ | — | — | NO_NULL_WORTHY_CELLS |
| 32 | BTC/pct_funding_top_bottom_5pct/h4/negative_funding_extreme | 633 | -42.1073493861974 | -48.1317932841326 | 0.2843601895734597 | -166.38808684709258 | -47.02430492012532 | ✗ | — | — | NO_NULL_WORTHY_CELLS |
| 33 | BTC/pct_funding_top_bottom_5pct/h8/positive_funding_extreme | 180 | -58.24327524575754 | -51.76321718832894 | 0.26666666666666666 | -264.10311129491413 | -46.9258560356985 | ✗ | — | — | NO_NULL_WORTHY_CELLS |
| 34 | BTC/pct_funding_top_bottom_5pct/h8/negative_funding_extreme | 633 | -33.11975124457187 | -42.95090085876999 | 0.3412322274881517 | -225.62201653937134 | -36.35583767879046 | ✗ | — | — | NO_NULL_WORTHY_CELLS |
| 35 | BTC/pct_funding_top_bottom_5pct/h12/positive_funding_extreme | 180 | -45.85686675647336 | -49.467502643391974 | 0.31666666666666665 | -328.49292344994416 | -36.035666555217816 | ✗ | — | — | NO_NULL_WORTHY_CELLS |
| 36 | BTC/pct_funding_top_bottom_5pct/h12/negative_funding_extreme | 633 | -21.449341027197303 | -44.53171114010775 | 0.3665086887835703 | -254.63815496411183 | -20.596989243841463 | ✗ | — | — | NO_NULL_WORTHY_CELLS |
| 37 | BTC/pct_funding_top_bottom_5pct/h24/positive_funding_extreme | 180 | -85.04071220316138 | -62.4259969587399 | 0.35555555555555557 | -526.9412287576074 | -52.182149321217885 | ✗ | — | — | NO_NULL_WORTHY_CELLS |
| 38 | BTC/pct_funding_top_bottom_5pct/h24/negative_funding_extreme | 633 | 4.421078928879565 | -26.720634252485542 | 0.44075829383886256 | -323.78835008762405 | -9.66608352814527 | ✗ | — | — | NO_NULL_WORTHY_CELLS |
| 39 | BTC/pct_funding_top_bottom_5pct/h48/positive_funding_extreme | 180 | -145.97734191509534 | -104.49755145492898 | 0.3111111111111111 | -786.6788315461272 | -94.55266082265925 | ✗ | — | — | NO_NULL_WORTHY_CELLS |
| 40 | BTC/pct_funding_top_bottom_5pct/h48/negative_funding_extreme | 633 | 33.982602170913836 | -9.506699919686248 | 0.48657187993680884 | -399.35407958086745 | -4.4188230681888 | ✗ | — | — | NO_NULL_WORTHY_CELLS |
| 41 | BTC/pct_funding_top_bottom_2.5pct/h4/positive_funding_extreme | 126 | -46.89357732603575 | -52.8791652041824 | 0.16666666666666666 | -143.9933576066788 | -23.821207157384233 | ✗ | — | — | NO_NULL_WORTHY_CELLS |
| 42 | BTC/pct_funding_top_bottom_2.5pct/h4/negative_funding_extreme | 739 | -44.67397630108042 | -49.59769797031348 | 0.2733423545331529 | -168.7070447098117 | -50.50752606451363 | ✗ | — | — | NO_NULL_WORTHY_CELLS |
| 43 | BTC/pct_funding_top_bottom_2.5pct/h8/positive_funding_extreme | 126 | -62.3131465328366 | -48.64726795867131 | 0.23809523809523808 | -242.7792900941734 | -34.44278594158241 | ✗ | — | — | NO_NULL_WORTHY_CELLS |
| 44 | BTC/pct_funding_top_bottom_2.5pct/h8/negative_funding_extreme | 739 | -30.743290310573745 | -42.53135428982124 | 0.34641407307171856 | -224.21072769626065 | -35.58705055780715 | ✗ | — | — | NO_NULL_WORTHY_CELLS |
| 45 | BTC/pct_funding_top_bottom_2.5pct/h12/positive_funding_extreme | 126 | -60.19161412773605 | -50.43442553908838 | 0.25396825396825395 | -328.49292344994416 | -31.817135833154182 | ✗ | — | — | NO_NULL_WORTHY_CELLS |
| 46 | BTC/pct_funding_top_bottom_2.5pct/h12/negative_funding_extreme | 739 | -19.977365740519463 | -42.19219174020431 | 0.37753721244925575 | -253.39788370659468 | -19.83040622961544 | ✗ | — | — | NO_NULL_WORTHY_CELLS |
| 47 | BTC/pct_funding_top_bottom_2.5pct/h24/positive_funding_extreme | 126 | -81.31244378388718 | -62.4259969587399 | 0.3412698412698413 | -434.27259337092187 | -33.98702731230038 | ✗ | — | — | NO_NULL_WORTHY_CELLS |
| 48 | BTC/pct_funding_top_bottom_2.5pct/h24/negative_funding_extreme | 739 | -1.0909269905170893 | -28.501530380160492 | 0.435723951285521 | -325.06996250638093 | -20.16315371620076 | ✗ | — | — | NO_NULL_WORTHY_CELLS |
| 49 | BTC/pct_funding_top_bottom_2.5pct/h48/positive_funding_extreme | 126 | -144.8852151609059 | -101.08225061794698 | 0.29365079365079366 | -803.8717601943584 | -52.31107507445196 | ✗ | — | — | NO_NULL_WORTHY_CELLS |
| 50 | BTC/pct_funding_top_bottom_2.5pct/h48/negative_funding_extreme | 739 | 29.020748742947365 | -14.83668587419303 | 0.48308525033829497 | -407.747237250327 | -9.387865933235307 | ✗ | — | — | NO_NULL_WORTHY_CELLS |
| 51 | BTC/pct_funding_top_bottom_1pct/h4/positive_funding_extreme | 109 | -44.45476598767235 | -50.95339749223472 | 0.13761467889908258 | -134.38277569817643 | -37.06130330898841 | ✗ | — | — | NO_NULL_WORTHY_CELLS |
| 52 | BTC/pct_funding_top_bottom_1pct/h4/negative_funding_extreme | 810 | -44.450017940411875 | -49.211307030214776 | 0.2679012345679012 | -166.38808684709258 | -50.204091691411406 | ✗ | — | — | NO_NULL_WORTHY_CELLS |
| 53 | BTC/pct_funding_top_bottom_1pct/h8/positive_funding_extreme | 109 | -61.975514617475696 | -51.72079498536196 | 0.21100917431192662 | -218.66467702531904 | -54.21646702227841 | ✗ | — | — | NO_NULL_WORTHY_CELLS |
| 54 | BTC/pct_funding_top_bottom_1pct/h8/negative_funding_extreme | 810 | -32.040814025602636 | -42.594931922301626 | 0.34074074074074073 | -218.39628152456217 | -36.93508136194975 | ✗ | — | — | NO_NULL_WORTHY_CELLS |
| 55 | BTC/pct_funding_top_bottom_1pct/h12/positive_funding_extreme | 109 | -75.030898996666 | -56.76144150923208 | 0.21100917431192662 | -328.80236113487945 | -61.737707133862386 | ✗ | — | — | NO_NULL_WORTHY_CELLS |
| 56 | BTC/pct_funding_top_bottom_1pct/h12/negative_funding_extreme | 810 | -22.691454475769586 | -44.24252944619048 | 0.3691358024691358 | -254.63815496411183 | -23.72851474248219 | ✗ | — | — | NO_NULL_WORTHY_CELLS |
| 57 | BTC/pct_funding_top_bottom_1pct/h24/positive_funding_extreme | 109 | -98.46847274905294 | -73.17352708733786 | 0.3119266055045872 | -504.28722569968306 | -52.41212911658426 | ✗ | — | — | NO_NULL_WORTHY_CELLS |
| 58 | BTC/pct_funding_top_bottom_1pct/h24/negative_funding_extreme | 810 | -0.6314766177446409 | -30.254592229336033 | 0.4345679012345679 | -322.4272789521637 | -21.05452293583447 | ✗ | — | — | NO_NULL_WORTHY_CELLS |
| 59 | BTC/pct_funding_top_bottom_1pct/h48/positive_funding_extreme | 109 | -170.964366895853 | -103.83787448247435 | 0.25688073394495414 | -863.113290034813 | -87.65625748432338 | ✗ | — | — | NO_NULL_WORTHY_CELLS |
| 60 | BTC/pct_funding_top_bottom_1pct/h48/negative_funding_extreme | 810 | 34.60307474038625 | -8.355992871561323 | 0.4901234567901235 | -404.40867682724996 | -10.147307548943836 | ✗ | — | — | NO_NULL_WORTHY_CELLS |

## Diagnostic Sensitivity (6 bps cost)

The following cells would change verdict if evaluated at 6 bps instead of 50 bps:

- BTC/abs_funding_ge_5bp/h4/negative_funding_extreme: optimistic_mean=278.18 bps, actual_verdict=NEEDS_MORE_DATA
- BTC/abs_funding_ge_5bp/h8/negative_funding_extreme: optimistic_mean=311.76 bps, actual_verdict=NEEDS_MORE_DATA
- BTC/abs_funding_ge_5bp/h12/positive_funding_extreme: optimistic_mean=0.65 bps, actual_verdict=NO_NULL_WORTHY_CELLS
- BTC/abs_funding_ge_5bp/h12/negative_funding_extreme: optimistic_mean=339.12 bps, actual_verdict=NEEDS_MORE_DATA
- BTC/abs_funding_ge_5bp/h24/negative_funding_extreme: optimistic_mean=712.98 bps, actual_verdict=NEEDS_MORE_DATA
- BTC/abs_funding_ge_5bp/h48/negative_funding_extreme: optimistic_mean=442.96 bps, actual_verdict=NEEDS_MORE_DATA
- BTC/abs_funding_ge_10bp/h4/positive_funding_extreme: optimistic_mean=15.13 bps, actual_verdict=NEEDS_MORE_DATA
- BTC/abs_funding_ge_10bp/h4/negative_funding_extreme: optimistic_mean=28.48 bps, actual_verdict=NEEDS_MORE_DATA
- BTC/abs_funding_ge_10bp/h8/positive_funding_extreme: optimistic_mean=1.15 bps, actual_verdict=NEEDS_MORE_DATA
- BTC/abs_funding_ge_10bp/h8/negative_funding_extreme: optimistic_mean=493.77 bps, actual_verdict=NEEDS_MORE_DATA
- BTC/abs_funding_ge_10bp/h12/positive_funding_extreme: optimistic_mean=16.82 bps, actual_verdict=NEEDS_MORE_DATA
- BTC/abs_funding_ge_10bp/h12/negative_funding_extreme: optimistic_mean=353.06 bps, actual_verdict=NEEDS_MORE_DATA
- BTC/abs_funding_ge_10bp/h24/positive_funding_extreme: optimistic_mean=3.64 bps, actual_verdict=NEEDS_MORE_DATA
- BTC/abs_funding_ge_10bp/h24/negative_funding_extreme: optimistic_mean=715.62 bps, actual_verdict=NEEDS_MORE_DATA
- BTC/abs_funding_ge_10bp/h48/negative_funding_extreme: optimistic_mean=375.20 bps, actual_verdict=NEEDS_MORE_DATA
- BTC/pct_funding_top_bottom_5pct/h4/negative_funding_extreme: optimistic_mean=1.89 bps, actual_verdict=NO_NULL_WORTHY_CELLS
- BTC/pct_funding_top_bottom_5pct/h8/negative_funding_extreme: optimistic_mean=10.88 bps, actual_verdict=NO_NULL_WORTHY_CELLS
- BTC/pct_funding_top_bottom_5pct/h12/negative_funding_extreme: optimistic_mean=22.55 bps, actual_verdict=NO_NULL_WORTHY_CELLS
- BTC/pct_funding_top_bottom_5pct/h24/negative_funding_extreme: optimistic_mean=48.42 bps, actual_verdict=NO_NULL_WORTHY_CELLS
- BTC/pct_funding_top_bottom_5pct/h48/negative_funding_extreme: optimistic_mean=77.98 bps, actual_verdict=NO_NULL_WORTHY_CELLS
- BTC/pct_funding_top_bottom_2.5pct/h8/negative_funding_extreme: optimistic_mean=13.26 bps, actual_verdict=NO_NULL_WORTHY_CELLS
- BTC/pct_funding_top_bottom_2.5pct/h12/negative_funding_extreme: optimistic_mean=24.02 bps, actual_verdict=NO_NULL_WORTHY_CELLS
- BTC/pct_funding_top_bottom_2.5pct/h24/negative_funding_extreme: optimistic_mean=42.91 bps, actual_verdict=NO_NULL_WORTHY_CELLS
- BTC/pct_funding_top_bottom_2.5pct/h48/negative_funding_extreme: optimistic_mean=73.02 bps, actual_verdict=NO_NULL_WORTHY_CELLS
- BTC/pct_funding_top_bottom_1pct/h8/negative_funding_extreme: optimistic_mean=11.96 bps, actual_verdict=NO_NULL_WORTHY_CELLS
- BTC/pct_funding_top_bottom_1pct/h12/negative_funding_extreme: optimistic_mean=21.31 bps, actual_verdict=NO_NULL_WORTHY_CELLS
- BTC/pct_funding_top_bottom_1pct/h24/negative_funding_extreme: optimistic_mean=43.37 bps, actual_verdict=NO_NULL_WORTHY_CELLS
- BTC/pct_funding_top_bottom_1pct/h48/negative_funding_extreme: optimistic_mean=78.60 bps, actual_verdict=NO_NULL_WORTHY_CELLS

## Verifications

- **BY_FDR_AVAILABLE:** yes
- **TIMESTAMP_SHUFFLE_NULL_AVAILABLE:** yes
- **No re-fetch:** all data loaded from cache; no REST/live/authenticated endpoint
- **No non-cached source:** Binance Vision archive only
- **Run executed once:** one seed (42), no re-window, no re-tune, no rerun
- **Frozen design unchanged:** 60 BTC primary cells, 6 thresholds × 5 horizons × 2 directions
- **Past-only percentile:** honored
- **Event dedup:** one event per funding timestamp per cell
- **Cost model:** 50.0 bps primary (gates promotion); 6.0 bps diagnostic only
- **No private key, order, execution, wallet, or bot path**