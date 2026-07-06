# claude.md

## 0. Rules for future updates (mandatory)

Any future edit to this file must be short and to the point. Do not write debugging narratives. Do not write incident retrospectives. Do not write stories like "we first thought X, then discovered Y." Only write the conclusion and the current state.

Follow this format when you append an update record:

`[Module name] Reason: short explanation. Changes: 1. 2. 3.`

Example:

`[Claude quota] Reason: Users said the quota was too low. Changes: 1. Changed CLAUDE_FREE_TIER_LIMIT from 10 to 20. 2. Updated the frontend dialog text to match the new number.`

After you add a new entry, check whether this document has grown too long again. If a history entry is older than 3 months, or the feature it describes is now fully stable, delete the details and keep only the final conclusion, folded into the relevant numbered section above section 14. This document is an operations manual for future Claude sessions, not a project log.

未来更新规范：后续任何修改必须简明扼要。请遵循以下示例格式：

[模块名] 更新原因：简短说明。调整内容：1. 2. 3.

## 1. System overview

This is a Flask web app that aggregates and compares large language models. A user types a prompt. The system calls several g4f providers at the same time. It shows each provider's answer and response time. Then successful answers can be peer-reviewed, by each other and by the frontier providers, through a separate scheduler.

The system supports three identity states: anonymous visitor, guest, and logged-in user. A logged-in user's conversation history is stored in Firestore. The user manages it from the Recents sidebar on the left, which supports grouping, pagination, pinning, renaming, and deleting. A guest's history lives only in the browser's sessionStorage. It disappears when the tab closes.

The system also supports text-to-image comparison, through a fully separate g4f call chain. Image generation results are stored in their own `image_history` collection, with their own Recents sidebar. Only logged-in users can use this feature. Guests and anonymous users cannot use it at all.

Beyond the free g4f providers, the project wires up five frontier (paid, official-SDK) providers, each its own independent call chain: Claude (chat), ChatGPT (chat and image), and Gemini (chat and image, the image tier is the "Nano Banana" series). These are the only providers that cost real money, so each has its own quota counter and abuse prevention, much stricter than the free g4f providers. See section 6 for the exact rules.

There is a Trial Quota badge at the top of the navbar, one pill per frontier provider per mode (chat mode shows Claude/ChatGPT/Gemini-chat; image mode shows Gemini-image/ChatGPT-image). After each call to any frontier provider, the frontend asks the backend for the real quota number again. It never guesses the number locally.

A user can click Stop Generating mid-flight to cancel any in-progress compare/generate/frontier request; an in-memory, TTL-evicted cancellation registry and a matching quota-refund ledger make sure a cancelled request neither writes into history nor keeps a consumed quota unit.

The backend is built with Flask plus a Blueprint (`auth/`). The frontend uses Jinja2 plus plain JavaScript, with no frontend framework and no build tool. Overall page scale is controlled by the `--page-zoom` variable on `:root`, currently set to 0.88.

## 2. Architecture map

The system has three parts: the Flask backend, the Firebase auth module, and the HTML5/JS frontend.

### Backend (`main.py`)

Routes fall into these groups: page routes (`/`, `/home`, `/history/<id>`, `/image-history/<id>`, `/apikey-config`), g4f chat API (`/api/providers`, `/api/compare`, `/api/compare/cancel`, `/api/test-single`), cross-namespace peer review (`/api/peer-review`), frontier chat APIs (`/api/claude-chat`, `/api/chatgpt-chat`, `/api/gemini-chat`, each with a `/refund` sibling), frontier image APIs (`/api/gemini-image`, `/api/chatgpt-image`, each with a `/refund` sibling), quota query API (`/api/quota-status`), text-to-image API (`/api/image-providers`, `/api/generate-images`, `/api/generate-images/cancel`), the static file route for generated images (`/media/<filename>`), auth API (`/api/auth/guest`), chat history API (the `/api/history` group), and image history API (the `/api/image-history` group, logged-in users only).

Concurrent scheduling uses `ThreadPoolExecutor`, which calls several g4f providers at the same time so one slow provider does not hold up the rest. The text-to-image route reuses the same scheduling skeleton, but it only has one stage.

On the g4f side there are two fully separate call chains: `g4f.ChatCompletion` handles text chat, and `g4f.client.Client().images.generate()` handles text-to-image. The model matching logic and the exception handling of the two chains share nothing. When g4f generates an image, it automatically downloads the image into the local `get_media_dir()` folder, and the `url` field it returns is a relative path like `/media/<filename>?url=...`. This is a routing convention from g4f's own bundled GUI server, but this project does not run that server, so we added our own `GET /media/<filename>` static file route (`serve_generated_media`) to serve these files. `get_media_dir()` and g4f's own `images_dir`/`media_dir` module globals are redirected at import time to `tempfile.gettempdir()`, because GAE Standard's local filesystem is read-only everywhere except `/tmp`; a relative path like `./generated_media` works locally (masking the bug) but fails 100% of the time in production. These files are not cleaned up automatically by age; they are only deleted together with their `image_history` record when the user explicitly deletes it. Before that deletion, the files just keep accumulating during normal use. This is an accepted, deliberate tradeoff in exchange for being able to view historical images forever.

Five frontier call chains run fully independent of g4f and of each other: `call_claude_model()` (Claude chat, official `anthropic` SDK), `call_chatgpt_model()` (ChatGPT chat, official `openai` SDK), `call_gemini_text_model()` (Gemini chat, official `google-genai` SDK), `call_gemini_image_model()` (Gemini image), and `call_chatgpt_image_model()` (ChatGPT image). None of them enter the `ThreadPoolExecutor` g4f scheduler or reuse a g4f mapping table. Each is shown on the frontend as its own provider card, with its own class, its own model dropdown, and its own quota pill. When the user clicks Compare/Generate, the frontend sends the normal g4f request first (unless Frontier-only mode is on, see section 6), then one extra request per checked frontier provider, and merges each result into the same rendered list as it arrives.

