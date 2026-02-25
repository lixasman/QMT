# Tasks

- [x] Task 1: 新增 StrategyConfig 与 CLI 覆盖
  - [x] 定义 `StrategyConfig` 数据结构与默认值
  - [x] 实现命令行参数解析并覆盖配置字段（adapter、broker、tick interval 等）
  - [x] 定义运行时日志初始化参数（console + file）

- [x] Task 2: 实现 StrategyRunner 初始化与日生命周期
  - [x] 初始化 DataAdapter / TradingAdapter（xt 与 gui 两模式）
  - [x] 初始化 StateManager 并加载/创建 PortfolioState
  - [x] 实例化 EntryFSM / ExitFSM / PositionFSM 并执行 recover_on_startup
  - [x] 实现 `_pre_open()`：同步资产、执行 pending_locked、重置 T0 日字段、watchlist 更新、t0_prepare_day
  - [x] 实现 `_intraday_loop()`：按 tick_interval 执行 `_tick_cycle()`，并检查交易时间
  - [x] 实现 `_post_close()`：on_post_close、滚动窗口更新、日频入场扫描、状态最终保存、GUI 限额重置

- [x] Task 3: 实现 tick_cycle 编排与联动回调
  - [x] 对每个持仓 ETF：数据质量检查、熔断、exit(L1/L2)、t0、加仓评估与执行、highest_high 更新
  - [x] 对每个 PendingEntry：执行 Phase3 确认动作与下单
  - [x] Entry 下单后轮询 confirm_order 并回调 PositionFSM（trial/confirm/failed）
  - [x] Exit 下单后确认成交量并回调 PositionFSM（layer1_clear/layer2_reduce）
  - [x] 对每一步按 ETF 与模块粒度捕获异常并记录日志
  - [x] 在关键状态变更后调用 StateManager.save()

- [x] Task 4: 新增 main.py 入口与运行模式框架
  - [x] 解析 CLI 并构建 StrategyConfig
  - [x] 初始化 logging（固定格式，文件+控制台）
  - [x] 启动 StrategyRunner 并运行 `run_day()`；保留回放模式扩展入口（不实现回放逻辑）

- [x] Task 5: 新增集成测试覆盖关键场景
  - [x] 基于已有 tests 的 FakeData/FakeTrading 模式搭建 Runner 依赖
  - [x] 用例：完整日循环（pre_open→3 tick→post_close）与 state 文件可反序列化
  - [x] 用例：入场→持仓→T0→出场全流程（包含 confirm_order 回调）
  - [x] 用例：数据质量降级跳过所有动作
  - [x] 用例：GUI 操作限额触发 freeze 与盘后重置

- [x] Task 6: 更新 README 使用说明
  - [x] 追加“模块 4：策略交易系统”章节
  - [x] 给出两种适配器模式的启动命令示例与注意事项

# Task Dependencies
- Task 2 depends on Task 1
- Task 3 depends on Task 2
- Task 4 depends on Task 1, Task 2
- Task 5 depends on Task 2, Task 3, Task 4
- Task 6 depends on Task 4
