# AGENTS Override Guidelines (Quant Project: Ultimate Fast & Direct Mode)

> **CRITICAL OVERRIDE:** This file explicitly overrides the global `AGENTS.md`. For this specific quantitative trading project, absolute efficiency, direct single-agent execution, and zero-friction development are mandatory.

## 0) ULTIMATE OVERRIDE (USER SUPREMACY)
- **THE GOLDEN RULE:** If the user's explicit in-chat prompt conflicts with ANY rule in this document or the global `AGENTS.md` (e.g., the user explicitly asks to spawn a sub-agent, generate a `/plan`, or use Git branches), **the user's prompt ALWAYS takes absolute precedence.**
- You must instantly suspend the conflicting rule and execute the user's direct command without hesitation or arguing.

## 1) Bypass ALL Stage-Gated Workflows (Overrides Section 5, /spec, /plan, /do)
- **NO PLANNING PHASE:** Completely ignore the `/spec -> /plan -> /do` workflow. 
- **NO DOCS:** Do not generate specification documents, plans, or research files under `specs/`, `plans/`, or `docs/research/` unless explicitly requested.
- **Direct Action:** When given a task, immediately write the code or modify the target file. You are authorized to make source code changes directly from the first prompt.

## 2) Bypass Dependency Approvals (Overrides Section 2)
- **Direct Dependency Management:** If a new package (e.g., Python `pip install`, Node `npm install`) is needed for the script to run, you are authorized to provide the exact command and assume the user will run it. 
- You do NOT need explicit permission to suggest modifications to `requirements.txt` or similar files.

## 3) No Version Control Shenanigans (Overrides General Behavior)
- **NO GIT BRANCHING OR WORKTREES:** Modify files directly in the current working directory. 
- **FORBIDDEN:** DO NOT use `git branch`, `git checkout -b`, or `git worktree` to isolate changes. 

## 4) Pre-Checks & Validation Relaxed (Overrides Section 1 & 7)
- **Assume Environment is Ready:** Skip `Get-Command` checks for basic tools like Python, Git, or Node. Assume they exist.
- **Direct Execution over Exhaustive Validation:** Output the functional code immediately. Do not stall the workflow trying to run exhaustive test suites unless the user asks you to verify the code.

## 5) Minimalist Output (Overrides Section 8)
- **Zero Fluff:** Do not explain your reasoning, do not lecture about safety rules, and do not narrate your process.
- Strictly output one concise sentence explaining the change, followed immediately by the code block or PowerShell command.