Persisting frontier results into history: every frontier chat/image endpoint accepts an optional `history_id` field in the request body. As long as the call actually happened (not blocked by quota, not cancelled), the result is appended to the existing history record for that `history_id`, whether the call succeeded or failed. The functions that do the appending are `append_chat_history_result()`/`append_image_history_result()` in `auth/db.py`, plus thin per-provider wrappers in `main.py`. They check ownership before appending, and after appending they re-sort the whole results array using "success first, then shorter response time first." These functions can only append to a record that already exists; they cannot create one. The only entry points that create a new record are the g4f chain's `save_chat_history()`/`save_image_history()` (called even in Frontier-only mode, producing an empty-results record for later frontier calls to append to).

Image results whose payload arrives as base64 (Gemini image, ChatGPT image) are never written to Firestore as-is: `_persist_image_result_local_copy()` decodes `b64_json` to a local file under `get_media_dir()` and swaps it for a `url` before appending, because an embedded base64 string over roughly 1MB makes Firestore reject the whole write ("invalid nested entity"). If the local write itself fails, this function returns a small failure result instead of falling back to the oversized original, so the history append always succeeds (as a visible failure) instead of the entry silently vanishing from history.

Peer review is a separate concern from the g4f compare/generate stage. `run_cross_peer_review()`, reached through `POST /api/peer-review`, is a standalone scheduler the frontend calls once every checked provider (g4f and frontier) has returned, reviewing every successful result against every other successful result regardless of namespace. See section 6 for trigger and reliability rules.

### Auth subsystem (`auth/`)

`auth_bp` is mounted at the root path, with no prefix: `/login`, `/register`, `/logout`, `/profile`. When `auth/db.py` starts, it tries to connect to Firebase Firestore. If it cannot connect, it sets `FIREBASE_AVAILABLE` to `False`, and the auth routes then return 503 instead of crashing. User identity is carried between requests by Flask's `session`, keyed by the `SECRET_KEY` environment variable.

### Frontend (Jinja2 + JS)

The navbar has three states, switched based on `session.user_id`/`is_guest`. Both `auth/base.html` and `index.html` each keep their own copy of this logic. All communication with the backend goes through the Fetch API and is non-blocking.

## 3. Tech stack

The languages are Python and JavaScript. The backend framework is Flask, with concurrency handled by `concurrent.futures.ThreadPoolExecutor`. Core dependencies are g4f, firebase-admin, python-dotenv, and three official provider SDKs: `anthropic` (Claude), `openai` (ChatGPT), and `google-genai` (Gemini, imported as `from google import genai`; do not confuse it with the deprecated `google-generativeai` package).

Auth uses Werkzeug's password hashing functions plus Flask's `session`. The database is Google Cloud Firestore, accessed through the Firebase Admin SDK. The frontend is plain HTML5, CSS3, and vanilla JS, with no framework and no build tool. The template engine is Jinja2. The deployment platform is Google App Engine (Standard, python312 runtime); its local filesystem is read-only outside `/tmp`, which is why all locally-written media goes through `tempfile.gettempdir()` (see section 2).

