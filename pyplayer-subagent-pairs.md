# PyPlayer — план реализации через пары субагентов (DEV + CRITIC)

Оркестратор — главный агент. Субагенты запускаются через `subagent`:
разработчик — `general`, критик — `critic`. Фиксы после ревью отправляются
в ту же сессию разработчика (по `sessionID`). Каждая фаза заканчивается
git-коммитом — diff следующей фазы изолирован.

## Правила цикла пары

1. **DEV** (general): реализует фазу, пишет pytest-тесты для не-UI частей, коммитит.
2. **CRITIC** (critic): ревьюит `git diff HEAD~1`, файлы НЕ меняет, выдаёт
   `[CRITICAL|MAJOR|MINOR] file:line — проблема — фикс` и вердикт `APPROVE` / `FIX_REQUIRED`.
3. Если `FIX_REQUIRED` — находки отправляются в dev-сессию, dev коммитит
   `fix(review): ...`, критик делает короткий ре-чек. Максимум 2 итерации;
   если не сошлись — остановиться и показать результат пользователю.

## Допущения (согласованы с пользователем)

- Статистика пишется в БД только при переключении/конце трека и при выходе
  (aboutToQuit + SIGINT), в процессе проигрывания БД не трогается.
- Темп/тон: три режима — «только темп», «только тон», «оба одновременно»
  («оба» = чистое ресемплирование/вариспид, без артефактов).
- UI-надписи на русском.
- Фаза 6 — опциональна.

---

## Общий контекст (вставляется в начало каждого промпта)

```text
# Project
PyPlayer — desktop music player, Python 3 + PySide6 (QML UI). Repo root:
/home/alex/Рабочий стол/PyPlayer (Linux, path contains non-ASCII — test with it).
Current state: template scaffold only (main.py imports non-existent equipment
modules, qml/Main.qml and data/*.json are empty). Design doc: todo.txt (Russian).

# Fixed design decisions (do NOT change)
- Storage: SQLite (stdlib sqlite3) at data/pyplayer.db, no ORM. JSON = export only.
  Tables: tracks(id, path, fingerprint, title, artist, duration_sec, sample_rate,
  format, volume, trim_start_sec, trim_end_sec, speed, pitch, rate_mode),
  play_stats(track_id, play_count, total_listened_sec, last_played_at),
  playlists(id, name, description, created_at, loop, order_mode, allow_repeats),
  playlist_items(id, playlist_id, position, ref_type 'track'|'playlist', ref_id).
- Track card created lazily on first scan. Fingerprint = size + mtime + hash of
  first 64 KiB (survives renames: match by path, then by fingerprint of orphans).
- Audio: streaming via python-miniaudio callbacks; sample-accurate trims, volume,
  varispeed resampler ("both" mode, artifact-free). Stats accumulate in memory
  (real played seconds, pauses excluded) and flush ONLY on track end/switch and
  app quit (aboutToQuit + SIGINT). Nothing is written to DB during playback.
- rate_mode: 'tempo_only' | 'pitch_only' | 'both' ('both' = pure resampling).
- UI: QML three-column window per todo.txt, labels in Russian.
- Style: PEP 8, type hints, pytest for all non-GUI logic.
- Finish every phase with `git add -A && git commit` (conventional message).
```

## Шаблон промпта критика

```text
# Role
You are reviewing a phase of work in the PyPlayer repo (path:
/home/alex/Рабочий стол/PyPlayer). A developer agent just implemented it and
committed. Do NOT modify any files — report only.

# What to review
- Run `git show HEAD` / `git diff HEAD~1` and read the changed files fully.
- Phase goal: <цель фазы>
- Focus: <фокусные риски фазы — из списка ниже>

# Also always check
- Correctness of threading/locking (audio callback vs GUI thread), resource
  leaks (open files, DB connections, audio device).
- SQL correctness (injection, transactions, foreign keys, migration safety).
- Python/QML boundary: type conversions, missing @Property/@Slot decorators,
  signals emitted from wrong thread.
- Adherence to the fixed design decisions in the project context; flag deviations.
- Edge cases: empty library, missing/corrupt file, unicode paths (the repo path
  itself has non-ASCII), division by zero in DSP, NaN gain.

# Output format
List issues as: [CRITICAL|MAJOR|MINOR] file:line — problem — concrete suggested fix.
End with verdict line: `VERDICT: APPROVE` if no CRITICAL/MAJOR issues,
else `VERDICT: FIX_REQUIRED`.
```

## Шаблон промпта фиксов (в dev-сессию по sessionID)

```text
You received review findings from the critic:
<вставить находки>
Fix all CRITICAL and MAJOR issues (and cheap MINOR ones), re-run pytest,
verify the app still starts, and commit as `fix(review): <phase>`.
Do not refactor beyond the findings.
```

---

## Фаза 0 — Скелет и очистка

**DEV:**
```text
{COMMON}
# Phase 0 — reset & skeleton
Remove all leftover template code (equipment models/services, their imports in main.py).
Create layout: models/, services/{db,audio}, qml/. Rewrite main.py: QGuiApplication +
QQmlApplicationEngine loading qml/Main.qml — a placeholder 3-column window (headers only).
Create requirements.txt (PySide6, python-miniaudio, numpy, scipy, mutagen, pytest),
install into a venv, and services/config.py: loads/creates data/config.json defaults
(music_dir, max_playlist_depth, smart_shuffle_default, shuffle_temperature).
Acceptance: app starts and shows the window; `pytest` runs (may be 0 tests).
```
**CRITIC focus:** остатки шаблона, корректность bootstrapping, дефолты конфига, полнота requirements.

