# ROMTools Audit Log

Started 2026-07-12. Full-codebase audit of `romtools.py` + `templates/base.html`
(backend + frontend, read in full by two separate review passes). This file
tracks findings and fix status so the work survives a lost session.

Status values: `TODO`, `IN PROGRESS`, `DONE`, `WONT FIX` (with reason),
`ON HOLD` (with reason).

---

## Second full audit pass (2026-07-13) — after CHD feature + settings/file-picker work

User asked for a fresh full re-audit given the volume of changes since the
first pass (CHD verification feature, settings system, file picker, many
smaller fixes). Two agents re-read both files in full, specifically primed
with everything changed this session so far, to catch regressions/
interaction bugs the piecemeal edits might have introduced.

- [x] **DONE** (2026-07-13) — **Real regression found and fixed**:
      `compare_delete()` (romtools.py, `/api/compare/delete`) was calling
      `remove_gamelist_entries(gamelist_path, set(file_list), False)` - 3
      positional args - but `remove_gamelist_entries()`'s signature had
      already been reduced to 2 params (`gamelist_path, filenames`) during
      the earlier "remove all dryrun params" pass. The trailing `False`
      (a leftover dryrun-shaped argument) was never cleaned up at this one
      call site, even though the other two call sites
      (`gamemanager_remove_gamelist_entries`, `delete_unverified`) were
      correctly updated. Since `run_in_executor(pool, func, *args)`
      forwards args positionally with no adaptation, this was a hard
      `TypeError: remove_gamelist_entries() takes 2 positional arguments
      but 3 were given` on every real invocation - meaning **any Compare
      delete on a folder with a gamelist.xml was completely broken** (the
      whole request would 500) since the fix that added gamelist cleanup
      to `compare_delete()` was merged, until caught by this audit.
      Fix: dropped the stray `False` argument. Verified against real
      disposable test data (dummy ROM + gamelist.xml, same pattern used
      earlier this session): delete now succeeds, gamelist entry
      correctly removed, backup created, no error.

- [x] **DONE** (2026-07-13) — Frontend audit (base.html, full re-read,
      ~4432 lines) found 3 additional issues, all fixed:
      1. `compareExportFullCsv()` was a leftover hand-rolled CSV builder
         that never got migrated to the shared `downloadCsvRows()`
         helper built earlier this session - meaning it was missing the
         UTF-8 BOM fix and emoji/symbol-stripping that specifically fixed
         the `â€”` mojibake bug for every *other* CSV export. Since Compare
         deals with real filenames (accented characters, em-dashes, etc.
         are common in game titles), this was a real gap, not just
         inconsistency. Fixed: now calls `downloadCsvRows()` like every
         other export.
      2. Compare's SSE `error` handler used `res.innerHTML = ...`
         (overwrite) instead of `+=` (append) like every sibling SSE
         consumer (`mcRun`/`md5Run`/`gmRun`). Not currently reachable
         mid-stream (today `compare_scan()` only errors before any data
         is emitted), but a latent trap if the backend is ever changed to
         report a per-folder error - fixed for consistency/future-proofing.
      3. Compare's stream-finalize callback didn't distinguish a genuine
         connection failure occurring *after* results had already started
         rendering from a clean finish - it silently just added the CSV
         export button either way, with no indication the results were
         incomplete. Fixed: now shows an explicit "connection lost -
         results above are incomplete" message in that case.
      Also fixed one performance concern raised by the audit: the
      "emit `file_progress` every file" change from earlier this session
      (needed so slow CHD extractions show progress) would have pushed
      tens of thousands of SSE events/DOM writes for a full-size MAME
      folder (25,000+ ROMs, near-instantly hashed) - changed to an
      adaptive stride (`max(1, len(rom_files) // 100)`, capped to ~100
      events per folder) so small/slow folders (CHD-heavy) still get
      per-file updates while huge fast folders don't get flooded.
      Verified against real data: re-ran the same 25-file Mega CD
      end-to-end verify scan used earlier - still 25/25 verified, still
      exactly 25 `file_progress` events (stride correctly computes to 1
      for a folder this small), confirming the stride change didn't
      regress the original CHD progress-visibility fix.
      Nothing else found wrong - the audit specifically re-checked every
      item changed this session (nav/home-card reorder, Compare SSE
      rewrite, ROM Scanner header checkbox + dual CSV export, all the new
      shared JS helpers, the 3-way confirm-dialog consolidation, Media
      Cleaner mode-pill removal, all `dryrun` removals, DAT Coverage
      auto-save, the new Settings section, and the file-picker mode) and
      confirmed each was implemented consistently with no stale
      references, no leftover dead code, and no signature-mismatch bugs
      beyond the one already listed above.

## Already completed (prior to this audit, same session)

- [x] Removed dead backend routes with no frontend caller: `/api/detect-mode`,
      `/api/dup/delete`, `/api/dup/delete-list` (romtools.py)
- [x] Fixed stale `<!-- COMPARE (stub) -->` HTML comment (base.html) — tab was
      fully implemented, comment was misleading
- [x] Swapped Compare/Utilities order in top nav
- [x] Compare tab: fixed column headers showing literal text "(folder path)"
      instead of the actual collection path
- [x] Compare tab: added reverse "delete files not found on the other side"
      function, mirroring the existing copy function
      - Backend: new `POST /api/compare/delete`
      - Frontend: `compareDelete()` + delete buttons next to each copy button
- [x] Fixed orphaned-gamelist-record bug in `/api/dat/delete-unverified`
      (shared by DAT Scanner, Duplicates, Game Manager delete flows) — now
      calls `remove_gamelist_entries()` after deleting ROM+media, with backup
- [x] Applied the same gamelist-entry cleanup to the new `/api/compare/delete`
- [x] Simplified Game Manager's `gmConfirmDelete()` — removed its now-redundant
      second network call to `/api/gamemanager/remove-gamelist-entries`
      (delete-unverified handles it server-side in one round trip now)
- [x] Updated status messages in all delete call sites (DAT Scanner x2,
      Duplicates, Game Manager, Compare) to surface gamelist-entries-removed count
- [x] Verified end-to-end with disposable test data: ROM + media + gamelist
      entry all correctly removed, backup created, untouched sibling entry's
      formatting preserved byte-for-byte

---

## Audit findings — High priority (data-integrity / real bugs)

- [x] **DONE** (2026-07-13) — `rename_files()` (romtools.py:2113-2197) never
      updates `gamelist.xml`'s `<path>` tag after renaming a ROM to its
      canonical DAT name. Creates an instantly-stale gamelist entry. Same
      class of bug as the delete-sync issue already fixed, missed on the
      rename path.
      Fix: new `rename_gamelist_entries()` helper (text-splice, same
      approach as `remove_gamelist_entries()`, with backup) called from
      `rename_files()` after a real rename succeeds. New `type: "gamelist"`
      result rows added, frontend updated to show a separate "gamelist
      entries updated" count instead of mixing them into the ROM-rename
      list. Verified end-to-end with disposable test data: path updated
      in-place, sibling entry's formatting untouched, backup created.
      NOTE (found while implementing, not yet triaged): `rename_files()`'s
      `dryrun` body param is read but never actually used to gate the real
      rename - it always renames for real regardless of the flag sent. Not
      fixed as part of this change; flag for a future pass.

- [x] **WONT FIX** — `compare_copy()` (romtools.py:2280-2324) never creates a
      `<game>` entry in the destination gamelist.xml for copied files. Mirror
      gap of what `compare_delete()` now fixes on the delete side.
      Reason (user, 2026-07-13): gamelist entries should only ever be
      created by the emulator frontend (RetroBat/Recalbox) itself, not by
      this tool. ROMTools may remove stale entries but must not fabricate
      new ones.

- [x] **DONE** (2026-07-13) — `compare_copy()`/`compare_delete()`
      (romtools.py:2296-2358) skip the `Path(x).name` sanitization every
      sibling delete endpoint applies to `folder` and per-file filenames
      from the request body. Not reachable via normal UI flow today, but
      it's the one write/delete path without the same defense-in-depth, and
      `compare_delete` is an unconditional `unlink()` with no dryrun step.
      Fix: new shared `_resolve_within_root(root, rel)` helper resolves the
      folder path and rejects it (400 "invalid folder path") if it escapes
      root; per-file filenames sanitized to `Path(fn).name` in both
      endpoints. Verified: a `folder=..\..\Windows` payload to
      `/api/compare/delete` now correctly returns 400; a legitimate nested
      bucket (`nes/media/images`) still copies/deletes normally.

- [x] **DONE** (2026-07-13) — DAT Manager extension-mismatch logic
      disagreed between sub-tabs: DAT Coverage (base.html:879,897) flagged
      mismatch if **any** ext check failed (`.some(c => !c.match)`); ROM
      Scanner Step 1+2 (base.html:1074,1143) only flagged it if **all**
      checks failed (`.every(c => !c.match)`). Only visible on folders
      mapped to 2+ DATs with mixed results (e.g. `gba`, mapped to 3 DATs
      per mapping.json).
      Fix: new shared `computeExtMismatch(extChecks)` helper (next to
      `esc()`) using the "all fail" rule; all 4 call sites (Coverage
      summary card + table row, Scanner Step 1 + Step 2) now route through
      it, so the two tabs can no longer disagree on the same folder.

