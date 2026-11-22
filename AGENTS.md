# Repository Guidelines

## Project Structure

This repository is a small Python 3.14 Telegram bot. Application code lives in `src/audiotext/`: `bot.py` handles Telegram updates, downloads, and replies; `transcription.py` loads the local Whisper model and transcribes audio. `README.md` describes setup and runtime configuration, `.env.example` lists supported environment variables, and `.github/workflows/prek.yml` runs checks in CI. There is currently no test directory.

## Build, Run, and Check

- `uv sync --locked --python 3.14` installs locked project and development dependencies.
- Copy `.env.example` to `.env`, set `BOT_TOKEN`, then run `uv run python -m audiotext.bot` (or `uv run audiotext`) to start polling Telegram.
- `uv run --locked prek run --all-files` runs the repository checks locally. CI runs the same checks with `uvx prek`.
- `uv run --locked ruff check src` runs lint and pyupgrade checks; `uv run --locked ty check src` checks types.

## Coding Style

Use four spaces for indentation, type annotations for functions and meaningful values, and descriptive `snake_case` names. Keep Telegram handling in `bot.py` and model-specific work in `transcription.py`. Follow Ruff, including its enabled `UP` rules, and resolve `ty` findings before submitting changes. Do not commit `.env`, bot tokens, model files, or downloaded audio.

## Testing

No automated test suite is configured yet. For changes, run `uv run --locked prek run --all-files` and manually check affected bot behavior when practical. If adding tests, place them in `tests/` with `test_*.py` names and document the chosen runner and command in the README.

## Commits and Pull Requests

The existing history uses short conventional prefixes such as `feat:`. Keep commit subjects concise and action-oriented (for example, `fix: remove temporary audio after errors`). Pull requests should explain the behavior change, note configuration or deployment impact, and report the checks run. Include a linked issue when one exists; screenshots are unnecessary for this command-line bot.

## Configuration and Agent Notes

Keep credentials in the ignored `.env` file and update `.env.example` and the README when adding configuration. When interacting with Cloudflare, use the `cf` CLI unless this project has a Wrangler configuration file.
