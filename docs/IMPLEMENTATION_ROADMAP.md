# Local-first implementation roadmap

**Version:** 1.1 | **Development branch:** `main` ONLY | **Deployment:** deferred until local completion.

Phases govern delivery, not the order of independently editable setup tabs.

| Phase | Scope | Status and acceptance |
|---|---|---|
| 0 | Architecture, scope, engineering rules and clean `main` foundation | **Completed** |
| 1 | PostgreSQL, migrations, API, first-party staff login, organiser settings, React UI, live tab updates and worker | **In progress.** Staff role editor added, plus authenticated UI and 5-second polling; still missing full staff account creation UI, durable worker, SSE and real multi-browser acceptance |
| 2 | CSV ingestion, People & Teams, QR-assisted attendance and table assignment | **Partially implemented.** CSV/team/people screens and manual identity-confirmed check-in (with audit and reversal) implemented; QR scanning and table assignment remain |
| 3 | Configurable ticket categories, bulk grants, soft limits, physical issuance, redemption and activity rewards | **In progress.** Categories, recipient previews, signed ledger, allocation/issue/redemption, soft warnings, idempotent confirmations and safe reversal implemented; activities, prizes, advanced reward flows remain |
| 4 | Native Team 125980 connection, encrypted credential handling, member/role sync and explicit native admin operations | Not started; team/workspace settings persisted but not verified |
| 5 | Resumable ZIP/folder upload, video metadata, fair whole-video distribution and logical prepared jobs | Not started |
| 6 | Pair collaboration proof, native labeling-job lifecycle, launch, submission detection, completeness, correction, automatic/manual release | Not started |
| 7 | Deterministic sampled frame/keypoint/box scoring and human-review dashboard | Not started |
| 8 | Closure, partial annotation export, duplicate provenance, recovery, load testing | Not started |

## Gates for continuing
- First prove the local server, login, migrations, React build and staff permission tests work together; never call a partial milestone production-ready.
- Add a true multi-user test and a durable event-driven notification mechanism before introducing real-time operations such as ticket issuance.
- Never claim that copying email addresses, marking sent, or entering a Supervisely Team ID confirms native membership.
- Validate all external Supervisely mutation APIs on authorised real accounts before enabling event-side effects.
- Current GitHub repository is public; no real rosters, credentials or event data may enter commits.
- Existing branches are read-only references. Do not create another branch, merge old history into `main`, or force-push.
- Hosting/deployment work is postponed until the local application passes the required acceptance tests.

## Reference material
See `PRODUCT_SPEC.md`, `ARCHITECTURE.md` and `ENGINEERING_RULES.md`.