- [x] **DONE** (2026-07-13) — `_GAME_BLOCK_RE` (romtools.py:1954, used by
      `remove_gamelist_entries()`) required a literal tab character before
      `<game>`. CONFIRMED against real data: scanned all 53 gamelist.xml
      files across both roots (18,062 total <game> entries) — 18,056
      (99.97%) matched fine (tab indented); exactly 3 files failed
      completely: `ports\Doom`, `ports\Quake`, `ports\Wolfenstein 3D` on
      the Recalbox (\\wt_cloud) side, all 2-space indented and scraped by
      "Skraper" instead of the native scraper (6 entries total).
      Fix: regex changed from requiring a literal `\t` to
      `^[ \t]*<game\b.*?</game>[ \t]*\r?\n?` with `re.MULTILINE` — matches
      any leading whitespace at the start of a line instead of exactly one
      tab. Verified with a static re-scan of all 53 real files: new regex
      now matches all 18,062 entries (0 mismatched files), and produces
      byte-identical matched text to the old regex on every one of the
      50 files the old regex fully handled (0 differences) — confirmed
      zero regression on already-working entries per the user's
      requirement.

- [x] **DONE** (2026-07-13) — Two different ROM-exclusion matching rules
      for the same `ROM_FILE_EXCL` list: `list_rom_candidate_files()` (DAT
      Scanner, Game Manager, Duplicates) was case-insensitive + suffix-only;
      `list_roms()` (MD5 Scan, **Compare**) was case-sensitive + substring
      match anywhere in the filename. CONFIRMED against real data: scanned
      all 64,285 files across both roots — exactly 9 real files affected,
      all under `ports\` on the Recalbox side (Prince of Persia's 8
      uppercase `.DAT` files + Wolfenstein 3D's `READ1ST.TXT`).
      Fix: `list_roms()`'s exclusion check changed to case-insensitive
      suffix-only matching, matching `list_rom_candidate_files()`'s rule.
      Verified live against the real `ports\Prince of Persia\data` and
      `ports\Wolfenstein 3D` folders: `list_roms()` (as used by MD5 Scan/
      Compare) now correctly excludes all 9 previously-affected files while
      still returning the folder's genuine ROM-adjacent files unchanged.

- [x] **NOT A BUG** — `gamemanager_build()`'s multidisc handling
      (romtools.py ~1873-1881) assumes `<multidisk>` holds a JSON array of
      disc filenames. VERIFIED against real data 2026-07-13:
      `D:\RetroBat\roms\psx\gamelist.xml` has 183 real `<multidisk>` tags,
      format confirmed as e.g.
      `<multidisk>["./Star Ocean - The Second Story (USA) (Disc 1).chd","./Star Ocean - The Second Story (USA) (Disc 2).chd"]</multidisk>`
      — exactly the JSON-array-of-filenames format the code assumes.
      No other system folder in either root uses `<multidisk>`. No fix
      needed.

## Found and fixed during ad-hoc user questions (2026-07-13)

- [x] **DONE** — User asked why `FinalBurn Neo (ClrMame Pro XML, Arcade
      only).dat` showed as "Unknown" format in the DAT Catalogue instead of
      MAME. Root cause: `detect_dat_format()` (romtools.py) only matched
      `"mame"`, `"fbneo"`, or `"fba"` as substrings of the header `<name>`
      field, but this real DAT's `<name>` is literally
      `"FinalBurn Neo - Arcade Games"` - "finalburn neo" (two words)
      contains none of those three substrings.
      Fix: added `"finalburn"` to the keyword list, and broadened the
      check to also match against `<author>` (this DAT's author is also
      "FinalBurn Neo"), not just `<name>`. Verified against all 23 real
      DAT files in DatRoot: the FBNeo DAT now correctly shows `format=mame`
      and every other DAT's classification (nointro/redump/mame) is
      unchanged.

- [x] **DONE** — User reported ROM Scanner/Verify's CSV export was
      inconsistent - "only rows that have been scanned are in the export
      but sometimes not all the scanned rows are in the csv" - and that it
      included non-printable/decorative characters (arrows, status icons).
      Root cause of the flaky/missing-row problem: `exportTableToCSV()`
      (shared by every CSV-export button in the app) read cell text via
      `.innerText`, which only returns text that's actually laid out/
      rendered by the browser - it silently returns `''` for any content
      inside a momentarily `display:none` ancestor, which explains the
      sporadic "sometimes rows are missing" behavior (a table-population
      bug was ruled out first: `scanBuildVerifyTable()` already adds a row
      for every folder with ROMs upfront, scanned or not, and
      `exportTableToCSV` iterates every `<tr>` unconditionally - the loss
      was happening at text-extraction time, not row-population time).
      Fix: switched to `.textContent` (immune to rendering-state timing),
      with a special case for cells containing `<details>` (the Verify
      table's per-folder detail column, which can hold hundreds of
      collapsed game names) to extract just the `<summary>` lines instead
      of dumping the full collapsed content into one CSV cell. Also added
      a regex strip targeting the arrow/symbol/dingbat and emoji Unicode
      blocks specifically (not all non-ASCII, so real non-English game
      titles are untouched) to remove decorative UI icons (✔ ✘ ⚠ 🔴 🟢 ✏
      🩹 → ➖ etc.) that read fine in the app but show up as garbled
      bytes/boxes in a CSV opened in Excel. Verified the regex against all
      12 status-icon characters actually used in the app (all correctly
      stripped) and a set of real-world game-title-style strings with
      accents/punctuation/CJK characters (all correctly preserved).

      **Follow-up round (2026-07-13, same day):** user reported 3 more
      issues after testing the above: (1) `â€”` mojibake appearing in some
      columns, (2) the leading checkbox column exporting as a useless
      blank column, (3) the Details column's category-summary squish
      (from the fix above) wasn't what was wanted - the detailed
      missing/missing-filtered/etc. lists needed to be real, separate CSV
      columns, and only in an explicitly separate "detailed" export, with
      a plain "summary" export having no list content at all.
      - `â€”` root cause: classic Excel/UTF-8 gotcha - a CSV `Blob` without
        a UTF-8 BOM gets misread by Excel on Windows as Windows-1252,
        turning any multi-byte UTF-8 character (the "—" em-dash placeholder
        is 3 bytes) into several garbled Latin-1 characters. Fixed by
        prepending `'﻿'` to the Blob content in a new shared
        `downloadCsvRows()` helper.
      - Checkbox column: `exportTableToCSV()` now detects columns where
        every cell across every row is just a bare checkbox with no other
        text, and drops that column entirely - generic fix, so it also
        cleans up Game Manager's CSV export (same leading checkbox column
        issue) without needing a separate fix there.
      - Detailed lists: replaced the single "Export CSV" button with two -
        "Export Summary CSV" (counts only, no lists) and "Export Detailed
        CSV" (adds 5 extra columns: Not in DAT List, Bad Dumps List, Wrong
        Name List, Missing List, Missing Filtered List, each
        semicolon-joined within its cell). Rebuilt as a dedicated
        `scanExportVerifyCsv(includeDetails)` function that reads directly
        from the cached scan-result data (`scanCacheGet()`) rather than
        scraping the rendered table - sidesteps the whole DOM-timing class
        of bug entirely for this export, not just the checkbox/mojibake
        symptoms. Verified: page loads cleanly (200), both new button IDs
        present in the served HTML, BOM character confirmed correctly
        embedded as literal U+FEFF (not corrupted) by reading the raw file
        bytes back.

- [x] **DONE** — User asked why DAT Coverage showed a yellow "No DAT"
      warning for `megacd` despite `mapping.json` mapping it to a real
      DAT filename. Root cause found by checking real files directly:
      `mapping.json` referenced
      `Sega - Mega CD & Sega CD - Datfile (549) (2026-05-28 18-06-58).dat`,
      but that file only existed as a `.zip` inside `DatRoot\Archives\` -
      never extracted to a plain `.dat` in `DatRoot\` itself, so it was
      invisible to the catalogue scan and `has_dat` fell back to false.
      User extracted the file into `DatRoot\` directly, then asked to also
      harden DAT-file discovery to never look in subfolders (so a
      not-yet-extracted archive folder can't silently get swept in). Found
      one genuinely recursive spot: `dat_descriptions()`'s fallback
      (romtools.py, backs `/api/dat/descriptions`, used by Duplicates tab
      and DAT Scanner enrichment) used `dat_root.rglob("*")` - unlike
      `scan_dats()` and `_build_scan_overview()`, which already correctly
      used non-recursive `glob("*.dat")`.
      Fix: changed the `rglob("*")` fallback to non-recursive `glob("*")`,
      matching the other two DAT-scanning routes. Verified against real
      data: `megacd` now correctly reports `has_dat: true` with all 549
      games from the (now-findable) Redump DAT via a live
      `/api/dat/scan-overview` call.
      **Follow-on note surfaced by this fix:** the same response shows
      `ext_checks` flagging a mismatch - "DAT expects .bin, .cue but
      folder has .chd" - since Mega CD's 25 ROMs are stored as `.chd`.
      This is the same CHD-vs-Redump-hash limitation already tracked
      under the ON HOLD item above (Mega CD is CD-audio-capable and was
      already in that hold's inspected-folders list) - not a new issue,
      just now visible because the DAT is finally being found. The
      yellow "No DAT" warning will be replaced by a red "Ext Mismatch"
      one for this folder until that hold is revisited.

## Newly discovered while fixing High priority items (not yet triaged)

- [x] **DONE** (2026-07-13) — `rename_files()` (romtools.py,
      `/api/dat/rename-files`) read `dryrun` from the request body but
      never actually checked it before renaming - the real rename always
      happened regardless of the flag sent.
      Resolution (user, 2026-07-13): rather than wire it up, remove
      `dryrun` entirely everywhere in the app - the two-click confirm
      already provides the safety net, per the earlier "no dryrun params"
      decision (see the `compare_delete` WONT FIX entry above). Removed
      `dryrun` from all 5 endpoints that had it: `/api/dup/clean-readmes`,
      `remove_gamelist_entries()` (shared helper - also dropped its
      "would_remove"/"would_delete" status variants, always "removed"/
      "deleted" now), `/api/gamemanager/remove-gamelist-entries`,
      `/api/dat/delete-unverified`, and `/api/dat/rename-files`. Updated
      all 7 frontend call sites that sent `dryrun: false` to stop sending
      it. Verified: page loads cleanly (200); live-tested
      `/api/dat/delete-unverified` end-to-end against disposable test
      data (no `dryrun` param sent) - ROM deleted, gamelist entry removed,
      backup created, identical behavior to before, just without the now-
      meaningless `dryrun` field in the response.

## Audit findings — Medium priority (consistency / UX gaps)

- [x] **DONE** (2026-07-13) — Compare tab had no SSE streaming progress, no
      Stop/cancel button, and no CSV export, unlike every other scan tab.
      Fix: backend `compare_scan()` rewritten as a true SSE generator
      (`_compare_system_dirs()` enumerates systems up front, `list_roms()`
      gained an optional `base_rel` param so it can be scanned one system
      at a time while producing identical bucket keys to the old
      whole-tree call); frontend rewritten to use `streamPostAbortable` +
      `compareScanStop()` + incremental per-folder row rendering
      (`compareRenderShell`/`compareAppendFolderRow`/`compareUpdateSummary`)
      + `compareExportFullCsv()` (Folder/Status/Filename for every
      matched/only1/only2 entry, not just summary counts).
      Verified: new SSE endpoint produces byte-identical totals to the old
      whole-tree algorithm against real data (matched/only1/only2/folder
      count all match exactly); user live-tested in browser and confirmed
      working after one follow-up fix (Stop button used
      `style.display=''` which fell back to the `.btn-stop` CSS class's
      `display:none` instead of showing it — corrected to
      `'inline-block'` matching the pattern every other Stop button uses).
      Also polished per user follow-up: enlarged the folder-path text under
      the Only-in-1/Only-in-2 headers (0.72em→0.95em, dropped the opacity
      fade) and darkened the "Only in 2" orange from `#e67e00` to `#b35900`
      for contrast, applied consistently across the summary card, table
      cells, header, and detail-panel label.

