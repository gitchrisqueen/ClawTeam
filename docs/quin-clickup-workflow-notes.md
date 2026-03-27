# Quin ClickUp + ClawTeam Workflow Notes

**Author**: GitHub Copilot CLI  
**Session**: 2026-03-27  
**Codebase root**: `/home/quin/ClawTeam-OpenClaw`  
**OpenClaw runtime**: `/home/quin/.openclaw`  

---

## 1. What Was Discovered

### System State (as of 2026-03-27)

| Component | Status | Details |
|-----------|--------|---------|
| OpenClaw 2026.3.24 | ✅ Running | Gateway up, systemd-enabled, PID auto-managed |
| ClickUp webhook server | ✅ Running | PID 622048, port 4050, `workspaces/quin/clickup_webhook_server_PRODUCTION.py` |
| ClickUp MCP (67 tools) | ✅ Connected | Via mcporter, credentials in `.openclaw/.env` |
| clickup-triage pipeline | ✅ Working | Completed 5/5 tasks on 2026-03-27; two-tier gate verified |
| cqc-feature-dev template | ✅ Exists | 7 agents via ClawTeam, tmux backend |
| ClawTeam templates | ✅ 12 templates | See `clawteam template list` |
| Team Coordinator after-hours | ⚠️ Fixed | Timeout increased from 120s → 300s |
| EOD cron | ⚠️ Monitor | 1 transient Discord delivery error; likely self-resolving |
| Webhook PID files | ✅ Fixed | Stale pid 146986 → updated to 622048 |

### Architecture: Two-Tier Triage Gate

The webhook server (`clickup_webhook_server_PRODUCTION.py`) implements a clean two-tier flow:

```
ClickUp Event → Webhook (port 4050)
  │
  ├── Has "TRIAGE-DONE" tag?
  │     NO  → Route to cu-clickup-triage global team (AI pipeline)
  │               documentor → classifier → prioritizer → router → verifier
  │               triage-lead adds "TRIAGE-DONE" tag + updates priority
  │               Webhook re-fires on tag update
  │
  └── YES → Fast Python classify → WORKFLOW_MAP → dispatch
              Global teams (persistent): clickup-triage, human-escalation
              Ephemeral teams (per-ticket): cqc-feature-dev, security-audit,
                research-brief, content-pipeline, project-delivery
```

### What Was Investigated as Broken (and Actual Status)

**"0/8 ClawTeam workflow completion"** (March 24 log entry):
- Root cause: Test ticket "🧪 Test Ticket — Webhook Processing Validation" matched
  `r"webhook"` in CATEGORY_PATTERNS["infra"] → routed to `cqc-feature-dev`
- The `cqc-feature-dev` pipeline launched a full development team (7 agents) for a test
  ticket with no real codebase → no tasks could complete within 30-minute timeout
- **Not a bug in routing** — behavior is correct for a real feature ticket
- The triage gate (added before March 24) now routes ALL new tickets through AI triage first,
  which would have caught this miscategorization

**Team Coordinator after-hours timeout**:
- The three-phase coordinator (collect → check → spawn) needs ~90s but the timeout was 120s
- On heavy load (many teams to check), exceeded 120s → timeout error
- **Fixed**: Increased timeout to 300s

**Stale webhook PID files**:
- `webhook_final.pid` contained dead PID 146986; actual server PID was 622048
- **Fixed**: Updated both PID files to current PID

**EOD cron "Message failed"**:
- Single Discord delivery failure on 2026-03-26
- `consecutiveErrors: 1` — transient failure
- Protocol and channel config look correct; next run should succeed
- **Monitor**: If `consecutiveErrors > 2`, investigate Discord bot permissions for channel `1466720609471041631`

**ClawHub ClickUp skill**:
- No separate "clickup" skill exists in the openclaw skill registry
- ClickUp integration is via MCP (67 tools, already connected and working)
- The `clawhub` skill (disabled) is the skill marketplace CLI, not ClickUp-specific
- **No action needed**: MCP covers all ClickUp operations

---

## 2. Changes Made

### 2.1 Cron: Team Coordinator Timeout (2026-03-27)

**File**: `/home/quin/.openclaw/cron/jobs.json`  
**Change**: Job `2b80d3f5-bae9-419b-bb80-0e34899e4789` ("Team Coordinator — After Hours")  
`payload.timeoutSeconds`: `120` → `300`  
**Backup**: `/home/quin/openclaw_backups/20260327_213815/cron_jobs.json.bak`

### 2.2 Webhook PID Files (2026-03-27)

**Files**:
- `/home/quin/.openclaw/workspaces/quin/webhook_final.pid`
- `/home/quin/.openclaw/workspaces/quin/webhook.pid`  
**Change**: Updated from stale PID 146986 to current PID 622048

### 2.3 Quin AGENTS.md — Requirement Decomposition SOP (2026-03-27)

**File**: `/home/quin/.openclaw/workspaces/quin/AGENTS.md`  
**Change**: Added two new sections before `<!-- antfarm:workflows -->`:
1. **Requirement Decomposition SOP**: 6-step process for turning fuzzy requests into structured ClickUp tasks (clarify → create parent → decompose → assign → log → confirm)
2. **ClickUp Next-Action Recommendations**: Pattern for surfacing stale task recommendations in morning briefs and EOD deltas  
**Backup**: `/home/quin/openclaw_backups/20260327_213815/AGENTS.md.bak`

