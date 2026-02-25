## 需求边界
- 在新文件夹内独立实现“行业/主题ETF筹码分布计算引擎”，暂不接入现有 finintel/newsget。
- 数据获取严格走 XtQuant（xtquant.xtdata），算法以《ETF筹码分布计算_执行策略_优化后.md》为准。

## 已确认关键口径
- ETF清单：`xtdata.download_etf_info()` + `xtdata.get_etf_info()` 获取全量ETF元信息，再按名称关键词排除固收/QDII/商品/海外等。
- L1快照：period='tick'，`amount/volume` 为日内累计 → 差分得到窗口增量。
- 成交量单位：你已确认 `tick.volume` 单位为“股/份”（不做手→股转换）。
- total_shares：`xtdata.get_instrument_detail(etf_code, False)['TotalVolume']`（缺失回退 FloatVolume）。

## 核心模块（按优化版）
- Module A IOPV：使用 `xtdata.get_etf_info(etf_code)` 返回的 `reportUnit/cashBalance/stocks.componentVolume/navPerCU/nav` 计算 IOPV 与 premium_rate。
- Module B MaxEnt：三约束（归一化 + VWAP + γ），并将价格网格归一化到 [0,1] 使 γ 生效；γ 由 premium_rate 驱动（k_gamma、gamma_max）。
- Module C/D/E/F/G：按优化版实现（换手衰减、申赎修正、日终扩散、指标输出、主控编排）。

## 依赖处理
- 直接使用 SciPy：`scipy.ndimage.gaussian_filter1d`、`scipy.signal.find_peaks`。
- 更新 [requirements.txt](file:///d:/Quantitative_Trading/requirements.txt) 增加 numpy/pandas/scipy（声明依赖；实际安装在你后续执行 pip/或你的QMT环境中完成）。

## 新建目录与文件
- 新建 `d:\Quantitative_Trading\etf_chip_engine\`
  - config.py
  - models\chip_distribution.py
  - modules\iopv_calculator.py
  - modules\maxent_solver.py
  - modules\turnover_model.py
  - modules\redemption.py
  - modules\diffusion.py
  - modules\indicators.py
  - data\xtdata_provider.py（ETF清单/名称、tick下载与读取、日线获取、ATR计算、份额读取）
  - data\tick_adapter.py（tick ndarray→窗口快照 DataFrame：差分、异常重置）
  - engine.py（ETFChipEngine）
  - service.py（单入口：给定交易日批量计算行业ETF筹码与指标；返回结构化结果，先不改现有 output）

## 单元测试（不依赖真实XtQuant）
- 新增 `tests/test_etf_chip_engine.py`（unittest）：
  - IOPVCalculator：按 reportUnit/cashBalance/componentVolume 的样例校验。
  - MaxEntSolver：γ=0/γ≠0 的收敛与均值≈VWAP。
  - tick_adapter：伪造“累计 amount/volume”序列验证差分。
  - diffusion：扩散前后总量守恒。

## 兼容性策略（避免XtQuant版本差异导致不可用）
- 对 `xtdata.get_etf_info()`（无参全量）与 `xtdata.get_etf_info(etf_code)`（单只申赎篮子）做 try/except 兼容：优先无参获取ETF列表；不支持时退化为从板块/或你传入ETF列表（不改变默认路径）。

确认后我将按上述结构创建新目录与代码，并补齐 requirements 与单测。