- [x] **DONE** (2026-07-13) — DAT Scanner "Step 1" auto-ran on sub-tab
      entry with no abort guard, so two overlapping scans could race and
      the older one could overwrite the newer one's results.
      Fix: incrementing `_scanStep1Token` guard in `scanLoadStep1()` —
      each call captures its own token; on resolve, only the call whose
      token still matches the latest one is allowed to render (both the
      success and catch handlers check this before touching the DOM).

- [x] **DONE** (2026-07-13) — `verify_folders()` computed dead `bad`/
      `bad_list` SSE fields that were never populated anywhere in the scan
      loop. Fix: removed entirely per user's "remove dead fields, don't
      implement a fake check" direction — both the backend fields/
      initializers and the two frontend references (a `delete f.bad_list`
      no-op and a cache-trim key list entry) removed.

- [x] **WONT FIX** — `compare_delete` has no `dryrun` option unlike its
      closest sibling `delete_unverified` — no server-side preview/confirm
      round trip before an unconditional delete.
      Reason (user, 2026-07-13): dryrun params are unnecessary overhead —
      the two-click confirm plus Compare's own scan-then-review flow is
      sufficient. Do not add dryrun support to endpoints going forward.

- [x] **DONE** (2026-07-13) — `/api/gamemanager/image` served any absolute
      file path with no root restriction.
      Fix: added a required `rompath` query param; endpoint now resolves
      both paths and rejects (400) any `path` outside `rompath`. Frontend
      (`gmRenderTable()`) updated to send the current Game Manager rompath
      alongside every thumbnail request.

## Audit findings — Low priority (duplication to unify)

- [x] **DONE** (2026-07-13) — Three copy-pasted two-click confirm
      implementations (`mcArmDelete`/`mcConfirmDelete`,
      `gmArmDelete`/`gmConfirmDelete`,
      `gmArmRemoveGamelist`/`gmConfirmRemoveGamelist`) instead of reusing
      the existing shared `confirmAction()` helper already used by 8+ other
      delete buttons.
      Fix: all three now use `confirmAction(this, () => xConfirmY(this))` on
      a single button; the three `*ArmY()` functions and their paired
      warning-div/second-button markup removed entirely. Game Manager's
      "Remove From Gamelist" explanatory text (previously hidden until
      armed) kept as an always-visible note instead, since it explains a
      genuinely non-obvious distinction (removes only the gamelist entry,
      not the ROM). Dead `.btn-delete-confirm`/`.delete-warning` CSS rules
      removed. Verified: page still loads cleanly (200, template renders),
      grepped for stale references to the removed classes/functions - none
      found.

- [x] **DONE** (2026-07-13) — Four near-identical "delete result"
      status-message formatters (`verifyDeleteUnmatched`, `dupDeleteFiles`,
      `gmConfirmDelete`, `compareDelete`) — same data, four different
      hand-rolled HTML strings.
      Fix: new shared `formatDeleteSummary({rom, media, gamelist, errors,
      backups, showMedia})` (next to `confirmAction()`); all four call
      sites now build their counts and call it. Standardized on the
      single-line "✔ Deleted N ROM(s) + N media file(s) + N gamelist
      entries" style (3 of 4 already used it; `verifyDeleteUnmatched`'s
      old multi-line `<br>`-separated wording was the one that drifted,
      now matches). `compareDelete` uses `showMedia:false` since
      `/api/compare/delete` has no media-cleanup concept at all (avoids a
      misleading "+0 media file(s)"). `dupDeleteFiles` gained the backup
      note the other three already had (a small consistency improvement,
      not a behavior change). Verified: page loads cleanly (200),
      grepped - all 4 call sites route through the one function.

- [x] **DONE** (2026-07-13) — Three near-identical "group selected files by
      folder" loops before firing per-folder delete requests
      (`dupDeleteFiles`, `gmConfirmDelete`, `gmConfirmRemoveGamelist`).
      Fix: new shared `groupByFolder(items)` helper (next to
      `formatDeleteSummary()`); all three call sites now use it. Verified:
      page loads cleanly (200), grepped - all 3 call sites route through
      the one function.

- [x] **DONE** (2026-07-13) — `Math.round(done/total*100)` progress-percent
      math reimplemented independently 7 times across tabs (mcRun, md5Run,
      scanStep2Run x2, scanRenderFolderResult, dupScan, gmRun).
      Fix: new shared `pct(done, total)` helper (next to `fmtSize()`,
      returns 0 for a falsy/zero total); all 7 call sites now use it.
      A couple of sites had a local `const pct = ...` variable reused later
      in the same function - renamed those to `p` to avoid shadowing the
      new global `pct()` function rather than restructuring the call
      sites. Verified: grepped for any remaining raw
      `Math.round(x/y*100)` pattern - only the helper's own implementation
      remains; page loads cleanly (200).

- [x] **DONE** (2026-07-13) — Only Media Cleaner let the user manually
      override RetroBat/Recalbox mode detection; the other media-touching
      tabs had no escape hatch.
      Resolution (user, 2026-07-13): instead of adding the override
      elsewhere, remove it entirely and rely on auto-detection everywhere
      - simpler than adding it in 4 more places, and auto-detect has been
      reliable in practice.
      Fix: removed the Auto/RetroBat/Recalbox mode-pill UI, `mcMode`
      variable, and `mcSetMode()` from Media Cleaner; dead `.mode-pill`
      CSS removed. `/api/media/scan` simplified to drop the `mode` Form
      param entirely and always run its auto-detection branch (previously
      gated behind `if mode == "auto":`, now unconditional) - still emits
      the `{"type":"mode"}` SSE event so the UI's "Detected: retrobat"
      label keeps working. Verified: page loads cleanly (200), no
      remaining `mode-pill`/`mcSetMode`/`mcMode` references; live-tested
      `/api/media/scan` with no `mode` param against the real
      `D:\RetroBat\roms\nes` folder - correctly auto-detected `retrobat`
      and scanned all 2,638 media files with no errors.

- [x] **DONE** (2026-07-13) — Backend dead code: `md5_of_zip()` and
      `get_image_dir()` (romtools.py) were defined but never called
      anywhere. Removed both. Verified: syntax-checks clean.

