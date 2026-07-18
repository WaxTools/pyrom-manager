# Changelog

All notable changes to this project are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- App now opens the default browser automatically to `http://localhost:8000` a moment after startup.
- In-app update notification: a dismissible banner shows when a newer GitHub release is available (checked against `VERSION`, cached 6h, fails silently if offline). No auto-update — see [docs/UPDATING.md](docs/UPDATING.md) for manual update steps.

## [1.0.0] - 2026-07-18

### Added
- Initial public release: ROM File List (hashing), Media Cleaner, DAT Manager (Catalogue / Coverage / ROM Scanner), Duplicates cleaner, Game Manager, Collection Compare, and Utilities tabs.
- SQLite-backed result cache for hash and DAT-verify results.
- Repository documentation (README, LICENSE, CONTRIBUTING, issue/PR templates).