Environment variables: `SECRET_KEY` and `PORT` are basic config, loaded locally from a `.env` file via python-dotenv. `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, and `OPENAI_API_KEY` are the developer's default keys for the three frontier SDKs. The app starts fine without any of them; a call only fails when it actually needs the missing key. `GEMINI_API_KEY` is the one exception: `google_genai.Client()` checks it immediately at construction time and raises `ValueError` right away if missing, instead of waiting for an actual call — the user-facing outcome is the same either way (the request fails, nothing else is affected).

## 4. Code layout

```
llm_aggregator/
├── main.py                  # Flask entry point, all routes
├── auth/
│   ├── __init__.py          # auth_bp blueprint
│   ├── db.py                 # Firebase init, user CRUD, history CRUD, quota counters
│   └── routes.py             # /login /register /logout /profile
├── templates/
│   ├── home.html             # the only entry point for logged-out, non-guest visitors
│   ├── index.html            # main app page: compare form, text-to-image form, Recents sidebar
│   ├── history.html           # read-only chat history detail page
│   ├── image_history.html     # read-only text-to-image history detail page
│   ├── apikey-config.html      # personal API key config page
│   └── auth/
│       ├── base.html          # shared layout for auth pages
│       ├── login.html / register.html / profile.html
├── tests/                    # unittest tests, not deployed
├── availability_g4f/          # provider availability probing scripts, dev-only, not deployed
├── assets/                    # README screenshots, not deployed
├── firebase-key.json           # local Firebase key, must never be committed
├── .env                        # local environment variables, must never be committed
├── app.yaml                    # GAE deploy config, committed, env_variables use ${VAR} placeholders, no real secrets
├── requirements.txt
└── env/                         # virtualenv, not committed
```

### Key points in `main.py`

`load_dotenv()` loads environment variables first. `app.secret_key` comes from `SECRET_KEY`. If it is not set, a random value is used instead, which means every session becomes invalid after a restart.

`index()` is the core identity router: if the user is not logged in and is not a guest, it renders `home.html`; otherwise it renders `index.html`, and computes the Trial Quota context through `_get_frontier_quota_context()` (all five frontier providers) to pass into the template. For guests and anonymous users every quota value comes back as `None`, not a "shown as 0/10" fallback.

`home()` (`GET /home`) only clears `is_guest`. It does not touch `user_id`. Then it redirects to `/`.

`view_history(id)` handles the chat history detail page: anonymous users get redirected to the home page, logged-in users get the page rendered after an ownership check, and guests get an empty shell that the frontend fills in by reading `sessionStorage`. `view_image_history(id)` handles the image history detail page, and its rule is different: both guests and anonymous users are redirected straight to the home page. There is no empty-shell case here, because image history offers guests no record at all, in any form.

`_get_authenticated_user_id()` is the shared guard function for chat history, image history, all frontier providers, and quota query routes. Guests and anonymous users are always treated as unauthenticated and get a 401.

The pure functions for model fallback are `determine_actual_model()` (text) and `determine_actual_image_model()` (image); the rules are in section 6. `init_result_object()`/`init_image_result_object()` build the standard result dict. `detect_and_truncate()` does duplicate detection and blocked-word filtering. `parse_peer_review_json()` extracts a score and a comment from a peer review answer via `_extract_balanced_json_candidates()` (brace-depth scanning, tries the last candidate first); if parsing fails it falls back to `(80, original_text)`.

`test_g4f_provider()` and `test_g4f_image_provider()` are the core test functions for each of the two g4f chains. Their retry logic and error classification order are fully independent of each other; the exact order is in section 6. `compare_providers()` runs the g4f concurrent test stage only (peer review is now a separate endpoint, see below); logged-in users trigger `save_chat_history()`. `generate_images()` runs the single-stage concurrent flow; logged-in users trigger `save_image_history()`.

Each of the five frontier call functions (`call_claude_model()`, `call_chatgpt_model()`, `call_gemini_text_model()`, `call_gemini_image_model()`, `call_chatgpt_image_model()`) takes `(prompt, model_key, user_api_key=None, apply_persona=True)`. If `user_api_key` has a value, the client is built with the user's own key; otherwise it reads the developer's key from the environment. This is the single branch point for key routing; no caller should ever bypass it to instantiate a client directly. `apply_persona=False` is passed only during peer review, so a provider's own hidden style prompt never leaks into a review request. Claude's balance-exhausted detection checks whether `error.message` contains "credit balance" (verified against a real account; the 403 + `billing_error` combination from the docs is kept only as a compatibility fallback). Gemini's quota-exhausted detection checks `status_code == 429` (also verified against a real account) and reads exception attributes with `getattr()` duck typing on purpose, because google-genai's exception classes have no stable public import path.

`run_peer_review()` runs one peer review call with up to `PEER_REVIEW_MAX_ATTEMPTS` attempts, retrying only transient rate-limit errors with growing backoff; on final failure it returns `None` instead of a fallback message, so the review is hidden rather than shown with a fake score. `run_cross_peer_review()` (reached via `POST /api/peer-review`) is the standalone scheduler that peer-reviews every successful result (g4f and frontier) against every other; it serializes tasks aimed at the same reviewer identity with a per-reviewer lock, and computes its outer timeout dynamically from queue depth rather than a hardcoded constant.

`_append_claude_result_to_history()` and its four frontier siblings are thin wrappers that append a call's result to an existing history record. If `history_id` is empty, they skip. If the append fails, it is only logged; it never affects the response for this request. They also check the in-memory cancellation registry (`_CANCELLED_HISTORY_REQUESTS`) before appending, so a Stop-Generating click can't still land a write after the frontend gave up on it.

`quota_status()` (`GET /api/quota-status`) returns quota for all five frontier providers, using the same auth guard. `apikey_config()` (`GET /apikey-config`) only renders the page and has no login guard, because the page itself makes no request that needs permission; it only stores data in the browser's `localStorage`.

### Key points in `auth/db.py`

On init, it first looks for a local `firebase-key.json`, and only falls back to `ApplicationDefault()` (for GAE) if that file is not found. It must check whether the key file exists first, because `ApplicationDefault()` resolves credentials lazily, so its constructor throwing an exception cannot be used as the signal.

Any exception sets `FIREBASE_AVAILABLE` to `False`. Every history CRUD function checks this flag internally; none of them rely on the caller to check it. Except for create operations, every other operation reads the document first to verify that its `user_id` field matches. If it does not match, or the document does not exist, the operation is rejected and a fallback value is returned.

All queries use the new form `.where(filter=FieldFilter(field, op, value))`. Do not use the deprecated positional form `.where(field, op, value)`, which fills the logs with deprecation warnings.

`append_chat_history_result()`/`append_image_history_result()` can only append to a record that already exists; they cannot create a new one. The append logic is: read the existing results list, append the new result, re-sort using "success first, then shorter response time first," then write the whole thing back.

The `pinned_at` field controls pin sort order: pinning writes `SERVER_TIMESTAMP`; unpinning removes the field entirely with `DELETE_FIELD` (not by setting it to `None`). The sort rule is: within the pinned group, sort by `pinned_at` ascending; within the unpinned group, sort by `created_at` descending. Both chat history and image history follow this same rule.

`get_chat_history_list`/`get_image_history_list` both do a single-field equality query only. Sorting and pagination happen in the Python layer, which avoids depending on a composite index that would need to be created by hand in the Firebase console.

Claude and Gemini keep their own dedicated quota functions (`get_claude_free_tier_usage()`/`increment_claude_free_tier_usage()`/`decrement_claude_free_tier_usage()`, and a parallel Gemini set), each reading/writing one integer field on the `users` collection document with `firestore.Increment(1)`/`(-1)` for atomicity; there is no need to pre-write an initial value, `.get(field, 0)` is a sufficient fallback. ChatGPT chat, Gemini chat, and ChatGPT image instead share generic `get_free_tier_usage()`/`increment_free_tier_usage()`/`decrement_free_tier_usage(field_name)` helpers, parameterized by field name (`chatgpt_free_tier_usage`, `gemini_text_free_tier_usage`, `chatgpt_image_free_tier_usage`). All five counters are independent and share nothing. The decrement functions back a one-time refund ledger (`_PENDING_FRONTIER_REFUNDS` in `main.py`) used when a request is cancelled after its quota increment already happened; it only recognizes increments that really happened and cannot be replayed to farm quota. Checking a quota and incrementing it are two separate Firestore operations with no transaction between them; this is a deliberate simplification.

### `auth/routes.py`

Every route has an outer `try/except`, and on error it responds through `flash()`. A successful login or registration writes `session['user_id']`/`username` and clears `is_guest`. Logging out clears all three session keys. `/profile` first checks `session['user_id']` and redirects to the login page if it is missing.

### Key points in the frontend templates

`templates/index.html` uses a two-column layout: a 260px-wide dark sidebar on the left, and the main content area on the right. The sidebar uses `position:sticky` plus a pure CSS `calc()` height, instead of computing pixel heights in JS by hand. This avoids rounding errors that would otherwise stack up with the page zoom.

Text-to-image mode and chat mode are two mutually exclusive containers, switched by `switchToImageMode()`/`switchToCompareMode()`. Every provider checkbox (free or frontier) must use its own separate class and must not share a class with any other provider, because some queries in the project use `querySelectorAll` without scoping to a container, and a shared class name would let forms cross-contaminate each other.

The Recents sidebar supports both a chat history mode and an image history mode, switched by the `sidebarMode` variable, and both physically share the same `#sidebarRecents` container. The image version of Recents is only open to logged-in users; guests see a lock message and no network request is ever made. Chat history falls back to a sessionStorage mirror for guests; image history has no fallback at all for guests. This asymmetry is intentional.