- [x] **DONE** (2026-07-13) — `delete_unverified()` hand-duplicated the
      gamelist media-field walk that `get_xml_media_refs()` already
      implements. They actually needed different shapes though
      (`get_xml_media_refs` returns one flattened set across the whole
      gamelist for orphan detection; `delete_unverified` needs a per-ROM
      `{filename: [media_paths]}` breakdown) - not pure duplication, but
      both re-walked the same `<game>` elements with the same RetroBat/
      Recalbox field lists independently.
      Fix: new shared `_walk_gamelist_media(xml_path, mode)` helper
      returns `[(raw_path_text, rom_filename, {field: relative_path}), ...]`
      once; `get_xml_media_refs()` now builds its flattened set from it,
      and `delete_unverified()` builds its per-ROM map from it. Also
      removed the stale, unused `get_xml_image_refs` alias and its
      misleading comment claiming delete-unverified needed it (it never
      called it). Verified: ran both old algorithms (reconstructed from
      the pre-refactor code) and the new shared-walker versions against
      all 44 real gamelist.xml files across both roots - 0 mismatches,
      byte-identical output.

- [x] **DONE** (2026-07-13) — Zip/7z inner-file hashing loop was
      implemented independently in `md5_scan()`'s inline block and in
      `hash_rom_file()`.
      Fix: new shared `read_archive_entries(filepath)` helper returns
      `{inner_filename: raw_bytes}` for a zip/7z (handles the archive-open/
      iterate/error-swallow logic once); both `hash_rom_file()` and
      `md5_scan()`'s inner-file branch now build on it, keeping their own
      separate hash-selection logic (single hash_type vs simultaneous
      crc/md5/sha1 checkboxes) since that genuinely differs between
      callers. Verified against 60 real zip ROMs (NES/SNES/Mega Drive):
      reconstructed the old inline algorithm and compared byte-for-byte
      against the new shared-helper version - 0 mismatches; also verified
      `read_archive_entries()`'s returned entry names match `zipfile`'s
      own listing exactly.

- [x] **DONE** (2026-07-13) — `_dat_tree_cache` (romtools.py) was never
      evicted or bounded — grew for the life of the process, one entry per
      distinct DAT file path ever parsed (MAME's alone is ~80MB on disk,
      several times that once parsed as an ElementTree).
      Fix: converted to an `OrderedDict` with LRU eviction, capped at
      `_DAT_TREE_CACHE_MAX = 8` entries - `move_to_end()` on every hit/
      insert, oldest entry popped once the cap is exceeded. Verified: ran
      `_parse_dat_cached()` against all 23 real DAT files in DatRoot - cache
      size never exceeded 8; confirmed a cache hit returns the same object
      with no size growth, and a previously-evicted DAT correctly
      re-parses on demand.

- [x] **DONE** (2026-07-13) — Shared `ThreadPoolExecutor` (`_thread_pool`)
      had only 2 hardcoded workers; three concurrent long-running scans
      across browser tabs would queue/stall behind each other.
      Fix: `max_workers=max(4, os.cpu_count() or 4)` - scales with the
      machine instead of a flat 2 (moved the `os` import earlier since the
      pool construction now depends on it). Verified: confirmed the pool
      actually initializes with 16 workers on this machine (16 CPUs).

## Multi-disc / .m3u handling (found during targeted review, 2026-07-13)

User-requested review of how multi-disc games (separate standalone disc
files, e.g. `.chd`, plus an `.m3u` playlist referencing them) are treated
across every feature - as one entity or as independent files. Traced
through every tab; found two real bugs and one practical gap, all
currently dormant because PSX (the only system in the real collection
with `<multidisk>` entries - 183 of them in
`D:\RetroBat\roms\psx\gamelist.xml`) has no DAT mapped in `mapping.json`
yet. They will trigger the moment PSX (or any other CHD-multi-disc
system) gets DAT-mapped with hashing enabled.

