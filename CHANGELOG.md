# Changelog

All notable changes to this project are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- App now opens the default browser automatically to `http://localhost:8000` a moment after startup.
- In-app update notification: a dismissible banner shows when a newer GitHub release is available (checked against `VERSION`, cached 6h, fails silently if offline). No auto-update — see [docs/UPDATING.md](docs/UPDATING.md) for manual update steps.

### Fixed
- Game Manager's "Adult" column was always empty for RetroBat collections, since RetroBat's scraper has no `<adult>` gamelist tag at all (it's Recalbox-only) — it flags mature content via `<genre>Adults</genre>` instead. The Adult column now also picks that up.
- Game Manager's "Delete Selected ROMs" button got stuck on "Deleting…" after a successful delete (it only reset on failure), forcing a page refresh before the next delete.
- Fixed type errors in the DAT folder scan where `os.scandir()` entries were passed directly to functions expecting a `Path`.

## [1.0.0] - 2026-07-18

### Added
- Initial public release: ROM File List (hashing), Media Cleaner, DAT Manager (Catalogue / Coverage / ROM Scanner), Duplicates cleaner, Game Manager, Collection Compare, and Utilities tabs.
- SQLite-backed result cache for hash and DAT-verify results.
- Repository documentation (README, LICENSE, CONTRIBUTING, issue/PR templates).
