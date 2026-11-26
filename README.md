
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
| Colony (巣) | One screen with two sides. **Outside**: the work as sweets that ants eat and carry home. **Inside the nest**: the organization as rooms and tunnels. See below |
| My task / Today | Personal list and a start / pause / done timer that records actual effort |
| Split | Break a task into children in a spreadsheet-like grid (copy, paste, fill down / right) |
| Progress | Work speed and progress-rate charts with Blue / Yellow / Red signals |
| Members / Ranking | Leave, commitment, roles, and contribution views (output, speed, education, teaching time) |

The UI is primarily Japanese; English messages are available (`ui.language: en`).

## The colony: outside and inside the nest

The Colony tab is one game-like screen. Mouse wheel zooms, dragging moves the view, and the nest
entrance takes you underground. There are no separate pages for viewing and editing.

### Outside the nest — the work

- **Areas** are framed plots on the ground: one per top-level task, a rough division of the business.
- **Sweets** are the pieces of work inside an area. Their size says how far the work may be broken down:
  crumb (one task, not split), sugar cube (1 level), wrapped candy (2 levels), chocolate bar (3 levels).
- Started work is being **eaten** by ants; finished work is **carried home** and the sweet loses that piece.
  Ants walk between the nest and their sweets, and paths used by more ants get worn wider.
- Zooming in reveals details in leader-line callouts and each ant's team emblems above its head.
- The president can write **policy in chalk** on the ground. It is only a reminder everyone sees —
  like a company motto on the wall — and has no effect on anything else.
- **Place work** by grabbing a sweet from the hand tray at the bottom and dropping it into an area.
- **Assign work** by grabbing an ant and dropping it on a sweet.

### Inside the nest — the organization

The nest is a cross-section of tunnels and rooms. **A room is a team**; tunnels branch from the
president's room down to departments and sections, so the org chart reads as a tree, however deep.

- Click a room and its members walk through the tunnels into it. People can belong to several teams.
- Drag an ant into a room to add it to that team; drag it out onto the soil to remove it.
- Click an ant for its status card: role, teams, and how much food it brought home this month,
  with a six-month chart. Click a room for the team total and the **per-person** figure, so a big team
  that delivers little is visible too.
- The **storage room** holds everything the company brought home; the **nursery** shows teaching time —
  growing the next generation is core work of a colony.
- Distant rooms close their doors and show only the head count.

Figures use the same rules as the Ranking view (output = estimates of completed work) and the same
publication rule: others' numbers are shown only for the upper half; you always see your own, and the
president sees everyone's.

### Roles

| Role | Look | Biggest sweet | Can also |
|---|---|---|---|
| President (社長) | cigar | chocolate bar | write chalk policy, edit every room, set anyone's role |
| Manager (課長) | necktie | wrapped candy | create areas; dig rooms and decide members below the room they lead; appoint chiefs |
| Chief (係長) | yellow helmet | sugar cube | decide who does what (assign anyone) |
| Member (一般) | white helmet | crumb | take unstarted work for themselves |

A room's leader is a manager or a chief. Permissions are checked on the server.

## Install on Windows (no Python or Node needed)

Download `qolony_windows_*.zip` from [Releases](https://github.com/openceo2025/qolony/releases),
extract it, and run `update_qolony_app.ps1` in it. The app goes to `C:\qolony`; the database and your
settings stay in `C:\QOLONY_DATA`, so later updates never overwrite them. Details (Japanese) are in
`00_README_WINDOWS.md` inside the zip. To build the zip yourself:
`pwsh -File scripts/build_windows_bundle.ps1 -RuntimeEnv <conda env> -ZipPath qolony_windows.zip`.

## Quick start (from source)

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

Useful environment variables: `QOLONY_DB_PATH` (SQLite file), `QOLONY_SETTINGS_PATH` (settings file
kept outside the app folder), `QOLONY_RELOAD=1` (auto reload), `QOLONY_SKIP_FRONTEND_BUILD=1`.

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