- [x] **DONE for Saturn/PSX/Mega CD** (2026-07-13) — DAT Scanner couldn't
      verify `.chd` files against Redump DATs at all (not just a
      multi-disc edge case - this turned out to be true for single-disc
      CHDs too). Deep-dived into why and what a real fix would require;
      parked as ON HOLD, then the user supplied a real `chdman.exe`
      (placed at `chdman/chdman.exe` in the project folder) and asked to
      build real support for the 3 systems proven to work. Implemented via
      a full plan-mode design + build (see
      `C:\Users\<user>\.claude\plans\delegated-wandering-peacock.md`).
      Findings from the original investigation kept below for context.

      **What shipped:**
      - `detect_chd_track_format()` (romtools.py) - peeks at a CHD v5
        file's own header/metadata (no chdman call) to tell standard
        CD-track CHDs (`CHT2`/`CHTR` tag) from GD-ROM CHDs (`CHGD` tag),
        so Dreamcast is excluded automatically rather than via a
        hardcoded folder-name list. Verified against all 501 real CHD
        files across all 4 folders: 100% correct classification
        (Saturn/PSX/MegaCD all `cdrom`, Dreamcast all `gdrom`).
      - `hash_chd_file()` - extracts real track data via
        `chdman extractcd` (subprocess, `tempfile.TemporaryDirectory` for
        automatic cleanup, 120s timeout) and hashes each track. Wired into
        `hash_rom_file()` for `.chd` - this also gives Game Manager's
        DAT-match column CHD support for free, since it already calls
        `hash_rom_file()` generically.
      - `check_extension_match()` gained a `chd_verifiable` param;
        `_build_scan_overview()` now samples a folder's CHDs once via the
        new `sample_chd_verifiable()` and passes the result through -
        Saturn/PSX/MegaCD now report `match: true` with a clear note
        instead of the generic "DAT expects .bin, .cue" mismatch;
        Dreamcast still correctly reports a mismatch, now with an
        accurate "GD-ROM not supported yet" note instead of a misleading
        generic one.
      - New app-level `config.json` + `/api/settings` GET/POST + a
        chdman-path field in the Utilities tab (first real persisted
        app-level config the project has - previously everything was
        per-request form input or DatRoot's `mapping.json`). Defaults to
        the project-relative `chdman/chdman.exe` if unset.
      - `verify_folders()`'s SSE `file_progress` event now fires every
        file instead of every 20 - CHD extraction takes several seconds
        per file (near-instant for zips), so the old batching could leave
        a folder with no progress update for minutes.
      - Investigated whether CHD needed the same wrong-name-detection fix
        zip/7z get - turned out unnecessary: CHD only ever comes from
        Redump-format DATs, and that code path has no wrong-name
        detection at all (for zip-based Redump sets either) - no bug.
      - **Verified end-to-end against real data**: `/api/dat/scan-overview`
        live call confirms Saturn/PSX/MegaCD `match: true` and Dreamcast
        `match: false` with the new notes; a full real
        `/api/dat/verify-folders` run against all 25 real Mega CD `.chd`
        files completed in 165s with **25/25 verified, 0 unknown, 0 wrong
        name** - 100% match, consistent with the original experiment.
        Saturn and PSX DATs were also extracted into `DatRoot` (by the
        user, alongside Mega CD and Dreamcast) during this work, so both
        are mapped and ready for a full scan whenever wanted (not
        separately timed end-to-end here - Mega CD's 25-file run was
        judged sufficient to prove the pipeline; Saturn/PSX are ~7-8 min
        / ~33 min respectively per the original experiment's throughput).

      **Still out of scope / not done:** Dreamcast/GD-ROM verification
      (the compatibility gate correctly keeps it excluded rather than
      offering a scan that would mostly fail); parallelizing chdman
      extraction within a single verify scan (stays sequential, matching
      existing architecture); CHD rename support.

      Original investigation findings (kept for context):

      **Original framing (romtools.py `verify_folders()`) was too narrow:**
      the Redump subset-match logic only correctly matches a multi-disc
      game when all its discs are packed inside ONE zip/7z archive (it
      compares a combined hash-set from that single file's contents).
      Real CHD-based multi-disc collections store each disc as a separate
      standalone file, so that specific matching path can never succeed
      for them. But investigating further revealed a more fundamental
      blocker underneath that:

      **CHD is a compressed, transformed container - hashing the raw
      `.chd` file bytes (what `hash_rom_file()` currently does for any
      non-zip/7z extension, i.e. every `.chd`) can never match a Redump
      hash, for single-disc CHDs too, not just multi-disc ones.** Redump
      hashes are computed from the original uncompressed CUE/BIN track
      data; a CHD's compressed bytes bear no resemblance to that.

      **Real user CHD collections inspected directly (2026-07-13),
      confirming scope:** `D:\RetroBat\roms\{saturn,psx,megacd,dreamcast}`
      - 57 / 217 / 25 / 202 CHD files respectively (501 total). Parsed
      real CHD v5 headers from one sample per system:
      - Saturn (Albert Odyssey): 22 tracks (1 data + 21 CD-audio) - heavily
        multi-track.
      - PSX (Ace Combat 2): 1 track - this sample happens to be
        data-only, but PSX as a system is not consistently single-track.
      - Mega CD (AH3 Thunderstrike): 12 tracks - heavily multi-track.
      - Dreamcast (18 Wheeler): 3 tracks, and uses a *different* metadata
        tag (`CHGD`, GD-ROM-specific) than the other three (`CHT2`) -
        needs its own handling on top of everything else.

      **Why this is hard, not just "read the header":** CHD v5 stores a
      linked list of metadata entries (`CHT2`/`CHGD` tags) describing
      track *boundaries* (type, frame count, pregap) - but **no per-track
      hash**. The only hashes in the CHD header (`rawsha1`/`sha1`) cover
      the entire decompressed image as one blob (all tracks concatenated),
      which doesn't correspond to Redump's per-track hash structure at
      all. To get a real per-track hash to compare against Redump, the
      actual compressed hunks have to be decompressed (these CD-image
      CHDs use a zlib/LZMA-based codec split across sector data and
      subchannel data) and each track's byte range hashed from the
      reconstructed data. That's a real CHD decoder, not a header parser.

      **No shortcut available:** confirmed no maintained Python CHD-
      decompression library exists (a PyPI package literally named `chd`
      was tested and turned out to be an unrelated package pulling in
      Scrapy/Twisted - a name collision, not a CHD library). `chdman`
      (MAME's official CLI tool, the reference implementation) is not
      installed anywhere on this machine and isn't bundled with RetroBat
      (searched both).

      **Two real paths forward, either substantial:**
      1. Shell out to `chdman` (external dependency - user would need to
         install it separately and ROMTools would need a "CHD Tool Path"
         setting, similar to ROM Path/DAT Root) to extract/verify tracks.
         This is the pragmatic option - reuses the battle-tested reference
         decoder instead of reimplementing it.
      2. Write a native CHD decoder in Python (self-contained, no external
         install, but a multi-day+ engineering effort with real
         correctness risk, and Dreamcast's separate `CHGD` format is
         additional scope on top).

      Reason for hold: real scope turned out much larger than a normal
      bug fix once investigated - user wants to defer this decision
      rather than commit to either path right now.

      **Related bug folded into this hold (2026-07-13):** Game Manager
      (`gamemanager_build()`, romtools.py ~1914-1917) crashes with an
      unhandled `KeyError` for any multi-disc `.m3u` row when DAT hashing
      is enabled and a DAT is mapped to that folder - it does
      `rom_files[filename]` to hash the row, but `.m3u` is excluded from
      `rom_files` (it's in the non-ROM extension exclusion list), so that
      key never exists for an `.m3u` filename. This is the same underlying
      problem as the CHD hold above: even if the `KeyError` itself were
      patched, hashing the `.m3u` (a text playlist, not ROM data) could
      never determine real DAT-match status without the same
      CHD-decompression-and-per-track-hash machinery described above to
      roll up the underlying discs' hashes into one result for the row.
      **Update (2026-07-13, still open):** PSX now DOES have a real DAT
      mapped (extracted into `DatRoot` during the CHD verification work
      above), so this is now genuinely reachable - not just a theoretical
      "if someone maps it later" risk. Still not fixed: this crash is
      specifically in Game Manager's row-hashing path
      (`gamemanager_build()`), separate from the DAT Scanner verify path
      that just got real CHD support - Game Manager would need its own
      "skip/handle `.m3u` rows gracefully instead of calling
      `rom_files[filename]`" fix, which is a small, separable piece that
      could be done without any further CHD-decompression work. Worth
      prioritizing given PSX Game Manager use is now realistic.

      **FIXED (2026-07-13):** Added `and filename in rom_files` to the
      hashing gate in `gamemanager_build()` (romtools.py, `if on_disk and
      dat_files and filename in rom_files:`), so `.m3u` rows (on_disk via
      the fallback check, but never a key in `rom_files`) simply skip DAT
      hashing/matching instead of crashing - `dat_game_name`/`category`/
      `clone_status`/`clone_parent` fall through to their existing default
      empty-string values, same as any other unmatched row. Verified
      against real production data: live `/api/gamemanager/build` call
      against the real `D:\RetroBat\roms\psx` folder + real `DatRoot`
      mapping, `hash_type=crc32` - 183 real `.m3u` rows processed, zero
      errors, each correctly returned `dat_matched: false` with empty DAT
      fields instead of raising `KeyError`. This is now fully resolved,
      independent of the CHD/GD-ROM hold above (which remains ON HOLD for
      Dreamcast only).

- [x] **DONE** (2026-07-13) — Compare treated each disc file as fully
      independent and excluded `.m3u` entirely (same non-ROM exclusion
      list as everywhere else). Practical consequence: copying a missing
      multi-disc game via Compare copied the `.chd` disc files but
      silently left the `.m3u` playlist behind, since it was never part of
      the compared/copyable file set - the destination ended up with a
      non-functional multi-disc set until the `.m3u` was copied manually.
      Fix: new `COMPARE_FILE_EXCL` constant (ROM_FILE_EXCL minus `.m3u`
      and `.cue`) used only by `compare_scan()`, so Compare treats
      playlist/index files as real comparable/copyable files while every
      other tool (MD5 Scan, DAT Scanner, Game Manager) keeps excluding
      them as non-hashable ROM payloads, unchanged. Verified against the
      real `D:\RetroBat\roms\psx` folder (183 real `.m3u` files, matching
      the 183 `<multidisk>` gamelist entries found earlier): Compare's
      file listing now correctly includes all 183, while `.txt`/`.png`/
      `.nfo` junk remains excluded as before.

Confirmed working correctly, no action needed: **Duplicates** tab is
already multi-disc-aware - it strips `(Disc N)` for grouping purposes but
tracks disc numbers separately and deliberately does NOT flag Disc 1 vs
Disc 2 of the same game as duplicates of each other (with a "skip
multi-disc grouping" user override available). **ROM File List (MD5
Scan)** treats each disc file as an independent row with no grouping,
which is consistent/expected for a raw hash-listing tool.

## Confirmed clean (no action needed)

- All 21 backend routes have a live frontend caller — no dead endpoints
  besides the helper functions listed above.
- localStorage keys are consistently `romtools_`-prefixed, no collisions.
- No typo'd/dead `onclick` handler references found anywhere in base.html.

## New feature: SQLite result cache (2026-07-13)

User asked for a general SQLite-backed cache to speed up repeat scans,
specifically calling out ROM Scanner Verify results as the priority. Full
plan-mode design + build (see
`C:\Users\<user>\.claude\plans\delegated-wandering-peacock.md`).

**What shipped:** new `cache/cache.db` (SQLite, stdlib `sqlite3`, one
connection per worker thread via `threading.local()`, WAL mode) with
three tables:
- `file_hashes` — caches `hash_rom_file()`'s per-file hash results, keyed
  by `(path, hash_type)`, invalidated on mtime/size change. Wired
  transparently into `hash_rom_file()` itself, so `verify_folders()`,
  `gamemanager_build()`, and any future caller all benefit for free.
- `verify_results` — caches `verify_folders()`'s per-folder `folder_done`
  result, keyed by `(rompath, datroot, folder, hash_type)`, invalidated
  by a `compute_verify_signature()` fingerprint covering both the ROM
  folder's files AND the mapped DAT files (so a DAT re-map/update also
  correctly busts the cache, not just ROM changes). Cache hits skip the
  entire per-file hashing loop and yield the cached `folder_done` event
  immediately, tagged `from_cache: true`; the SSE contract and frontend
  rendering path are otherwise untouched.
- `simple_hashes` — caches `md5_scan()`'s crc/md5/sha1 results per file
  (and per zip/7z inner entry), independent per-algorithm so a later
  request wanting an additional algorithm backfills just that column.
- New `/api/settings/cache` (GET stats) and `/api/settings/cache/clear`
  (POST) routes; new "Result Cache" block in the Utilities → Settings
  tab (`settingsLoadCache()`/`settingsClearCache()`) showing row counts +
  DB size with a two-click confirm Clear button.

**Verified against real production data** (`D:\RetroBat\roms\megacd` +
`O:\MYNAS\PYRom Manager\DatRoot`, all 25 real Mega CD `.chd` files):
- Run 1 (cold): 167.1s, 25/25 verified, `from_cache: false`.
- Run 2 (same folder, unchanged): **0.08s**, identical 25/25 result,
  `from_cache: true`.
- Invalidation test: touched one real file's mtime only (no content
  change, later restored to its original `12/24/2022 07:17:14`) → next
  run correctly detected the folder as stale (`from_cache: false`) but
  took only 5.9s, not 167s — proving the two cache layers compose
  correctly (folder-level cache correctly invalidated; only the one
  changed file's hash was recomputed, the other 24 still served from the
  per-file cache).
- `gamemanager_build()` on the same now-cached megacd folder: 5.8s (down
  from the 167s cold cost), 25/25 `dat_matched: true` — confirms the
  shared `hash_rom_file()` cache benefits Game Manager automatically with
  no code changes there, as designed.
- `md5_scan()` on the same folder: run 1 16.8s, run 2 (cached) 0.08s,
  byte-identical hash output both runs.
- `/api/settings/cache` GET/clear round trip: counts correctly reset to
  0 after clear, DB size unaffected functionally (VACUUM's disk-size
  reclaim under WAL mode lags slightly — cosmetic only).
- All ROM/DAT files left untouched (only `cache.db` was written to; the
  one deliberately-touched mtime was restored to its original value
  immediately after that test).

**Follow-up (2026-07-13, same day):** user asked to relocate the DB into
a `cache/` subfolder to keep the project root tidy. `CACHE_DB_PATH`
changed from `Path(__file__).parent / "cache.db"` to
`Path(__file__).parent / "cache" / "cache.db"`, with `CACHE_DIR.mkdir()`
added to `_cache_conn()` so the folder is created on first use. Old
root-level `cache.db`/`-wal`/`-shm` test artifacts removed. Verified:
fresh server run creates `cache/cache.db` correctly, and a real Mega CD
verify scan against the new location still returns 25/25 verified.

## Real-world PSX/CHD verify failure + MAME baddump false-positives (2026-07-14)

User ran the new chdman-based verify (see the multi-disc/CHD section
above) against all their real folders. All passed except
`D:\RetroBat\roms\psx\`: only 31% passed, 69% failed. Asked whether the
fails were bad dumps or a tool bug.

- [x] **DONE** (2026-07-14) — **PSX/CHD single-track verify bug,
      romtools.py:1989-2004 (Redump matching logic)**. Diagnosed by
      extracting 3 real failing PSX titles directly with `chdman` and
      hashing the result by hand: "Ace Combat 2 (USA)", "Ace Combat 3 -
      Electrosphere (USA)", and "In Cold Blood (USA) Disc 1" all
      hash-matched their Redump DAT entry **exactly** — proving these are
      good dumps and the failures were a code bug, not bad images.
      Root cause: `game_hashes` (built for Redump-format DAT matching)
      included the `.cue` sheet's hash alongside the real track hash(es)
      for each game. The exact-match path
      (`g_hashes.issubset(file_hash_set)`) requires *every* hash in
      `g_hashes` to be present in what was actually extracted/hashed from
      the CHD — but `chdman extractcd` only produces track data, never a
      `.cue` file, so `g_hashes` could never be a subset of
      `file_hash_set` for any single-track CHD once a `.cue` hash was
      mixed in. Multi-track games happened to still partially work via a
      different branch, which is why the failure rate wasn't 100%.
      Fix: `.cue` entries excluded from `g_hashes` when building
      `game_hashes` (both the DAT-parsing builder and the mirrored logic
      in `compare_scan()`), so single-track CHDs can hit the clean
      exact-match path instead of being blocked by a hash they can never
      produce. Verified against the real extracted/hashed data for all 3
      diagnosed titles: the fixed matching logic now passes cleanly for
      each.

- [x] **DONE** (2026-07-14) — **MAME "Bad Dump" false positives,
      romtools.py `verify_folders()` (~1740-1810, 2119-2132)**. User
      separately flagged that the MAME Bad Dump list looked suspiciously
      large. Confirmed with hard data: **864 distinct CRC hashes** in the
      MAME 0.278 DAT are flagged `status="baddump"` for one machine but
      are the normal, correct dump for a *different* (usually related
      clone) machine — e.g. `005`/`005a`, `40love`/`40lovej`. The old
      check matched a file's hash against a single flat set of every
      baddump hash in the whole DAT, so a good ROM in machine A got
      wrongly flagged bad just because the same chip CRC happened to be
      a known-bad dump in unrelated machine B.
      Fix: `parse_dat_for_verify()` (or equivalent DAT-parsing builder)
      now tracks `baddump_hashes` per-game (`{game_name: set(hashes)}`)
      instead of one global set; the verify loop records
      `matched_game_name` for each file as it's matched, then scopes the
      baddump check to only that specific game's flagged hashes
      (romtools.py:2119-2132). Verified: confirmed `baddump_hashes` is
      the only call site relying on the old flat-set shape (no other
      code needed updating).

## Verify-cache "Clear" buttons split by storage location (2026-07-14)

User noticed the ROM Scanner Verify page's "Clear Cached Results" button
looked like pre-SQLite legacy code (left over from before the SQLite
result cache shipped on 2026-07-13) and asked to make it clear the new
SQLite cache instead.

- [x] **DONE** (2026-07-14) — Investigated and found 3 separate
      "clear cache" entry points that had drifted inconsistent:
      Home page, Utilities → Settings, and ROM Scanner Verify. Also found
      genuine dead code: `window._verifyCache = null` in the Home page's
      `clearAllCache()` — set once, never read anywhere in the file,
      leftover from the pre-SQLite era.
      First pass wired the Home page button to also clear the SQLite
      cache; user asked to revert that and split strictly **by storage
      location** instead of by feature:
      - **Home page "🧹 Clear All Saved Data"** (`clearSavedData()`,
        base.html:193) — localStorage only (saved paths, preferences,
        remembered tab, verify-scan replay copy). Reverted the SQLite
        addition; dead `_verifyCache` line removed.
      - **Utilities → Settings "Clear Cache"** — entire SQLite cache
        (`/api/settings/cache/clear`) — already correct from the
        2026-07-13 cache feature, no change needed.
      - **ROM Scanner Verify "🗑 Clear verify cache"** button
        (base.html:1674) — SQLite-only, scoped to just that scan's
        verify-results rows via new backend `clear_verify_results_for(
        rompath, datroot)` (romtools.py:929, called from the
        `/api/dat/clear-verify-cache`-style route at romtools.py:437) —
        does not touch localStorage. Tooltip rewritten to accurately
        describe the new scope ("Clears this scan's server-side
        verify-results cache only (SQLite) - not file hashes or MD5 scan
        data. ... use Home → Clear All Saved Data for that.").
      Verified: grepped for stale references to the old combined
      `clearAllCache` behavior and the removed `_verifyCache` variable —
      none remain; the three buttons now cleanly correspond to
      localStorage / SQLite-all / SQLite-one-scan with no overlap.

---

## Media Cleaner false-positive orphans after scraper added new media types (2026-07-15)

User updated their (external) scraper to pull additional media types for
`D:\RetroBat\roms\dreamcast\` — files now include `-bezel.png`,
`-boxback.png`, `-fanart.jpg` alongside the existing image/marquee/
thumbnail/video/manual files, and `gamelist.xml` gained matching
`<bezel>`/`<boxback>`/`<fanart>` tags. Media Cleaner started reporting all
of these as orphaned.

- [x] **DONE** (2026-07-15) — Root cause: `RETROBAT_MEDIA_FIELDS`
      (romtools.py:142) only mapped `image/marquee/thumbnail/video/manual`
      to their subfolders. `get_xml_media_refs()` builds its "referenced"
      set purely from that map (via the shared `_walk_gamelist_media()`
      helper, romtools.py:174), so files tagged `bezel`/`boxback`/`fanart`
      in `gamelist.xml` were on disk and validly referenced, but never
      added to `xml_refs` — flagged as orphans. Fix: added `bezel`,
      `boxback`, `fanart` → `"images"` to `RETROBAT_MEDIA_FIELDS`
      (romtools.py:142-150), which also fixes `delete_unverified()`'s
      per-ROM media cleanup for free since it shares the same helper. Also
      added the same three fields to `RETROBAT_GAMELIST_FIELDS`
      (romtools.py:157-163) so Game Manager's full-field view stays
      consistent with what Media Cleaner now recognizes.
      Verified against real data (`D:\RetroBat\roms\dreamcast`, 1608 media
      files, 1797 xml media refs across `gamelist.xml`): orphan count was
      **598** before the fix (all `-bezel`/`-boxback`/`-fanart` files) and
      **0** after. Compared before/after via `get_xml_media_refs()` +
      `get_media_dirs()` + `scan_image_dir()` called directly against the
      real folder — read-only, no files modified. Note: 189 pre-existing
      "missing" entries remain, unrelated to this fix — about half of the
      dreamcast gamelist entries are stale, still using old Recalbox-style
      `media/images/<hash>.png` paths that don't resolve under this
      RetroBat layout (no `media/` folder exists on disk at all); not
      touched since the user didn't report that as a problem.

---

## Media Cleaner Type column added; Recalbox video/manual orphan false-positives fixed (2026-07-15)

Follow-up to the RetroBat media-types fix above. User asked to add a media
Type column to Media Cleaner's Orphans/Missing tables, then ran a fresh
scan of `O:\MYNAS\roms\dreamcast\` (Recalbox layout, full re-scrape) and
got `XML refs: 189 | Disk files: 566 | Orphaned files: 377 (across 2 media
folders)` — asked to validate whether that was a real problem or another
detection bug.

- [x] **DONE** (2026-07-15) — **Type column**: `get_xml_media_refs()`
      (romtools.py:228) now also returns `ref_types` (full path → XML
      field name), used to label each row in the Missing table
      (`media_scan()`, romtools.py). Added `guess_media_type()`
      (romtools.py:271) to infer a type for Orphans rows, which have no
      XML field to read from — first tries the RetroBat
      `<romname>-<type>.<ext>` filename suffix (with a `thumb`→`thumbnail`
      alias so both tables share vocabulary), falling back to the
      containing folder name (`images`/`videos`/`manuals` → `image`/
      `video`/`manual`) for layouts like Recalbox where the filename
      carries no type suffix at all. `templates/base.html`'s
      `mcRenderFolder()` renders the new column in both tables; left the
      "no image tag" list alone (single-purpose, always `image`).

- [x] **DONE** (2026-07-15) — **Recalbox orphan false positives,
      validated against real data**: reproduced the user's exact numbers
      (566 disk / 189 xml refs / 377 orphans) directly against
      `O:\MYNAS\roms\dreamcast\gamelist.xml`. Found two separate causes:
      1. **Videos (188 false positives)** — same shape as the RetroBat
         bug: `RECALBOX_MEDIA_FIELDS` (romtools.py:154) only mapped
         `image` → `media/images`; `video` was missing even though every
         one of the 202 `<game>` entries has a valid `<video>` tag and the
         file exists on disk. Fix: added `"video": "media/videos"`.
      2. **Manuals (189 false positives)** — not a mapping gap: confirmed
         `<manual>` appears **zero** times anywhere in this gamelist.xml
         (`grep` count). Classic Recalbox/EmulationStation's gamelist
         schema has no manual field at all; the scraper still drops PDFs
         into `media/manuals/` using the same `<title> <hash>.ext`
         convention as images/videos, just never references them from the
         XML. Asked the user how to treat these
         (AskUserQuestion) — chose to treat a manual as valid if its title
         prefix matches a game that *is* referenced via another field,
         rather than flagging every Recalbox manual as orphaned. Added
         `_media_base_name()` (romtools.py) to strip the `<hash>.<ext>`
         suffix and recover the shared title prefix, and
         `get_xml_media_refs()` now also returns `media_base_names` (every
         referenced file's title prefix). `media_scan()`'s orphan loop
         special-cases `resolved == "recalbox"` + parent folder `manuals`:
         skip if the file's title prefix is in `media_base_names`.
      Verified against real data: orphan count went from **377 → 0** for
      `O:\MYNAS\roms\dreamcast\` (566 disk files, xml_refs grew from 189
      to 377 once video was mapped; all 189 manuals matched by title
      convention). Also verified the negative case with a disposable
      synthetic fixture (temp dir, 1 game with image+video only, one
      matching manual + one `UnrelatedGame` manual with no game entry):
      only the unmatched manual was flagged as orphan, confirming the
      convention-matching doesn't just rubber-stamp every manual file.
      Fixture deleted after the test — no changes to real ROM/media data.
      Also fixed `guess_media_type()`'s suffix heuristic misfiring on
      Recalbox filenames (game titles routinely contain dashes, e.g.
      "Alone in the Dark - The New Nightmare", which broke the RetroBat-
      style `rsplit("-", 1)` suffix extraction) by validating the guessed
      suffix against a known-media-types set before trusting it, falling
      back to the folder-name mapping otherwise.

---

## Media Cleaner: "Remove gamelist entries" button for No-image-tag / Missing lists (2026-07-15)

User asked for a delete-style button on Media Cleaner's "ROMs with no
image tag" and "image(s) in XML but missing from disk" detail lists,
mirroring the existing Orphaned-files delete button — but since there's no
stray file to delete for these two (the problem is a stale/incomplete XML
entry, not an extra file on disk), the action removes the `<game>` block
from `gamelist.xml` instead, backed up first the same way every other tab
already does.

- [x] **DONE** (2026-07-15) — Reused `remove_gamelist_entries()`
      (romtools.py, already used by Game Manager/Compare/delete-unverified)
      unchanged — same `gamelist.xml.<timestamp>.bak` backup convention,
      no new removal logic needed. Added:
      - `get_xml_media_refs()` now also returns `ref_rom_paths` (full
        media path → owning game's raw `<path>` text) so a "missing"
        entry can be traced back to which ROM to remove.
      - `missing` list entries in `media_scan()` gained a `rom_path` field.
      - New route `/api/media/remove-gamelist-entries` — body
        `{system_path, xml_file, files: [rom_path,...]}` — thin wrapper
        around `remove_gamelist_entries()`.
      - `templates/base.html`: new `mcRemoveEntriesButton()` /
        `mcConfirmRemoveEntries()`, wired through the same
        `confirmAction()` double-click-confirm helper the Orphans delete
        button uses. The Missing list's button dedupes by `rom_path`
        first (one ROM can have several missing media types — bezel +
        boxback + fanart — but should only be removed once).
      Removing the `<game>` entry does not touch any of that ROM's media
      files — if it had other valid media, that becomes newly orphaned on
      the next scan, which is expected (button only touches the XML).
      Verified with a disposable fixture (temp dir, 3 games: one with no
      `<image>` tag, one with an `<image>` tag pointing at a nonexistent
      file, one fully valid control) — called the removal logic directly:
      both stale entries removed, the valid control entry untouched, and
      a timestamped `.bak` containing the exact original 3-entry file was
      created first. Also called the live route
      (`POST /api/media/remove-gamelist-entries`) against the already-
      running dev server (auto-reload picked up the new route with no
      restart) with a second disposable fixture — same result via real
      HTTP. Both fixtures deleted after testing; no real ROM/gamelist data
      touched.

---

## Verify page: Missing / Missing-Filtered → modal popup with DAT columns (2026-07-15)

User asked to replace the Missing and Missing-Filtered lists' in-row
`<details>` expansion (messy at scale — a full MAME/Redump set can have
thousands of entries) with a modal popup showing the DAT's own columns
alongside the name, plus its own CSV export. Also asked to remove the
page-level "Export Detailed CSV" button. I asked which DAT columns to
include and whether removing Detailed CSV (which also serves Unknown/
Baddump/Wrong-name) was acceptable; the question was declined ("proceed"),
so I went with the literal reading — see decisions below.

- [x] **DONE** (2026-07-15) — **Backend**: new `load_dat_game_details()`
      (romtools.py) parses `description`/`category`/summed `size`/
      `crc`/`md5`/`sha1` per game, reusing `_parse_dat_cached()` +
      `find_game_elements()` (same pattern as `load_dat_hashes`/
      `load_dat_descriptions` - no new parsing infra). `crc`/`md5`/`sha1`
      only populate when a game has exactly one `<rom>` - multi-track
      Redump games (Dreamcast/PSX/Saturn, one `<rom>` per CD track) have
      no single representative hash, so those stay blank; `size` is
      summed across all tracks instead. `verify_folders()` now also loads
      `game_details` and attaches a parallel `missing_details` list to
      `folder_result`, riding along in the existing SQLite cache for free
      since it's part of the same cached dict.
      Verified against two real DATs in `DatRoot\`: `Atari - Atari 2600`
      (single-rom) returned full size/crc/md5/sha1; the Dreamcast Redump
      DAT's "Mortal Kombat Gold (Europe)" (53 tracks) returned summed
      `size: 1188782547` and blank hashes, exactly as designed. Also hit
      the live `/api/dat/verify-folders` SSE route for the real
      `D:\RetroBat\roms\dreamcast` + Dreamcast DAT and confirmed
      `missing_details` streams correctly shaped entries end-to-end.
- [x] **DONE** (2026-07-15) — **Frontend**: `scanRenderFolderResult()`
      now stashes each folder's full result on
      `window._verifyFolderData` (works for both live SSE and
      localStorage-replayed results, since both call this function) so a
      modal opened later can look up `missing_details`/`owned_names`. The
      two `<details>` blocks became a one-line count + "🔍 View details"
      button each (same `data-folder`+`onclick(this)` pattern as the
      existing Unknown/Baddump delete buttons in the same function). New
      `#vr-modal-overlay`/`#vr-modal` (styled like the existing
      `#fb-overlay` folder-browser modal) shows Name/Description/
      Category/Size/CRC32/MD5/SHA1 for either list (Missing-Filtered
      reuses the existing `verifyFilterMissing()` helper client-side), with
      its own `⬇ Export CSV` via the existing `downloadCsvRows()` helper.
      `missing_details` added to `scanCacheTrimEvt()`'s capped-keys list
      so it trims in step with `missing_list` under the pre-existing
      500-entry localStorage cap (unrelated to this feature - same cap
      discussed earlier this session).
      Page-level "Export Detailed CSV" removed entirely:
      `scanEnsureVerifyCsvBtn()` no longer creates the detail button, and
      `scanExportVerifyCsv(includeDetails)` simplified to a parameterless
      `scanExportVerifyCsv()` (summary-only). Unknown/Baddump/Wrong-name
      keep their in-row `<details>` unchanged but lose CSV export, per the
      declined clarifying question - flagged as a known tradeoff, not
      silently dropped.
      Verified: fetched the live running page (`GET /`, 200 OK) and
      confirmed the new modal markup/JS (`vr-modal-overlay`, `vr-modal-tbody`,
      `vrOpenMissingModal`, `vrExportMissingModalCsv`) is present and the
      old "Export Detailed CSV" text/`scan-verify-csv-detailed` id/
      `includeDetails` are completely gone from the served output. Could
      not visually click through in a browser - no Playwright/chromium-cli
      in this environment and installing a full browser stack for one
      check was disproportionate for this personal tool - so this was
      verified by code inspection + live server fetch, not a screenshot.
      User should smoke-test the modal (open it, check columns, export
      CSV) on next real use.

---

## Missing modal: ROM File column + CloneOf/RomOf columns; Missing-Filtered review (2026-07-15)

Follow-up to the Missing-list modal above. User asked for (1) a ROM
filename column, noting some games (Redump multi-track discs) have many
rom files per game while the modal must stay one-row-per-game, and (2) a
review of the Missing-Filtered dedup logic, plus (3) adding
`cloneof`/`romof` columns (same fields `load_dat_clone_info()` already
surfaces for the Duplicates tab) to the report.

For (1) I proposed three options (primary-file-only+count, full
semicolon-joined list, blank-for-multi-file) and flagged that Dreamcast's
DAT filenames are raw Redump format (`.cue`/`.bin`) which don't match the
user's actual on-disk `.chd` files - user picked **option 1** and
confirmed the DAT-as-written display (not a derived `.chd` name).

- [x] **DONE** (2026-07-15) — **ROM File + count column**:
      `load_dat_game_details()` (romtools.py) now also returns `rom_file`
      (preferring `.cue`, then `.iso`/`.m3u`, then the first `<rom>` in DAT
      order) and `rom_count`. Modal shows `<rom_file> (+N more)` for
      multi-file games. Verified against real DATs: Dreamcast's "Mortal
      Kombat Gold (Europe)" → `Mortal Kombat Gold (Europe).cue`, count 54
      (53 tracks + cue); FBNeo's `88games` (36-chip arcade set, no
      cue/iso/m3u) → falls back to first rom `861m01.k18`, count 36.
- [x] **DONE** (2026-07-15) — **CloneOf/RomOf columns**: `verify_folders()`
      now also calls the existing `load_dat_clone_info()` (already used by
      the Duplicates tab's clone-parent grouping, romtools.py:1827) and
      merges `cloneof`/`romof` into each `missing_details` entry - no new
      parsing logic, same function/values used elsewhere in the app.
      Verified: FBNeo real DAT shows `99lstwarb`/`99lstwark`/`99lstwar`
      all clone `repulse`, matching what Duplicates already resolves.
      Both fields also re-verified through the live `/api/dat/verify-folders`
      SSE route (had to clear a stale SQLite-cached result from an earlier
      test first - signature-based caching doesn't invalidate on code
      changes, only on ROM/DAT file changes).
- [x] **DONE** (2026-07-15) — **Missing-Filtered review, real finding**:
      `verifyFilterMissing()` (base.html) groups missing/owned entries
      purely by `dupParseFile(name).base` - stripping parenthetical
      region/revision tags. That works for No-Intro/Redump naming
      (`Game (USA) (Rev A).zip` groups with `Game (Europe).zip`) but MAME/
      FBNeo names have no parenthetical tags at all (`99lstwar` vs
      `99lstwarb` vs `99lstwark`), so they never share a `base` string and
      are **never grouped** - unlike the Duplicates tab's `dupAnalyse()`,
      which walks `cloneof`/`romof` via `cloneGroupRoot()` (base.html:2786)
      specifically to group MAME clone/parent sets before this same
      base-name fallback. Net effect: for arcade DATs, Missing-Filtered
      can report a clone AND its parent as two separate "missing" entries
      instead of picking the one best variant, and won't recognize that
      owning one clone satisfies "you already have this game" for a
      sibling clone. Confirmed via code reading + the real FBNeo clone
      chain above (99lstwarb/99lstwark/99lstwar → repulse; none of the
      four share a parseable "base" today). Not fixed yet - this is a
      filtering *behavior* change (not just adding a report column), so it
      needs the same clone-grouping logic ported from `dupAnalyse()` into
      `verifyFilterMissing()`; flagged for the user to confirm before
      changing what Missing-Filtered actually returns.

---

## Missing modal CSV filename includes system name (2026-07-16)

User asked for the Missing/Missing-Filtered modal's CSV export filename to
include the system/folder name instead of the generic `dat_verify_missing.csv`
for every folder.

- [x] **DONE** (2026-07-16) — `vrOpenMissingModal()` (base.html) now also
      stashes `window._vrModalFolder`/`window._vrModalFiltered` alongside
      `window._vrModalRows`; `vrExportMissingModalCsv()` sanitizes the
      folder name (strips `\/:*?"<>|`) and builds
      `dat_verify_missing_<folder>.csv` / `dat_verify_missing_filtered_<folder>.csv`.
      Verified live against the running server (`GET /`, 200) and by
      confirming `_vrModalFolder`/`_vrModalFiltered` are set on the only
      code path that populates `_vrModalRows`.

## Third full audit pass (2026-07-16) — after CHD/cache/media-cleaner/modal work

User asked for a fresh full re-audit given the volume of changes since the
second pass (2026-07-13): CHD verification via chdman, the SQLite result
cache, the PSX/CHD `.cue`-hash bug fix, the MAME baddump per-game scoping
fix, the three-way Clear Cache split, the RetroBat/Recalbox media-field
fixes, the Missing/Missing-Filtered modal, and today's CSV-filename change.
Two agents each read one full file (romtools.py, base.html) end to end,
primed with everything changed since the last pass.

**Frontend (base.html): no defects found.** All the areas re-verified
(three Clear Cache buttons' scope separation, Media Cleaner Type column +
Remove-From-Gamelist dedup-by-`rom_path`, the Missing modal's removal of
the old inline `<details>` UI and the page-level Export Detailed CSV
button, CSV export routing through the shared `downloadCsvRows()` BOM-safe
helper, today's `_vrModalFolder`/`_vrModalFiltered` addition) checked out
clean. One note from the audit brief's premise turned out moot: the
capped-500-entry localStorage trim list (`scanCacheTrimEvt()`) referenced
in the pass-2 log no longer exists in the current code — verify-scan
results now live only in `window._verifyFolderData` (in-memory) backed by
the server-side SQLite cache, which has no size cap, so there's no
localStorage key list for `missing_details` to have been left out of.

**Backend (romtools.py): two real bugs found and fixed.**

- [x] **DONE** (2026-07-16) — **Unhandled `struct.error` crash in
      `detect_chd_track_format()`** (romtools.py:1382-1427, added during
      the CHD feature work on 2026-07-13). The function wraps its body in
      `try/except OSError`, but `struct.error` is not an `OSError`
      subclass, and two of its three `struct.unpack()` calls
      (romtools.py:1404, 1408) have no length check before them. Any
      `.chd`-extension file that's truncated or malformed (incomplete
      download, corrupted file, a misnamed placeholder) reaching either
      unpack call raises an uncaught `struct.error` instead of returning
      `None` like every other unrecognized-format case. Reachable from
      `sample_chd_verifiable()` → `/api/dat/scan-overview` (would 500 the
      whole overview scan for that ROM root) and `hash_chd_file()` →
      `hash_rom_file()` → the per-file loop inside
      `/api/dat/verify-folders`'s SSE generator (would kill the SSE stream
      mid-verify).
      Fix: `except OSError:` → `except (OSError, struct.error):`.
      Verified: reproduced the exact crash with two synthetic truncated
      `.chd` files (one just the 8-byte magic, one truncated right after a
      valid v5 header) — both raised `struct.error` before the fix, both
      cleanly return `None` after. Confirmed no regression against 3 real
      Mega CD `.chd` files from `D:\RetroBat\roms\megacd` — all still
      correctly classify as `cdrom`.

- [x] **DONE** (2026-07-16) — **Stale `verify_results` cache survives a
      chdman path change**. `compute_verify_signature()`
      (romtools.py:1075) fingerprints only ROM-file and DAT-file
      name/size/mtime — it has no dependency on `config.json`'s
      `chdman_path` or whether chdman is currently resolvable. Scenario:
      user runs DAT Scanner Verify on a CHD-based system before chdman is
      configured (every CHD comes back `unknown` since `hash_chd_file()`
      returns `[]` with no chdman path) — that result gets cached under a
      signature based purely on the untouched ROM/DAT files. User then
      sets a working chdman path in Settings and re-runs Verify on the
      same folder: the signature is unchanged, so the stale all-unknown
      result is served from cache instead of re-verifying, with nothing
      surfaced to the user that a config change should invalidate it.
      (The per-file `file_hashes` cache layer was checked and is NOT
      affected — `hash_rom_file()` only writes to that cache when
      `results` is non-empty, so an unconfigured-chdman `[]` never gets
      cached there.)
      Fix: `/api/settings` POST (`save_settings()`, romtools.py:478) now
      clears the `verify_results` table (new `clear_cache_table()` helper,
      factored out of the existing `clear_cache()`'s per-table delete)
      whenever the submitted `chdman_path` actually differs from the
      stored value; a no-op save (same value) leaves the cache untouched.
      Verified live against the running server and the real cache
      (22 real cached `verify_results` rows at the time of the test):
      POSTing the same `chdman_path` back left the count at 22; POSTing a
      different path dropped it to 0; the real chdman path was then
      restored via a final POST, confirmed via the returned
      `chdman_resolved` field.

---

## Verify tab showing empty/no cached results after audit-pass testing (2026-07-16)

User reported that the ROM Scanner Verify page stopped loading past cached
results — only folders scanned fresh in the current session showed up —
with no cache-clear action taken on their end. They flagged a possibly
unrelated change (added a new DAT file to the mapping, not yet mapped to
any ROMs) as the only thing they'd changed.

- [x] **DONE / ROOT CAUSE: not a bug, an artifact of my own testing** —
      Verifying the "stale verify_results cache on chdman_path change" fix
      immediately above was done by POSTing to the real, running
      `/api/settings` endpoint three times (same-value no-op, a bogus
      path to prove the clear-on-change branch, then restoring the real
      path) to prove the invalidation worked. Step 2 correctly cleared
      `verify_results` (working as designed); step 3 — restoring the
      original path — is *also* a value change from the bogus path back
      to the real one, so it triggered a second clear. Net effect: the
      real `verify_results` table (22 real cached folder results at the
      time) was left at 0 rows on the user's actual running server/cache,
      not a throwaway copy.
      Not related to the new unmapped DAT file — mapping.json changes for
      an unrelated folder don't touch other folders' cache signature
      (`compute_verify_signature()` is scoped to that folder's own mapped
      DAT files only, confirmed by reading `verify_folders()`'s per-folder
      `dat_files = [dat_root / d for d in mapping.get(folder, [])...]`
      lookup).
      No data lost: `verify_results` only caches scan *results* to skip
      re-hashing; ROM/DAT/gamelist files were never touched. Cache
      repopulates automatically as each folder is re-verified (cold-run
      timing until then, same as any first-ever scan).
      **Process lesson, applied going forward:** verifying a cache-clear /
      cache-invalidation code path against the live server must snapshot
      `cache/cache.db` (or point `CACHE_DB_PATH` at a temp copy) first
      instead of exercising the real endpoint directly — this project's
      cache is real user state, not disposable like the temp ROM/gamelist
      fixtures already used elsewhere in this log for delete/rename
      testing.
