
<img width="1024" height="1024" alt="QOLONY concept image" src="https://github.com/user-attachments/assets/7afbb5c8-2c14-4095-a7bf-e8f38dbbd648" />

# QOLONY
**A collective-intelligence project management system inspired by ant colonies**

[日本語](README.ja.md)

QOLONY is built on one core belief:

> People are not resources to be controlled.
> A team is a living system that self-organizes through shared rules, mutual visibility, and trust.

Like an ant colony, each person acts locally and autonomously, yet the group can move like one organism—finding better routes, adapting continuously, splitting work naturally, and delivering outcomes together.
No exploitation. No rigid hierarchy of domination.
QOLONY is an operating model for collective intelligence in real work.

---

## Philosophy

### 1) Decentralized collaboration over centralized control
QOLONY values local judgment, peer coordination, and adaptive execution.
It is designed for teams that want to move from command-and-control toward self-organizing flow.

### 2) Progress as a shared early-warning language
QOLONY uses **work-speed-based signals** to make momentum and risk visible early.
The goal is not punishment. The goal is earlier support, better decisions, and healthier delivery.

### 3) Education is a core function of the colony
In natural colonies, nurturing the next generation is not peripheral work—it is core work.
QOLONY treats education the same way: not as a side activity, not as an afterthought, but as a structural center of team sustainability and long-term performance.

### 4) Collective intelligence requires transparent feedback loops
When ownership, progress, contribution, and learning are visible, teams can self-correct faster.
QOLONY is designed to strengthen these feedback loops so the whole system improves continuously.

### 5) Competition is contextual, contribution is central
QOLONY includes ranking views, but its deeper intent is not individual rivalry.
Its intent is to surface contribution patterns, strengthen cooperation, and help the colony perform as a whole.

---

## Why QOLONY exists

Many teams fail not because individuals lack effort, but because the system produces:
- unclear responsibility,
- delayed feedback,
- hidden overload,
- undervalued learning and mentorship.

QOLONY exists to redesign those conditions.
It helps teams notice earlier, adapt earlier, and deliver sustainably—together.

---

## What it does

QOLONY is a task, effort, progress and feedback tool for a team on one local network.
One person per task; work done by several people is expressed by splitting the task.

| View | Purpose |
|---|---|
| WBS / Gantt | Task tree, schedule, dependencies |
| Colony (巣の外) | The whole tree as sweets that ants eat and carry home — started work is being eaten, finished work is carried away |
| My task / Today | Personal list and a start / pause / done timer that records actual effort |
| Split | Break a task into children in a spreadsheet-like grid (copy, paste, fill down / right) |
| Progress | Work speed and progress-rate charts with Blue / Yellow / Red signals |
| Members / Ranking | Leave, commitment, roles, and contribution views (output, speed, education, teaching time) |

The UI is primarily Japanese; English messages are available (`ui.language: en`).

## Quick start

Requirements: Python 3.12, Node.js 20+.

```bash
# backend dependencies
cd backend
python -m pip install -r requirements.txt
cd ..

# builds the frontend if needed, then starts the server on http://localhost:8000/
./scripts/run_backend.sh          # macOS / Linux
pwsh -File scripts/run_backend.ps1  # Windows
```

On the first start with an empty database, demo tasks are created for the fictional members in
`backend/config/settings.yaml`. Edit `users.members` to match your team.
The admin tab asks for the top manager's password (`change-me` by default) — change it.

Useful environment variables: `QOLONY_DB_PATH` (SQLite file), `QOLONY_RELOAD=1` (auto reload),
`QOLONY_SKIP_FRONTEND_BUILD=1`.

### Development

```bash
cd backend && python -m pytest tests -q
cd frontend && npm install && npm run check && npm test
cd frontend && npx vite     # dev server; /api is proxied to :8000
```

`frontend/field-lab.html` (dev server only) shows the Colony view with generated fake data:
`http://localhost:5173/field-lab.html?n=11000&done=0.72`.

## Architecture

- Backend: FastAPI + SQLAlchemy + Alembic, a single SQLite file (`backend/app/`)
  - domain rules are pure functions in `backend/app/tms_core/domain/`
  - database access stays in the repository layer
- Frontend: React + Vite SPA (`frontend/src/`), talks to the backend only through `/api`

See [AGENTS.md](AGENTS.md) for the product rules the code follows.

## Optional: iQUAVIS integration

QOLONY can sync tasks two-way with the iQUAVIS PLM system through an Excel macro workbook.
It is **off by default** (`integrations.iquavis.enabled: false`) and the rest of QOLONY works without it.
The workbook and vendor documents are not part of this repository.

## Security notes

QOLONY is meant for a trusted local network. The password above is a simple gate for the admin
tab, not authentication for the API. Do not expose QOLONY directly to the internet.

## License

[MIT](LICENSE)