Both the chat form and the image form use a "four-section" layout: first the frontier provider selection area (one card per frontier provider), then that provider's own model dropdown, then the free g4f provider checkbox area, then the free model dropdown. The frontier area and the free area are two separate containers and must not be merged.

Each of the five frontier model dropdowns is a custom component (`.custom-select-wrapper`/`.custom-select-trigger`/`.custom-options`/`.custom-option`, built by the shared `setupFrontierModelDropdown()`), not a native `<select>` — a native popup's colors/hover state cannot be fully restyled with CSS. The native `<select>` is kept underneath, hidden with `display:none`, purely to hold the `.value`/`.disabled` state that the rest of the code already reads. The free-model dropdown (`#customOptions`/`#imageCustomOptions`) uses `position:fixed`, computed on open via `getBoundingClientRect()` (divided by `--page-zoom`), so it never inflates the scrollable height of the page; the panel collapses on real page scroll. Selecting a new option delays the black "selected" highlight by `MODEL_HIGHLIGHT_DELAY_MS` so it doesn't snap ahead of the panel's own collapse animation.

Each provider (free or frontier) remembers its own model selection independently — `providerModelSelections`/`imageProviderModelSelections` — and the shared free-model dropdown always binds to whichever free provider was checked most recently, not a merged union list. On submit, per-provider overrides go out as a `provider_models` dict, not one global model field.

Frontier-only mode (`#frontierOnlyToggle`/`#frontierOnlyToggleImage`) force-unchecks and grays out every free provider checkbox and locks the free model dropdown; submitting with it on and no frontier provider checked is blocked client-side with an alert.

Guests and anonymous users see a grayed-out card on every frontier provider with the message "Log in to unlock frontier models." When the form is submitted, if any frontier card is checked, the frontend sends one extra request per checked frontier provider after the g4f results, and merges each result into the same rendered list; once every checked provider (g4f and frontier) has answered, the frontend calls `POST /api/peer-review` to trigger cross-namespace peer review.

Every piece of user-visible text on the page must be in English. No Chinese text is allowed there. This rule does not govern code comments or this document itself.

For scrollbars, the project draws its own draggable scroll indicator, and the native scrollbar is fully hidden. When a custom dropdown panel closes, it must use `max-height:0` plus `overflow:hidden`, not just `opacity:0`/`visibility:hidden`; otherwise the invisible box would still expand the page's scrollable area.

## 5. Core execution flow

1. On startup: load environment variables, register the auth blueprint, initialize Firebase, redirect g4f's media directories to `/tmp`, and probe whether g4f/anthropic/openai/google-genai can be imported correctly.
2. Visiting `/`: `index()` checks login state and decides whether to render the home page or the main app page.
3. Guests go through `/api/auth/guest`; login and registration go through their own forms.
4. Chat comparison goes through `/api/compare` (skipped entirely in Frontier-only mode): tests each g4f provider concurrently, sorts the results, saves history for logged-in users, and returns the results.
5. Each checked frontier chat provider goes through its own route (`/api/claude-chat`, `/api/chatgpt-chat`, `/api/gemini-chat`): auth guard, own-key check, quota check, call the official API, classify the error, update the quota counter, append to the history record.
6. Text-to-image goes through `/api/generate-images`, structurally identical to step 4 but for the image g4f chain.
7. Each checked frontier image provider goes through its own route (`/api/gemini-image`, `/api/chatgpt-image`), structurally identical to step 5.
8. Once every checked provider has responded, the frontend calls `POST /api/peer-review` to run cross-namespace peer review over every successful result.
9. Clicking Stop Generating aborts in-flight requests client-side and calls the matching `/cancel` or `/refund` endpoint so the request never writes to history and any already-consumed quota is refunded.

## 6. Core business rules

### The three identity states

An anonymous user has no `user_id` and no `is_guest`. They see `home.html` on the home page, and nothing is ever stored for them. A guest has `is_guest=True`. They see the main app page plus a guest badge, and their data lives only in frontend memory and sessionStorage; nothing is written to the database. A logged-in user has `user_id`, and their data syncs with Firestore. These three keys are mutually exclusive: whenever `user_id` is present, `is_guest` must already be cleared, and the same holds in reverse.

### Model fallback rules

Text models follow three rules: if the requested model is in the mapping table, use it directly; if it is unsupported or not specified, use the first model in the mapping table; if the provider has no model config at all, fall back to `gpt-3.5-turbo`. Image models only follow the first two rules, with no third fallback: if the provider is not in the mapping table, it returns `None`, and the frontend shows it as `default`.

### Peer review rules

Cross-namespace peer review (`run_cross_peer_review()` / `POST /api/peer-review`) runs after the frontend has collected every checked provider's result (g4f and frontier together). The trigger condition is: at least 2 results total, and at least 2 successes. Every successful answer is reviewed by every other successful answer, never itself; a failed result neither reviews nor gets reviewed. `apply_persona=False` is used for the answering call made during a review, so a provider's own style prompt never leaks into review text. If a review's response parses but the JSON extraction fails, it falls back to a score of 80 plus the raw text; if the review call itself exhausts its retries, the whole review is dropped (returns `None`) and simply does not appear, rather than showing a fake fallback score. `run_peer_review()` retries only transient rate-limit errors, with growing backoff, up to `PEER_REVIEW_MAX_ATTEMPTS` attempts total; `run_cross_peer_review()` serializes tasks aimed at the same reviewer identity behind a per-reviewer lock so a single rate-limited backend never gets hit by a whole concurrent batch at once, and its outer timeout is computed from queue depth, not hardcoded. The endpoint accepts at most `MAX_PEER_REVIEW_ENTRIES` (10) results and validates each entry's provider/model/type combination against the matching availability flag, silently dropping invalid entries; login is required as soon as any frontier reviewer is involved, while a pure g4f list stays guest-accessible.

