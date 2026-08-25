# Dictionary Audio Background Track

The hub serves an optional background track (`The Nethack Dictionary`) at `/dictionary.mp3`. This is a 19 MB asset that is shipped separately from the codebase and never committed to git.

## Deployment

The track is placed by the deployer (e.g., into the `hubdata:/data` volume as `/data/dictionary.mp3`).
Set the environment variable:

```bash
NETHACKERS_DICT_AUDIO=/data/dictionary.mp3
```

## Runtime behavior

- When the file exists at the configured path, the route serves it as `audio/mpeg`.
- When the file is absent, the route returns 404 (silent static frame on the frontend).
- This allows dev/CI/fresh clones to run without the 19 MB asset by default.

## Local development

To use a local dictionary file during development, set `NETHACKERS_DICT_AUDIO` to point at your audio file:

```bash
NETHACKERS_DICT_AUDIO=./path/to/dictionary.mp3 uv run uvicorn nethackers.hub.api:create_default_app --reload
```

Or leave it unset — the hub will gracefully degrade to a silent frame.

## Preview harness (dev only)

The `scripts/dict_viz_preview.mjs` script is an offline, manual visual checker that uses
Playwright to screenshot the dungeon visualization at different audio volumes. It is
**not part of the pytest CI suite** — it is a developer-only tool for visually inspecting
the reactive appearance of the visualization without real audio input.

### Setup

Install Playwright and the Chromium browser:

```bash
npm i -D playwright
npx playwright install chromium
```

### Usage

With the hub running locally (`uv run uvicorn nethackers.hub.api:create_default_app --reload`),
run the preview harness:

```bash
node scripts/dict_viz_preview.mjs --out <output-directory>
```

- `--out <output-directory>`: directory where PNG screenshots will be saved.
  If not specified, defaults to `./viz-shots`.

The harness will load `http://127.0.0.1:8000/?vizpreview=<volume>` at volumes:
- `0` (idle, no animation)
- `0.35` (moderate animation)
- `0.9` (full animation)
- `0.9` scrolled to 1200px (full animation with scroll)

Each screenshot is saved with a numeric prefix (e.g. `1-idle.png`, `2-vol35.png`).
Any console errors encountered during rendering are logged to stdout.

### Important: Not CI

This harness is a **manual dev check only**. The pytest suite does not run this script;
CI validates the visualization route behavior via unit tests in `tests/`, not via visual
inspection. Use this tool locally to eyeball the reactive appearance during development.
