# List available recipes.
default:
    @just --list

# Install locked dependencies for Python 3.14.
setup:
    uv sync --locked --python 3.14
    @if [ ! -e .env ]; then cp -n .env.example .env; fi

# Run the Telegram bot with configuration from .env.
run:
    uv run --locked --env-file .env audiotext

# Run the repository checks used in CI.
check:
    uvx prek@0.3.11 run --all-files

# Run the local Whisper integration test without contacting Telegram.
test:
    uv run --locked python -m unittest discover -s tests -v