### Sort rule

Successful results come first. Among results with the same success state, the one with the shorter response time comes first. Both the text chain and the image chain share this same sort expression.

### Error message classification order

On the text side, content moderation errors (like an Azure OpenAI moderation block) must be classified before network errors, because retrying a moderation error is pointless, and misclassifying it as "system busy" would push the user into a useless retry.

On the image side, GPU quota exhausted errors must be classified before network errors, because a quota exhausted error should never be retried: retrying does nothing for an already-exhausted quota, and it adds pressure to an already tight free resource pool.

### Image generation retry rule

Only transient rate-limit errors, like a 429 or a full queue, get retried, and only once, waiting 2 to 3 seconds of random jitter before the retry. GPU quota exhausted errors and content moderation errors are never retried.

### Image generation timeout budget

The default advisory timeout is 40 seconds. The outer timeout is not a hardcoded constant; it is computed on the fly with the formula `2 * advisory + 5 second buffer`, which comes out to 85 seconds in the default case. It uses double the advisory time because a retry can run up to two attempts, and each one might run close to the full advisory time before finishing. `AnyProvider`, an aggregator-style provider, measurably takes longer, so it gets its own advisory budget of 70 seconds; its outer time is still computed by the same formula, not hardcoded separately.

If some provider in the future needs more time, give it its own advisory override instead of raising the global default across the board, which would slow down the worst-case wait time for every provider batch.

### Frontier-only mode

When `frontier_only` is true in the request body, `/api/compare` and `/api/generate-images` force `providers_to_test` empty, skipping the entire g4f concurrent stage — this does not fall back to the old legacy meaning of "empty providers array means test everything." A history record is still created (with empty g4f results) for frontier calls to append to.

### Claude / ChatGPT / Gemini access control

Guests and anonymous users are always blocked from every frontier provider, with two layers of defense: the frontend shows a grayed-out card, and the backend returns 401. There is no degraded tier.

Each of the five frontier providers has its own free-call limit (currently 10 each, independent constants and independent Firestore fields — never share one). The limit is only checked and consumed when the user did not bring their own key, and only a successful call consumes it; a failed or cancelled call does not count. Once a quota is used up, the backend blocks the request outright and never calls the official API, so it never touches the developer account's own budget. There is no transaction protecting the gap between checking a quota and incrementing it; this is a deliberate simplification. One click still consumes at most one quota unit per provider clicked.

A user can enter their own key on the `/apikey-config` page, per provider (`X-User-Claude-Key`, `X-User-ChatGPT-Key`, `X-User-Gemini-Key` — the Gemini key covers both its chat and image tiers). Keys live only in the browser's `localStorage` and are sent per-request; the backend never persists a personal key.

When a developer account's balance/quota is exhausted, this is converted into a provider-specific error code (e.g. `SERVER_CREDITS_EXHAUSTED` for Claude, `SERVER_QUOTA_EXHAUSTED` for Gemini/ChatGPT) and returned as a 503; it never counts against the user's free quota. The friendly message text branches on whether the user brought their own key: exhausting your own key tells you to check your own account; exhausting the developer key (while your trial quota still has uses left) tells you to contact the developer.

Gemini's verification coverage is the smallest of the five: only the quota-exhausted scenario has been verified against a real, zero-quota account. The "success with sufficient quota" and "403 from an invalid key" paths are only backed by docs and mock data — a known, still-open verification gap.

## 7. Data models

### LLM Result (text, 7 fields; a peer-reviewed result gets one extra `peer_reviews` array, making 8 fields)

```python
{
    'provider': str, 'success': bool, 'response': str, 'error': str,
    'response_time': float, 'model': str, 'type': 'g4f'
}
```

### Image Result (image, 8 fields, an independent contract, not mixed with the text DTO)

```python
{
    'provider': str, 'success': bool, 'url': str | None, 'b64_json': str | None,
    'error': str, 'response_time': float, 'model': str, 'type': 'g4f_image'
}
```

`url` and `b64_json` are mutually exclusive; on success only one of them is non-empty.

### Frontier chat results (Claude, ChatGPT, Gemini-chat)

```python
{
    'provider': str, 'success': bool, 'response': str, 'error': str,
    'response_time': float, 'model': str, 'type': str,  # 'anthropic' / 'openai' / 'google_genai_text'
}
```

Same shape as LLM Result but independent from it: `type` differs per provider and there is no `peer_reviews` field at the top level (cross peer review results, when present, are attached separately by the frontend/`/api/peer-review`, not baked into this DTO).

### Frontier image results (Gemini-image, ChatGPT-image)

```python
{
    'provider': str, 'success': bool, 'url': None, 'b64_json': str | None,
    'error': str, 'response_time': float, 'model': str, 'type': str,  # 'google_genai' / 'openai_image'
}
```

Same shape as Image Result but independent from it: the official APIs return image bytes directly as base64, so `url` is always `None` in the response returned for the live request. Before being appended to the Firestore history record, `_persist_image_result_local_copy()` converts `b64_json` into a local file and swaps it for a `url` (see section 2). So the persisted copy read back from history has a non-empty `url` and a `None` `b64_json`.

### Firestore collection layout

The `users` collection stores username, email, password hash, creation time, and five quota fields: `claude_free_tier_usage`, `gemini_free_tier_usage`, `gemini_text_free_tier_usage`, `chatgpt_free_tier_usage`, `chatgpt_image_free_tier_usage`.

The `history` collection (logged-in users only) stores chat history, with fields `user_id`, `title`, `prompt`, `results`, `created_at`, `is_pinned`, `pinned_at`. The `results` array may mix g4f-shaped results with any frontier chat result, so rendering code must defensively handle the case where `peer_reviews` may not exist.

