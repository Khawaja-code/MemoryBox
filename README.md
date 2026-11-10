# MemoryBox

Records and replays **your own** mouse and keyboard actions on **your own** machine, openly and with consent.
Now with an **AI layer** that understands your recordings, learns your habits and takes plain-English commands. It works fully offline, and can optionally use Claude (see *AI features* below).

Rules that apply in every module:
- Data is stored locally (SQLite, `db/memorybox.db`). Nothing is uploaded.
- You are told what is being recorded (the recorder and dashboard both show a notice).
- Keys typed into password fields are never recorded or replayed (`capture/safety.py`; it fails closed).
- Remote access needs a token and an enabled permission, and every attempt is logged.
- **Emergency stop during playback:** move the mouse to the top-left corner of the screen.

## Run it in VS Code (Windows, Python 3.12)

1. Install **Python 3.12** from python.org (tick *Add python.exe to PATH*) and **VS Code** with the *Python* extension.
2. In VS Code: **File > Open Folder** and choose the `memorybox` folder.
3. Open a terminal (**Terminal > New Terminal**) and run ` .\setup.bat ` from the project folder. This creates `venv`, installs `requirements.txt`, creates the database and runs a setup check. If VS Code opened a PowerShell terminal, the `.bat` file should still run directly; if it does not, switch the terminal profile to **Command Prompt** and try again.
4. Press **Ctrl+Shift+P**, choose **Python: Select Interpreter**, and pick `.\venv\Scripts\python.exe`.
5. Check the setup: **Run and Debug (Ctrl+Shift+D)** > *MemoryBox: doctor* > press F5.
   Everything important should say `OK`. If `uiautomation` is missing, keyboard keys are not recorded (mouse still is).

## Using it

Run commands from the project root (the folder containing `cli.py`). In the VS Code terminal, use the virtual-environment Python explicitly if interpreter selection is not working: ` .\venv\Scripts\python.exe cli.py doctor `.

| Goal | Run and Debug entry | Or in the terminal |
|---|---|---|
| Record | *MemoryBox: record* | `python cli.py record "My routine"` (Enter stops) |
| List | *MemoryBox: list recordings* | `python cli.py list` |
| Play | *MemoryBox: play recording* | `python cli.py play 1 --speed 1.5 --loops 2` |
| Find repeated routines | *MemoryBox: mine routines* | `python cli.py mine --min-support 3` |
| Dashboard + API | *MemoryBox: API + dashboard* | `python cli.py serve` then open http://127.0.0.1:8765 |
| Tests | Testing panel (beaker icon) or task *MemoryBox: run tests* | `pytest` |

AI commands (offline, no key needed): `describe 1`, `when "every weekday at 9am"`, `suggest`, `ask "play morning report twice"`.

Other commands: `rename`, `move`, `duplicate`, `delete`, `save-routine`, `report` (see `python cli.py --help`).

### A good first test
1. `python cli.py record "Open notepad"`, then open Notepad, type a sentence, press Enter in the terminal to stop.
2. `python cli.py play 1`. You have 3 seconds to get ready, then it replays.
3. Record the same small task 3 or more times, then run `python cli.py mine --min-support 3` to see it detected as a routine.

## AI features

All of these run **locally with no account and no extra libraries**. Open the dashboard to use them.

| Feature | What it does | Where |
|---|---|---|
| **Plain-English scheduling** | "every weekday at 9:30am", "monthly on the 15th", "every 2 hours" become a validated schedule with a preview of the next runs. Refuses one-offs and anything more often than every 5 minutes. | Schedules page, `ai/nlschedule.py` |
| **Recording summaries** | Plain-English step list, suggested name, stats and honest advice (for example that clicks depend on window position). | Recording > Details, `ai/describe.py` |
| **Habit detection** | If you play a recording around the same time on the same days, it proposes a schedule with a confidence score. Needs real evidence (at least 3 runs on the same weekday, or 5 across weekdays). | Overview > Suggested for you, `ai/habits.py` |
| **Next-action prediction** | A variable-order Markov model learns your action sequences, predicts what you usually do next and scores how routine a recording is (perplexity). | Recording > Details, `ai/predict.py` |
| **Similarity and duplicates** | TF-IDF cosine similarity over action n-grams finds near-identical recordings. | Overview, `ai/similarity.py` |
| **Copilot** | Chat box: "play invoice export twice at 1.5x", "schedule morning report every monday at 8", "what should I automate?". It only **proposes** actions; you press Confirm. | "Ask Copilot" button, `ai/assistant.py` |

### Optional: connect Claude
Settings > AI. Paste an API key (or set `ANTHROPIC_API_KEY`), tick **Use cloud AI**, save. It is **off by default** and a key alone is not consent. The model then helps in two places: understanding free-form Copilot requests the rules cannot, and polishing recording names.

Privacy and safety design (worth explaining in your report):
- Only step labels (e.g. "Left-click"), counts, durations and recording names are sent. **Typed characters, key names and screen coordinates are never sent** (there is a test for this).
- The model never executes anything. Its output is validated (ids must exist, speed 0.1-5, loops 1-50, schedules re-checked by the same validator) and becomes a proposal that the user confirms. Recording names are treated as data, not instructions.
- Uses the standard library only (`urllib`), so there is nothing extra to install. Default model `claude-haiku-5-5`; change it in Settings or with `MEMORYBOX_LLM_MODEL`.
- The key is stored in the local SQLite database (`setting` table) and is never returned by the API (only its last 4 characters). Prefer the environment variable on shared machines.

New API routes (all under `/api/ai/`): `status`, `settings`, `overview`, `recordings/{id}`, `recordings/{id}/polish`, `parse-schedule`, `ask`, `execute`; plus `GET /api/recordings/{id}/timeline` (events without typed characters, used for the timeline strips). State-changing routes need the `X-MemoryBox-Local` header like the rest of the local API.

### How to describe the AI in your FYP report
Rule-based NLP (intent and slot extraction, temporal expression parsing), statistical sequence modelling (Markov chain with back-off, perplexity), unsupervised pattern mining (PrefixSpan, already in the project), information retrieval (TF-IDF and cosine similarity), temporal clustering for habit detection, and an optional LLM used behind a validation layer (human-in-the-loop). Tests for all of it are in `tests/test_ai.py`.

## Folder layout (matches the guide)
`capture/` input capture and safety rule (Module 1) · `devices/` device monitor (2) · `db/` schema and data layer ·
`playback/` player and runner (4) · `scheduler/` APScheduler wrapper (5) · `intelligence/` pattern mining (6) ·
`ai/` AI layer (new) · `api/` FastAPI (8) · `dashboard/` web dashboard (7) · `reports/` run report and logs (9) · `tests/` pytest suite · `docs/` notes.

## Known limits
- The dashboard is a single self-contained HTML file (no build step, no internet needed). The guide names React; this page calls the same API, so a React app can replace it later.
- The AI layer was tested with fakes and a stand-in for FastAPI where the library could not be installed. Run `pytest` and click through the dashboard on your machine before your demo.
- Habit suggestions are only as good as your history: they need several manual runs at consistent times.
- Clicks on the dashboard while recording are recorded too (the final Stop click is removed automatically).
- On Windows, Ctrl+letter shortcuts can arrive as control characters; test your shortcuts after recording.
- The password-field check, the device monitor and the API/scheduler were written without being able to run them on Windows. Test them on your machine.