### 2.4 acp-clickup Agent AGENTS.md (2026-03-27)

**File**: `/home/quin/.openclaw/agents/acp-clickup/agent/AGENTS.md` *(new file)*  
**Change**: Defined purpose, spawn protocol, responsibilities, and output protocol for the
`acp-clickup` sub-agent (bulk ClickUp operations, spawned via `sessions_spawn`)

### 2.5 WORKFLOW_AUTO.md — Architecture Update (2026-03-27)

**File**: `/home/quin/.openclaw/workspaces/quin/WORKFLOW_AUTO.md`  
**Change**: Complete rewrite to document the two-tier triage gate architecture, all active
workflows, safeguards, monitoring commands, and manual intervention procedures  
**Backup**: `/home/quin/openclaw_backups/20260327_213815/WORKFLOW_AUTO.md.bak`

### 2.6 This Document (2026-03-27)

**File**: `/home/quin/ClawTeam-OpenClaw/docs/quin-clickup-workflow-notes.md` *(new file)*

---

## 3. How to Roll Back

### Rollback: Cron Timeout Change

```bash
BACKUP_DIR=/home/quin/openclaw_backups/20260327_213815
cp "$BACKUP_DIR/cron_jobs.json.bak" /home/quin/.openclaw/cron/jobs.json
# Reload gateway to pick up cron changes:
openclaw gateway restart
```

### Rollback: AGENTS.md

```bash
BACKUP_DIR=/home/quin/openclaw_backups/20260327_213815
cp "$BACKUP_DIR/AGENTS.md.bak" /home/quin/.openclaw/workspaces/quin/AGENTS.md
```

### Rollback: WORKFLOW_AUTO.md

```bash
BACKUP_DIR=/home/quin/openclaw_backups/20260327_213815
cp "$BACKUP_DIR/WORKFLOW_AUTO.md.bak" /home/quin/.openclaw/workspaces/quin/WORKFLOW_AUTO.md
```

### Rollback: acp-clickup Agent

```bash
rm /home/quin/.openclaw/agents/acp-clickup/agent/AGENTS.md
# Directory existed before (sessions/ subdir) — safe to remove just the file
```

### Rollback: Webhook PID Files (if needed)

```bash
# Kill current server and check process table for webhook PID
WEBHOOK_PID=$(ps aux | grep clickup_webhook_server_PRODUCTION | grep -v grep | awk '{print $2}' | head -1)
echo "$WEBHOOK_PID" > /home/quin/.openclaw/workspaces/quin/webhook_final.pid
```

### Rollback: ClawTeam-OpenClaw Repo

```bash
git -C /home/quin/ClawTeam-OpenClaw revert HEAD
# or reset to backup git state:
cat /home/quin/openclaw_backups/20260327_213815/repo_git_state.txt
```

---

## 4. Validation Results

### Tests
- **ClawTeam-OpenClaw test suite**: 142 tests, all passed (2026-03-27)
- Command: `python3.12 -m pytest tests/ -x -v --tb=short` (run with fresh venv)

### Pipeline Health
- `cu-clickup-triage` global team: 5/5 tasks completed (2026-03-27 ~21:00 ET)
- Webhook server: responding `{"status":"healthy"}` on port 4050
- OpenClaw gateway: reachable, 138ms, all agents online

---

## 5. Outstanding Items / Future Improvements

### Monitor
1. **EOD cron Discord delivery** — if `consecutiveErrors > 2`, check bot permissions for channel `1466720609471041631`
2. **cqc-feature-dev completions** — monitor for real feature tickets; the pipeline should complete normally for actual code tasks

### Future Improvements (not implemented in this session)
1. **Enable clawhub skill** (`openclaw skills enable clawhub`) — lets Quin install new skills on demand
2. **Webhook server process supervision** — add systemd service for automatic restart on crash (currently no supervisor)
3. **Morning brief stale task scan** — add `clickup_get_workspace_tasks(statuses=["in progress"], date_updated_lt=48h)` call to morning brief cron
4. **acp-clickup agent registration** — register in `openclaw.json` agents list with proper config block (tools profile, workspace, model)
5. **clickup-triage team heartbeat** — configure a cron to ensure `cu-clickup-triage` global team is always running

---

## 6. Key File Locations

| File | Purpose |
|------|---------|
| `workspaces/quin/clickup_webhook_server_PRODUCTION.py` | Webhook server (ClickUp → ClawTeam dispatch) |
| `workspaces/quin/clickup_routing_config.json` | List ID → Quin lane mapping |
| `workspaces/quin/WORKFLOW_AUTO.md` | Architecture doc (two-tier gate) |
| `workspaces/quin/AGENTS.md` | Quin orchestrator SOPs including decomposition |
| `workspaces/quin/reference/CLICKUP.md` | ClickUp MCP tools reference |
| `agents/acp-clickup/agent/AGENTS.md` | Bulk ClickUp sub-agent definition |
| `.clawteam/templates/clickup-triage.toml` | ClawTeam clickup-triage template |
| `.clawteam/templates/cqc-feature-dev.toml` | ClawTeam feature dev template |
| `cron/jobs.json` | All cron job definitions |
| `openclaw_backups/20260327_213815/` | Backup of all modified files |