The `image_history` collection (logged-in users only) has a similar structure, but is a fully separate collection storing image-type DTOs. Its `results` array may mix g4f image results with Gemini-image/ChatGPT-image results.

Never merge these two collections, and never cross-use their discriminator fields.

### CRUD contract table

| Function | Args | Success | Failure |
|---|---|---|---|
| `save_chat_history` | `user_id, prompt, results` | dict with an id | `None` |
| `get_chat_history_list` | `user_id, limit=20, offset=0` | list of dicts | `[]` |
| `get_chat_history_by_id` | `user_id, history_id` | dict with an id | `None` |
| `delete_chat_history` | `user_id, history_id` | `True` | `False` |
| `update_chat_history_title` | `user_id, history_id, new_title` | `True` | `False` |
| `toggle_pin_chat_history` | `user_id, history_id` | the flipped boolean | `None` |
| `append_chat_history_result` | `user_id, history_id, result` | `True` | `False` |

The image history function set has identical names and an identical contract, just against a different collection, plus a delete-time local-media cleanup step (see section 2). Checking whether `toggle_pin` failed must use `is None`, because `False` is a valid success result.

The frontier quota counter functions (Claude/Gemini's dedicated pairs, plus the generic `get_free_tier_usage`/`increment_free_tier_usage`/`decrement_free_tier_usage` used by ChatGPT chat/image and Gemini chat) have no concept of ownership checking, because `user_id` comes directly from the session.

## 8. External interfaces

Chat: `GET /api/providers`, `POST /api/compare`, `POST /api/compare/cancel`, `POST /api/test-single`, `GET /health`.

Cross-namespace peer review: `POST /api/peer-review`, body carries up to `MAX_PEER_REVIEW_ENTRIES` results (g4f and/or frontier); login required once any frontier reviewer is included.

Frontier chat (login required): `POST /api/claude-chat`, `POST /api/chatgpt-chat`, `POST /api/gemini-chat` — body is `{prompt, model, history_id}`, optionally with `X-User-Claude-Key`/`X-User-ChatGPT-Key`/`X-User-Gemini-Key`. Each has a `POST .../refund` sibling for Stop-Generating cleanup.

Frontier image (login required): `POST /api/gemini-image`, `POST /api/chatgpt-image` — same body/header shape as above, each with its own `/refund` sibling.

Quota query (login required): `GET /api/quota-status`, returns per-provider `{used, limit}` for all five frontier providers.

Text-to-image: `GET /api/image-providers`, `POST /api/generate-images`, `POST /api/generate-images/cancel`, `GET /media/<filename>`.

Page routes: `GET /`, `GET /home`, `GET /history/<id>`, `GET /image-history/<id>` (login required, guests/anonymous get redirected), `GET /apikey-config` (no login guard).

Auth: `/login`, `/register`, `/logout`, `/profile`. Guest: `POST /api/auth/guest`.

Chat history (login required): `GET /api/history`, `PATCH /api/history/<id>/title`, `DELETE /api/history/<id>`, `POST /api/history/<id>/toggle-pin`. Image history routes have the same structure, with the prefix swapped to `/api/image-history`.

### Third-party integrations

g4f is used to call free channels with no credentials. The Firebase Admin SDK uses a local key file locally, and ADC on GAE. The `anthropic`, `openai`, and `google-genai` SDKs are the three integrations that need a real, paid key. Gemini image generation through g4f's key-free path is still unavailable; that is unrelated to the official paid Gemini API path and the two should not be confused.

## 9. Known risks and limitations

Timeout values must stay in sync: the peer review internal/outer timeouts and the image generation advisory/outer timeouts must each stay linked through their own formula; do not hand-edit just one side.

Files under `generated_media/` (now under system temp, see section 2) are not scoped per user, and they keep accumulating as long as their matching `image_history` record still exists; there is no automatic cleanup by age. Deleting a record explicitly deletes its matching files, but that only covers the "user actively deletes it" path. The real long-term fix is shared storage (e.g. Cloud Storage) plus a per-user quota; not built yet.

In production (GAE with multiple instances), local disk (including `/tmp`) is independent per instance. If an image is written on instance A and a later request lands on instance B, that request gets a 404. Fixing this requires shared storage, not just a different local path.

All five frontier free quotas are counted per registered account, so anyone can register endless new accounts to get unlimited quota. There is currently no IP rate limiting or CAPTCHA protection. This is a known, unresolved problem. Three directions are being considered: IP-level rate limiting (first needs confirming whether a trustworthy client IP is even available under GAE deployment), a CAPTCHA, and email verification.

Some free g4f providers get a 100% "Access from cloud provider blocked" 403 the moment they run from a cloud IP, even though they work fine locally (Groq and OpenRouterFree were removed for this reason after a real GAE deploy). When adding a new free g4f provider, verify it against a real deployed instance, not just local dev, before trusting the probe script's local-only result.

Gemini's error classification has only been verified against one real scenario (zero quota); see section 6.

The frontend interaction layer (optimistic updates, animations, layout) has no automated tests. The project has not adopted any frontend test framework; verification relies on manual testing.

## 10. Guide for extending and modifying

### Safe zone: adding a new text provider

After running the probe script to confirm it works — including, ideally, from a real deployed instance, not just locally (see section 9 on cloud-blocked providers) — add it to the `G4F_PROVIDERS` list and to `PROVIDER_MODELS_MAP`. Optionally, give it an invisible style prompt and a peer review judge prompt. The frontend wiring is fully automatic; there is no need to touch any HTML or JS.

### Safe zone: adding a new image provider

Same process: add it to `IMAGE_PROVIDERS`/`IMAGE_PROVIDER_MODELS_MAP`. Do not let an image provider share a mapping table or a namespace with a text provider.

### Safe zone: adding a new page

If this page could ever be a redirect target, it must include the flash message display block, otherwise messages will keep piling up in the session.

### Safe zone: adding a new frontier model

First confirm whether the official model ID has changed, then add one mapping entry in the matching `*_MODELS` dict, and add one option to the frontend's custom model dropdown for that provider. There is no need to touch quota or permission logic.

### Safe zone: adding a new frontier provider

Follow the existing pattern from any of the five current frontier providers: an independent call function (own key routing, own error classification), an independent model mapping, an independent quota constant/field, and its own route reusing the shared auth guard, plus a matching `/refund` route. On the frontend, add a new card with its own independent class and its own custom model dropdown (via `setupFrontierModelDropdown()`). In `apikey-config.html`, wire the matching input to its own independent `localStorage` key. Do not let the new provider fall into g4f's namespace or take part in g4f's concurrent scheduling.

### Danger zone: logic not to touch

Do not change the 7-field text contract, the 8-field image contract, or the frontier result shapes in section 7. Do not let text and image providers share a mapping table or a scheduling path. Do not let any provider checkbox reuse another provider's class name.

Do not mix image history into the chat history collection, and do not let `generate_images()` call `save_chat_history()`. Do not let guests or anonymous users use the image version of Recents or the image history detail page.

Do not add a server-side proxy endpoint for image downloads; that introduces an SSRF risk. Downloads must happen in the browser. Do not add "if the file is missing, fetch it from the URL parameter" logic to `/media/<filename>`.

Do not reintroduce any form of automatic cleanup for `get_media_dir()`, whether lazy cleanup by age or wiping the whole directory indiscriminately. Do not change `get_media_dir()` back to a relative path like `./generated_media`; GAE Standard's filesystem is read-only outside `/tmp`, so it must stay redirected to `tempfile.gettempdir()`. Do not let `_persist_image_result_local_copy()` fall back to returning the original oversized `b64_json` on a local write failure; it must return a small failure result so the Firestore append still succeeds.

Do not let the peer review and text-to-image timeouts drift out of sync. Do not remove the `provider_models_json`/`image_provider_models_json` injection in the root route. Do not reverse the classification order between content moderation errors and network errors, and on the image side do not reverse GPU-quota-vs-network-error order either; do not add a retry for GPU quota errors.

Do not change the image timeout formula back from "double the advisory plus a buffer" to "one times the advisory plus a fixed buffer." Do not add a separate outer key to the per-provider timeout override table; the outer value must always be derived from the advisory value through the formula.

Do not set both `user_id` and `is_guest` in the session at the same time. Do not skip the `FIREBASE_AVAILABLE` check and call a CRUD function directly. Do not change the behavior of `GET /home`; it must not clear `user_id`. Do not remove the ownership check inside the history CRUD functions. Do not let the guest path call any history CRUD function. Do not use `if not new_pinned` to check whether a pin operation failed; it must be `is None`.

Do not let `get_chat_history_list` go back to a composite sort query on the Firestore side; sorting must stay in the Python layer. Do not change the pin sort field back from `pinned_at` to `created_at`. Do not bring back the deprecated pattern of "clicking a history item renders it in place into an editable form"; it must fully navigate to a read-only page. Do not switch the guest history storage medium from `sessionStorage` to `localStorage`. On a history detail page, do not navigate away before an in-flight DELETE of the currently-viewed entry actually resolves; await it first and show a toast on failure instead.

Do not remove the `sticky` positioning or the CSS `calc()` fixed height on `.left-sidebar`. Do not bring back the native scrollbar. Do not add any "resubmit" form entry point back to the history detail pages. Do not revert any of the five frontier model dropdowns from the custom `.custom-select-wrapper` component back to a bare native `<select>` — the native popup's colors can't be fully restyled. Do not remove `position:fixed` from the free-model `.custom-options` panel. Do not remove the `MODEL_HIGHLIGHT_DELAY_MS` delay on option selection.

Do not let guests or anonymous users call any frontier route; all must be fully unavailable to them, with no degraded experience. Do not let any frontier card reuse another provider's class. Do not merge the frontier provider selection area and the free provider selection area back into a single container. Do not let any frontier provider take part in g4f's concurrent scheduling.

Do not change the quota check order to "call the official API first, then check quota." Do not let a request that brought its own key still check or increment the free quota. Do not persist a user's personal key on the backend. Do not count balance/quota exhaustion against the user's free quota. Do not let two different frontier quota constants/fields reference the same value.

Do not let any frontier chat/image endpoint call `save_chat_history()`/`save_image_history()` itself to create a new record; it can only append to an existing one. Do not let the append functions skip the ownership check, or turn into an endpoint that accepts arbitrary client-submitted content. Do not forget to swap the internal error code for a user-friendly message before appending to history on the balance/quota-exhausted branch.

Do not let `run_peer_review()`/`run_cross_peer_review()` go back to showing a fallback message when a review call itself fails; a failed review must simply be hidden. Do not remove the per-reviewer-identity lock in `run_cross_peer_review()`, and do not hardcode its outer timeout instead of deriving it from queue depth.

Do not reintroduce Chinese text anywhere user-visible, including page copy, flash messages, the message field in a JSON error body, or prompt text sent to an LLM. This rule does not govern code comments or this document itself.

Do not let the Trial Quota badges render for guests or anonymous users. Do not let the frontend guess locally whether a call consumed quota; it must rely on the backend's real query endpoint. Do not change the quota query endpoint's guard to allow guests.

## 11. Build, run, and test commands

### Environment setup

```bash
python3 -m venv env
source env/bin/activate
pip install -r requirements.txt
```

### Prerequisites for running locally

You need a `firebase-key.json` in the project root, and a fixed `SECRET_KEY` in `.env`. `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, and `OPENAI_API_KEY` are optional; the app starts fine without them, but the matching feature fails when actually called.

### Running

```bash
python main.py                    # default port 8080
PORT=5000 python main.py
gunicorn -b :8080 --timeout 300 main:app   # simulates GAE; 300s covers worst-case image/peer-review latency
```

After changing anything under `templates/*.html`, you must restart the dev server, because template auto-reload is not enabled. A running process keeps caching the old template, and a hard refresh in the browser will not fix that.

Visit `http://localhost:8080`, and check status with `/health`.

### Automated tests

The project uses `unittest`, with test files under `tests/`. They fall into a few groups: white-box tests of internal functions (model fallback rules, DTO completeness, error classification, and so on), black-box tests of HTTP routes, auth-related tests, dedicated per-provider integration tests, regression tests for the English-only text policy, and regression tests for HTML structural integrity.

Commands to run the tests:

```bash
python -m unittest discover -s tests
python -m unittest discover -s tests -v
python -m unittest tests.test_main_whitebox
```

Frontend interactions (animations, optimistic updates, the scroll indicator) are not covered by unittest; verification relies on manual testing.

### Smoke tests

```bash
curl http://localhost:8080/health
curl http://localhost:8080/api/providers
curl -X POST http://localhost:8080/api/test-single -H "Content-Type: application/json" \
  -d '{"prompt": "What is 2+2?", "provider": "PollinationsAI"}'
curl -X POST http://localhost:8080/api/compare -H "Content-Type: application/json" \
  -d '{"prompt": "Hello", "providers": ["PollinationsAI"]}'
curl -X POST http://localhost:8080/api/auth/guest
```

Every frontier endpoint needs a logged-in session, for example:

```bash
curl -X POST http://localhost:8080/api/claude-chat -H "Content-Type: application/json" \
  -H "Cookie: session=<session cookie after login>" \
  -d '{"prompt": "What is 2+2?", "model": "claude-sonnet-5"}'
```

### Provider availability probe scripts

Only rerun these after a g4f library upgrade, or when you suspect the existing conclusions are stale — and ideally confirm from a real deployed instance too, since some providers block cloud IPs (see section 9):

```bash
cd availability_g4f
python find_providers_models.py
python test_providers.py
python find_image_providers.py
python test_image_providers.py
```

### Dependency management

`requirements.txt` locks versions in `pip freeze` format, with the only exception being `gunicorn`. To update a dependency, run `pip install <package>` and then `pip freeze > requirements.txt`; do not edit version numbers by hand.

### Deploying to GAE

```bash
gcloud app deploy app.yaml
gcloud app logs tail -s default
```

`app.yaml` is committed to git as normal; its `SECRET_KEY`/`ANTHROPIC_API_KEY`/`GEMINI_API_KEY`/`OPENAI_API_KEY` entries under `env_variables` only hold `"${VAR_NAME}"` placeholders, never real values. `gcloud` does not expand these automatically — replace all four placeholders with real values locally by hand before deploying, and never commit the replaced version. `firebase-key.json` is not deployed; GAE uses ADC instead. Keep the simplest possible deploy setup: no secret manager, no CI pipeline, no multi-environment config, no separate deploy script — just `app.yaml` plus `gcloud app deploy app.yaml`.

## 12. Code conventions

### Python

Global constants use uppercase with underscores; functions and variables use lowercase with underscores. Route function names should line up semantically with their path.

Use the module-level `logger` for logging, never `print`. Use INFO level for normal flow milestones, and always pass `exc_info=True` on an error. Truncate long strings in logs; for example, only log the first 50 characters of a prompt.

LLM and text-to-image routes need an outer `try/except` that returns a JSON error body in a consistent shape. Auth routes report errors through an outer `try/except` using `flash()`. The field set of a result dict must not be added to or removed from casually.

### JavaScript and frontend

Use plain JS, with no framework and no build tool. Backend data is injected into the page through Jinja2's `tojson` filter, and parsed on page load. Before parsing a response as JSON, always check `response.ok` first; on a non-2xx status, try to read the `error` field first, and fall back to the status code if that field is missing.

### Commit conventions

Both Chinese and English commit messages are fine. Keep commits atomic: one commit does one thing.

## 13. Key paths, quick reference

Core chat path: submit the form, call `/api/compare` (unless Frontier-only), test each g4f provider concurrently, sort, save history for logged-in users, return JSON; each checked frontier chat provider is then called on its own route and merged into the same rendered list; once everything has answered, `POST /api/peer-review` runs cross-namespace peer review.

Core text-to-image path: `/api/generate-images` (g4f), then each checked frontier image provider on its own route; same sort/save/merge pattern as chat, no peer review restriction on image (frontier image providers aren't included in blind review yet).

Core frontier path (any of the five): auth guard, own-key check, quota check, call the official API, classify the error, update the quota counter on success without a personal key, append to the history record if `history_id` is non-empty, merge into the rendered list. None of this goes through the g4f concurrent scheduler, and it never creates a new history record on its own.

Core auth path: the login form is submitted, goes through the auth blueprint, queries Firestore, writes the session, and redirects to the home page.

Key invariants checklist: result sort order is always success first, then shorter response time first. `user_id` and `is_guest` are never both present at once. Any redirect target page must have a flash display area. The 7-field text contract and the 8-field image contract must not be broken. The error classification order must not be reversed. Peer review trigger is at least 2 results and at least 2 successes; a failed review is hidden, never shown with a fake score. Text and image providers keep strictly separate namespaces. History list queries only do a single-field query plus Python-layer sorting/pagination. Checking whether pinning failed must use `is None`. Guest data is never persisted: chat history mirrors into sessionStorage, image history has no fallback at all. Page zoom is controlled by `--page-zoom`; any CSS involving viewport height must use `calc(100vh/var(--page-zoom))`. `history` and `image_history` are two separate collections and must not be merged. No frontier provider can create a new history record on its own, only append to an existing one. All user-visible text must be in English. Every Trial Quota badge only renders for logged-in users, with numbers from a real backend query, never a frontend guess. Local media storage lives under `tempfile.gettempdir()`, never a relative path.

Core files: all backend logic is in `main.py`; auth logic is in `auth/routes.py` and `auth/db.py`; frontend templates are in `templates/`, with `index.html` as the main app page.

## 14. Update log

(No open entries. Historical entries as of 2026-07-06 were folded into sections 2/3/6/7/8/9/10/11 above and removed from this log per the rule in section 0. Append new entries below using the mandated format.)
