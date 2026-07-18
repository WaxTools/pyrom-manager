# Contributing

This started as a personal tool for managing a home ROM collection, so there's no formal process — but contributions are welcome.

## Reporting bugs / requesting features

Open an [issue](../../issues) with:
- What you did, what you expected, what happened instead
- Your OS and Python version
- Any relevant error output from the terminal running the app

## Submitting changes

1. Fork the repo and create a branch off `master`.
2. Make your change. Keep the diff focused — unrelated formatting/refactor changes make review harder.
3. Test manually against a real (or disposable/sample) ROM folder — there's no automated test suite yet, so exercising the actual feature you changed in the browser is the only way to catch regressions.
4. Open a pull request describing what changed and why.

## Code style

- No enforced linter/formatter yet; match the existing style in the file you're editing.
- Avoid adding new dependencies unless necessary — this is meant to stay a simple, self-hosted single-file-backend app.

## A note on the "delete" features

Several features in this app permanently delete or rename files. If you're touching that code path, be extra careful with edge cases (symlinks, permission errors, partial failures mid-batch) and call them out explicitly in your PR description.
