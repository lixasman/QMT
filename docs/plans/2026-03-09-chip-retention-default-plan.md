# Chip Retention Default Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将 `etf_chip_engine.daily_batch` 的默认保留周期从 730 个自然日改为 1095 个自然日，并同步文档说明。

**Architecture:** 保持现有按自然日清理的实现不变，只调整默认常量与面向用户的说明。通过现有单测覆盖 CLI 默认参数与内部默认 retention 值，避免改动算法路径。

**Tech Stack:** Python、pytest、PowerShell

---

### Task 1: 更新默认值测试

**Files:**
- Modify: `tests/test_etf_daily_batch_date.py`
- Modify: `tests/test_etf_data_retention.py`

**Step 1: Write the failing test**

- 将默认值相关断言从 `730` 改为 `1095`

**Step 2: Run test to verify it fails**

Run:

```powershell
.\qmt_env\Scripts\python.exe -m pytest tests/test_etf_daily_batch_date.py tests/test_etf_data_retention.py -q
```

Expected: 至少 2 个断言失败，实际值仍为 `730`

### Task 2: 修改默认值实现

**Files:**
- Modify: `etf_chip_engine/daily_batch.py`

**Step 1: Write minimal implementation**

- 将 `DEFAULT_RETENTION_DAYS` 改为 `1095`

**Step 2: Run test to verify it passes**

Run:

```powershell
.\qmt_env\Scripts\python.exe -m pytest tests/test_etf_daily_batch_date.py tests/test_etf_data_retention.py -q
```

Expected: 全部通过

### Task 3: 同步 README

**Files:**
- Modify: `etf_chip_engine/README.md`

**Step 1: Update docs**

- 将默认保留天数说明改为 `1095`
- 将自定义示例保留，但不改变命令格式

**Step 2: Re-run verification**

Run:

```powershell
.\qmt_env\Scripts\python.exe -m pytest tests/test_etf_daily_batch_date.py tests/test_etf_data_retention.py -q
```

Expected: 全部通过，文档与代码默认值一致
