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
