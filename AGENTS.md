# AGENTS.md

## Project
- Name: `AnkiGen`
- Type: PySide6 desktop app for AI-assisted Anki vocabulary generation and synchronization.
- Entry: `main.py`
- Python: 3.10+

## Core Architecture
- `gui/`: UI layer
  - `main_window.py`: main window, workers, progress/log UI, Anki sync workflow
  - `word_editor.py`: editor and audio controls
  - `settings_dialog.py`: runtime settings dialog
- `services/`: external integrations
  - `gpt_generator.py`: metadata generation and metadata repair
  - `tts_generator.py`: word/sentence audio generation
  - `anki_api.py`: AnkiConnect sync, media upload, batch create/update/delete
  - `models.py`: centralized AI model names, do not hardcode model strings elsewhere
- `utils/`
  - `file_manager.py`: local JSON/audio helpers, batch input parsing
  - `settings_manager.py`: settings persistence
- Data
  - `data/words.json`: local vocabulary store
  - `data/settings.json`: runtime settings
  - `audio/`: generated mp3 files

## Current Behavior That Must Be Preserved
- Batch input is comma-separated, not space-separated.
  - Example: `abandon, take off, in charge of`
  - Reason: phrases must remain intact.
- `example` field format is exactly:
  - first line: English sentence
  - second line: Chinese translation
- Sentence TTS reads only the first English line of `example`.
- `phonetic` and `part_of_speech` are optional in UI editing and should never block saving.
- Missing metadata is recoverable via AI.
- Left word list is sorted by `imported_at` and shows import time.
- Audio playback and list browsing should remain available during long-running sync/generation tasks when the UI is in browse/audio-allowed busy mode.

## Anki Sync Rules
- Sync is full sync, not create-only.
- Sync compares local `words.json` with Anki deck and does:
  - create missing notes
  - update existing notes
  - delete Anki notes not present locally
- Sync must stay a pure sync step.
  - Do not regenerate GPT metadata or TTS audio inside Sync.
  - Generation belongs to `Generate All`.
- Anki optimization already in place:
  - `addNotes` for batch create
  - `multi` + `updateNoteFields` for batch update
  - concurrent media upload
  - remote media check before upload, but changed media must overwrite old Anki media

## GPT Generation Rules
- Metadata generation is batched.
- Batch size is configurable in settings.
- Failed/missing words now use single-word retry logic.
- `services/gpt_generator.py` currently includes:
  - up to 3 retries for failed single entries
  - retry 1 uses standard prompt
  - retry 2-3 use stricter prompt
  - strict prompt requires exact `word` echo and forbids splitting phrases
- If modifying prompt behavior, preserve phrase safety and strict JSON parsing.

## Input / Phrase Handling
- Parsing logic lives in `utils/file_manager.py`.
- Input is split by English comma (also tolerates Chinese comma normalization if still present in code).
- Never switch back to space-separated parsing unless the product requirement changes.

## Settings
- User can configure in UI:
  - API base URL
  - API key
  - model
  - metadata batch size
  - TTS worker count
  - Anki upload worker count
  - Anki deck/model and TTS voice
- Settings are persisted in `data/settings.json`.
- `.env` exists only as local secret input; `.env.example` is committed, `.env` must remain gitignored.

## UI / UX Constraints
- Do not rewrite layout unless explicitly requested.
- Current theme direction is dark, ChatGPT-like neutral gray with restrained accent colors.
- Toolbar should maintain action hierarchy:
  - primary: `Generate All`
  - secondary: `Add Word`, `Sync to Anki`
- Log panel exists and should remain non-intrusive.
- Search box has embedded icon styling.
- Bulk delete exists via multi-selection; search-delete was intentionally removed.

## Known Important Files To Check Before Changes
- `/mnt/c/Users/kjmsd/Documents/GitHub/AnkiGen/gui/main_window.py`
- `/mnt/c/Users/kjmsd/Documents/GitHub/AnkiGen/services/gpt_generator.py`
- `/mnt/c/Users/kjmsd/Documents/GitHub/AnkiGen/services/anki_api.py`
- `/mnt/c/Users/kjmsd/Documents/GitHub/AnkiGen/services/tts_generator.py`
- `/mnt/c/Users/kjmsd/Documents/GitHub/AnkiGen/utils/file_manager.py`

## Safe Working Rules
- Prefer minimal, targeted edits.
- Do not remove phrase support.
- Do not reintroduce hardcoded AI model names outside `services/models.py`.
- Do not make Sync regenerate content.
- Keep UI responsive: long tasks belong in background workers, not main Qt thread.
- After Python edits, run at least `python3 -m py_compile` on modified files.

## If You Resume Work Later
1. Read the user’s latest request first.
2. Check whether it affects generation, sync, or UI separately.
3. Verify current behavior in the files above before patching.
4. Preserve the invariants listed in this file.