## Фаза 1 — Хранилище и сканер библиотеки

**DEV:**
```text
{COMMON}
# Phase 1 — storage & library scanner
Implement services/db.py (schema v1 above, foreign keys ON, migrations table) and
services/library.py: recursive scan of music dir for .mp3/.flac/.wav; upsert tracks
with mutagen metadata (title, artist, duration, sample_rate, format); lazy card
creation; rename-survival via fingerprint matching for orphaned cards.
Add models/track_model.py (QAbstractListModel) and services/export.py (JSON backup).
Tests (pytest, tmp dirs, tiny generated wav): empty dir, rescan is idempotent,
rename detection does not duplicate rows, corrupt file is skipped with a log.
```
**CRITIC focus:** SQL-инъекции, транзакции, поведение при unicode-путях и пропаже файла между сканами.

## Фаза 2 — Аудио-движок

**DEV:**
```text
{COMMON}
# Phase 2 — audio engine
services/audio/engine.py: streaming playback of decoded PCM via python-miniaudio
(wav/mp3/flac). Play/pause/stop/seek inside the trim window; volume; varispeed
resampler (rate 0.25–4.0, linear interpolation); enforce trim_start/trim_end and
emit 'track_finished' when playback crosses trim_end. Track real played seconds
(pause-aware accumulator). NEVER emit Qt signals directly from the audio callback —
use a thread-safe queue drained by a QObject in the GUI thread.
Tests: generated wav played to end at rate 1.0 and 2.0 — accumulator ≈ duration/rate;
trim end stops playback; pause freezes the accumulator.
```
**CRITIC focus:** гонки callback vs GUI, утечки устройства/файлов, точность аккумулятора, поведение при seek в паузе.

## Фаза 3 — Плейлисты и контроллер очереди

**DEV:**
```text
{COMMON}
# Phase 3 — playlists & playback controller
services/queue.py: flatten recursive playlists (cycle guard via visited set — not
just self-reference; max depth from config); 5 modes: sequential / sequential loop /
random no-repeat / random with repeats / random loop no-repeat (fresh shuffled queue
each pass). services/controller.py: PlaybackController(QObject) — signals
currentTrackChanged, positionChanged, playlistChanged; on 'track_finished' flushes
stats to DB and picks next per mode.
Tests: all 5 modes over fixed seed; A→B→A recursion rejected; depth limit enforced.
```
**CRITIC focus:** чистота queue-логики (без Qt-зависимостей), сходимость режимов, edge: плейлист из 1 трека, пустой плейлист.

## Фаза 4 — QML-интерфейс

**DEV:**
```text
{COMMON}
# Phase 4 — QML UI per todo.txt
Column 1: playlists/tracks tree with name, track count, created date, description.
Column 2: audio files of the selected folder; drag&drop into a playlist (col 1) and
reorder inside a playlist; context menus (rename, delete, properties).
Column 3: prev track (name+playlist) on top; seek bar with play/pause, shuffle and
repeat buttons in the middle; next track below. Search filter via QSortFilterProxyModel.
Wire PlaybackController to QML (no direct engine access from QML).
Acceptance: app runs without QML warnings; dnd works; manual smoke test steps listed.
```
**CRITIC focus:** граница Python/QML (типы, @Slot), сбросы моделей, утечки в делегатах, соответствие todo.txt (пункты 19–26, 31).

## Фаза 5 — Умная рандомизация и статистика

**DEV:**
```text
{COMMON}
# Phase 5 — smart shuffle & statistics
Weighted random via Efraimidis–Spirakis sampling without replacement: weight from
play_count + recency (direct = more listens → higher chance; inverse = fewer listens
→ higher chance), temperature as normalization exponent, recency window from config,
uniform weights on cold start. Global toggle (OFF/direct/inverse) in the header.
Wire stats flush at track end/switch, pause, and quit (aboutToQuit + SIGINT handler).
Playlist panel: total duration, avg/max/min track length, total listened time,
listen share vs whole library.
```
**CRITIC focus:** распределение весов (пограничные случаи, отрицательные/нулевые веса), надёжность flush при аварийном SIGINT, корректность процентов.

## Фаза 6 (опционально) — EQ, фейды, спектр

**DEV:**
```text
{COMMON}
# Phase 6 (optional) — EQ, fades, spectrum
Parametric EQ: chain of biquad filters via scipy.signal.lfilter applied AFTER the
resampler, up to 10 bands, per-track preset stored in the track card.
Fade-in/out per track card + optional crossfade between playlist tracks.
FFT spectrum (numpy) pushed to a QML Canvas at ~30 Hz from the GUI thread.
All three must be individually toggleable and degrade gracefully when off.
```
**CRITIC focus:** стабильность DSP (NaN/клиппинг), стоимость lfilter на буфер в реальном времени, деградация при отключении.

---

## Порядок запуска

Фазы строго последовательно: 0 → 1 → 2 → 3 → 4 → 5 → (6).
Каждая пара: DEV → CRITIC → (фиксы в dev-сессии → ре-чек критика) → коммит → следующая фаза.
Стоп-условие для возврата к пользователю: 2 неудачные итерации фиксов в одной фазе
или желание изменить зафиксированные дизайн-решения.
