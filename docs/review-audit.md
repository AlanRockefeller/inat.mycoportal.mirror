# PR #1 review audit

Audited against PR head `6fc21bc9cf88c8b79a5f72bd406f7a090ba7d60f` and the existing local changes. GitHub showed 29 inline threads: 20 resolved and 9 unresolved. The unresolved `get_single_obs` diagnostic issue was already fixed in the committed code; the other eight had local changes that needed verification or further fixes.

The table accounts for all 29 inline threads. “Verified existing fix” refers to code already in PR #1; “Fixed” refers to the audited changes prepared for its replacement. GitHub thread resolution flags have not been changed.

| Review concern | Outcome |
| --- | --- |
| Unbounded backoff | Verified existing cap for every platform. |
| Shared request layer exits successfully on failure | Verified catchable `MaxRequestAttemptsExceeded`. |
| Leading quote in dictionary | Verified `Slime` entry. |
| Example says “Loading image” | Verified “Uploading image” throughout. |
| Blocking OAuth socket receive | Verified remaining-time socket timeout; regression tested. |
| Undefined ROPC names | Verified undefined names were removed; interactive authorization uses PKCE. |
| Recursive PKCE retries | Verified failure returns rather than recursive calls. |
| Radius calculation uses degrees with sine | Verified cosine of radians; regression tested. |
| JWT timestamp not loaded globally | Verified global assignment; regression tested. |
| RAW image filename replacement | Verified `.raw` becomes `.jpg`; regression tested. |
| Top-level crash reports success | Verified nonzero exit; batch failures now also return nonzero. |
| `get_full_obses` writes strings in binary mode | Verified UTF-8 JSON diagnostic output. |
| [`get_single_obs` undefined diagnostic variable](https://github.com/AlanRockefeller/inat.mycoportal.mirror/pull/1#discussion_r2802110870) | Verified existing `answer` serialization; regression tested. Thread was stale. |
| Species list failure returns `None` | Verified empty list; regression tested. |
| Image limit counts failed attempts | Verified successful downloads count toward limit; regression tested. |
| PKCE insecure random generator | Verified `secrets.choice`; removed unused `random` import. |
| Oversized year query | Verified batches of at most 20 years; regression tested. |
| Observation creation hard exits | Raises a catchable error; additionally removed ambiguous creation retries. |
| iNat observation lookup assumes results | Verified empty/malformed response handling; regression tested. |
| Completion recorded after removing incomplete | Verified completion first; cleanup now preserves completed observations after an interrupted transition. |
| MO observation lookup assumes results | Verified empty/malformed response handling; regression tested. |
| [`post_tag` assumes valid results/tags](https://github.com/AlanRockefeller/inat.mycoportal.mirror/pull/1#discussion_r2805701302) | Fixed malformed results, null/invalid tags, and repeated tags. |
| [Proposal lookup hard exits](https://github.com/AlanRockefeller/inat.mycoportal.mirror/pull/1#discussion_r2805701306) | Fixed catchable errors; caller keeps failed observation incomplete. |
| [Settings writer reloads globals](https://github.com/AlanRockefeller/inat.mycoportal.mirror/pull/1#discussion_r2805701313) | Fixed pure settings reader; regression tested against stale saved JWT. |
| [Missing completes file on first run](https://github.com/AlanRockefeller/inat.mycoportal.mirror/pull/1#discussion_r2805701316) | Fixed and regression tested. |
| [Missing incompletes file on first run](https://github.com/AlanRockefeller/inat.mycoportal.mirror/pull/1#discussion_r2805701318) | Fixed and regression tested. |
| [Duplicate observation after uncertain creation](https://github.com/AlanRockefeller/inat.mycoportal.mirror/pull/1#discussion_r2805701324) | Fixed at both request and orchestration layers; persistent pending state prevents blind reruns. |
| [MO image download returns `None`](https://github.com/AlanRockefeller/inat.mycoportal.mirror/pull/1#discussion_r2805701330) | Fixed skip handling and caption alignment; regression tested. |
| [Long MO notes encoded in PATCH URL](https://github.com/AlanRockefeller/inat.mycoportal.mirror/pull/1#discussion_r2805701335) | Fixed form body with no notes or credentials in PATCH URL; regression tested. |

Review summaries also contained repeated cleanup suggestions. The replacement uses a `None` default for URL parameters, removes unused `confirm_json` and commented test invocations, returns booleans from `bad_API_key` with narrowed exceptions, uses shared image retries and taxonomy resolution, and closes file handles with context managers.

## Additional audit fixes

- Accept successful HTTP 201/204 responses and do not treat failed authenticated writes as successful updates.
- Send JSON writes with the documented content type; preserve OAuth form and multipart image bodies.
- Preserve incomplete status after field, photo, or notes failures; skip retained incomplete imports on reruns.
- Keep unknown creation outcomes tracked even when the search index has not caught up or a process is interrupted.
- Write incomplete tracking atomically; remove cleanup rows only after each successful deletion and protect completed observations from stale incomplete entries.
- Preserve a stable occurrence marker in final notes and distinguish occurrence IDs that share a numeric prefix.
- Handle first-run log files, invalid dates/coordinates/uncertainty, and headerless CSVs beginning with a URL.
- Bind OAuth callbacks to a random state and send the PKCE verifier in the token request body.
- Add usage/recovery instructions, dependency declaration, runtime-file exclusions, and a Python 3.9/3.14 CI matrix.

## Validation and limits

Run `python3 -m unittest discover -s tests -v`. The suite blocks real HTTP requests and isolates local tracking files. Compilation, CLI help, and whitespace checks also pass locally on Python 3.14. Python 3.9 execution is delegated to CI.

No live observations were created, updated, or deleted during validation. A newly opened PR should receive a fresh CodeRabbit review before merging. One importer process per working directory is supported; the tracking files are not a concurrent job queue. Unknown creation outcomes require checking iNaturalist before clearing tracking, because a missing search result is not proof that a POST failed.
