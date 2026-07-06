# claude.md

## 0. Rules for future updates (mandatory)

Any future edit to this file must be short and to the point. Do not write debugging narratives. Do not write incident retrospectives. Do not write stories like "we first thought X, then discovered Y." Only write the conclusion and the current state.

Follow this format when you append an update record:

`[Module name] Reason: short explanation. Changes: 1. 2. 3.`

Example:

`[Claude quota] Reason: Users said the quota was too low. Changes: 1. Changed CLAUDE_FREE_TIER_LIMIT from 10 to 20. 2. Updated the frontend dialog text to match the new number.`

After you add a new entry, check whether this document has grown too long again. If a history entry is older than 3 months, or the feature it describes is now fully stable, delete the details and keep only the final conclusion. This document is an operations manual for future Claude sessions, not a project log.

## 1. System overview

This is a Flask web app that aggregates and compares large language models. A user types a prompt. The system calls several g4f providers at the same time. It shows each provider's answer and response time. Then it asks the models that answered successfully to score and review each other.

The system supports three identity states: anonymous visitor, guest, and logged-in user. A logged-in user's conversation history is stored in Firestore. The user manages it from the Recents sidebar on the left, which supports grouping, pagination, pinning, renaming, and deleting. A guest's history lives only in the browser's sessionStorage. It disappears when the tab closes.

The system also supports text-to-image comparison, through a fully separate g4f call chain. Image generation results are stored in their own `image_history` collection, with their own Recents sidebar. Only logged-in users can use this feature. Guests and anonymous users cannot use it at all.

The chat feature also connects to the official Anthropic API (Claude). The image generation feature connects to the official Google Gemini API (the "Nano Banana" series). These are the only two providers in the project that cost real money, so they have a full set of quota controls and abuse prevention, much stricter than the other free providers. See section 6 for the exact rules.

There is a Trial Quota badge at the top of the navbar. It shows how many free calls the user has left for Claude and for Gemini. Chat mode shows the Claude number. Image mode shows the Gemini number. After each call to either provider, the frontend asks the backend for the real quota number again. It never guesses the number locally.

The backend is built with Flask plus a Blueprint (`auth/`). The frontend uses Jinja2 plus plain JavaScript, with no frontend framework and no build tool. Overall page scale is controlled by the `--page-zoom` variable on `:root`, currently set to 0.88.

## 2. Architecture map

The system has three parts: the Flask backend, the Firebase auth module, and the HTML5/JS frontend.

### Backend (`main.py`)

Routes fall into these groups: page routes (`/`, `/home`, `/history/<id>`, `/image-history/<id>`, `/apikey-config`), g4f chat API (`/api/providers`, `/api/compare`, `/api/test-single`), Claude API (`/api/claude-chat`), Gemini API (`/api/gemini-image`), quota query API (`/api/quota-status`), text-to-image API (`/api/image-providers`, `/api/generate-images`), the static file route for generated images (`/media/<filename>`), auth API (`/api/auth/guest`), chat history API (the `/api/history` group), and image history API (the `/api/image-history` group, logged-in users only).

Concurrent scheduling uses `ThreadPoolExecutor`, which calls several providers at the same time so one slow provider does not hold up the rest. The text-to-image route reuses the same scheduling skeleton, but it only has one stage. It has no peer review step.

On the g4f side there are two fully separate call chains: `g4f.ChatCompletion` handles text chat, and `g4f.client.Client().images.generate()` handles text-to-image. The model matching logic and the exception handling of the two chains share nothing. When g4f generates an image, it automatically downloads the image into the local `get_media_dir()` folder, and the `url` field it returns is a relative path like `/media/<filename>?url=...`. This is a routing convention from g4f's own bundled GUI server, but this project does not run that server, so we added our own `GET /media/<filename>` static file route (`serve_generated_media`) to serve these files. These local files are not cleaned up automatically by age. They are only deleted together with their `image_history` record when the user explicitly deletes that record (see the update log in section 14). Before that deletion, the files just keep accumulating during normal use. This is an accepted, deliberate tradeoff in exchange for being able to view historical images forever.

Claude runs through a fully separate, third call chain: `call_claude_model()` calls `client.messages.create()` from the official `anthropic` SDK directly. It does not enter the `ThreadPoolExecutor` scheduler, it does not take part in peer review, and it does not reuse any g4f mapping table. The frontend shows it as a provider card placed visually alongside the others in the chat form. When the user clicks Compare, the frontend sends one extra, separate `POST /api/claude-chat` request, and the rendering layer merges the result into the same results list. This request can carry the `history_id` already returned by `/api/compare`, so the Claude result gets appended to that same conversation history record.

Gemini (the "Nano Banana" series) runs through a fully separate, fourth call chain: `call_gemini_image_model()` calls `client.interactions.create()` from the official `google-genai` SDK. It is used in the image generation form, in a role fully parallel to Claude: one handles the chat scenario, the other handles the image scenario. All three model tiers are wired up: `Nano Banana Pro` maps to `gemini-3-pro-image`, `Nano Banana 2` maps to `gemini-3.1-flash-image`, and `Nano Banana Lite` maps to `gemini-3.1-flash-lite-image`. The frontend lets the user pick one from a dropdown, and any tier consumes exactly one quota unit. This request can also carry a `history_id` to append to an existing image history record.

Persisting frontier model results into history: both `claude_chat()` and `gemini_image_chat()` accept an optional `history_id` field in the request body. As long as the call actually happened (it was not blocked by the quota check), the result is appended to the existing history record for that `history_id`, whether the call succeeded or failed. The functions that do the appending are `append_chat_history_result()` and `append_image_history_result()` in `auth/db.py`. They check ownership before appending, and after appending they re-sort the whole results array using the rule "success first, then shorter response time first" before writing it back. These two functions can only append to a record that already exists. They cannot create a new record. The only entry point that creates a new record is still the g4f chain's `save_chat_history()`/`save_image_history()`.

### Auth subsystem (`auth/`)

`auth_bp` is mounted at the root path, with no prefix: `/login`, `/register`, `/logout`, `/profile`. When `auth/db.py` starts, it tries to connect to Firebase Firestore. If it cannot connect, it sets `FIREBASE_AVAILABLE` to `False`, and the auth routes then return 503 instead of crashing. User identity is carried between requests by Flask's `session`, keyed by the `SECRET_KEY` environment variable.

### Frontend (Jinja2 + JS)

The navbar has three states, switched based on `session.user_id`/`is_guest`. Both `auth/base.html` and `index.html` each keep their own copy of this logic. All communication with the backend goes through the Fetch API and is non-blocking.

## 3. Tech stack

The languages are Python and JavaScript. The backend framework is Flask, with concurrency handled by `concurrent.futures.ThreadPoolExecutor`. Core dependencies are g4f, firebase-admin, python-dotenv, the official `anthropic` SDK, and the official `google-genai` SDK (imported as `from google import genai`; do not confuse it with the deprecated old SDK `google-generativeai`).

Auth uses Werkzeug's password hashing functions plus Flask's `session`. The database is Google Cloud Firestore, accessed through the Firebase Admin SDK. The frontend is plain HTML5, CSS3, and vanilla JS, with no framework and no build tool. The template engine is Jinja2. The deployment platform is Google App Engine.

For environment variables, `SECRET_KEY` and `PORT` are the basic config, loaded locally from a `.env` file via python-dotenv. `ANTHROPIC_API_KEY` is the developer's default Claude key. The app can start without it; it only fails when a call actually needs it. `GEMINI_API_KEY` is the developer's default Gemini key, and it behaves differently from Claude: as soon as `google_genai.Client()` is constructed, it checks this environment variable immediately, and throws a `ValueError` right away if it is missing, instead of waiting until an actual call. Either way, the user sees the same outcome: the request fails, and nothing else is affected.

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
├── firebase-key.json           # local Firebase key, must never be committed
├── .env                        # local environment variables, must never be committed
├── app.yaml                    # GAE deploy config, committed, env_variables use ${VAR} placeholders, no real secrets
├── requirements.txt
└── env/                         # virtualenv, not committed
```

### Key points in `main.py`

`load_dotenv()` loads environment variables first. `app.secret_key` comes from `SECRET_KEY`. If it is not set, a random value is used instead, which means every session becomes invalid after a restart.

`index()` is the core identity router: if the user is not logged in and is not a guest, it renders `home.html`; otherwise it renders `index.html`, and computes the Trial Quota context through `_get_frontier_quota_context()` to pass into the template. This function only looks up Claude and Gemini quota usage when the user is logged in. For guests and anonymous users both values come back as `None`, not a "shown as 0/10" fallback.

`home()` (`GET /home`) only clears `is_guest`. It does not touch `user_id`. Then it redirects to `/`.

`view_history(id)` handles the chat history detail page: anonymous users get redirected to the home page, logged-in users get the page rendered after an ownership check, and guests get an empty shell that the frontend fills in by reading `sessionStorage`. `view_image_history(id)` handles the image history detail page, and its rule is different: both guests and anonymous users are redirected straight to the home page. There is no empty-shell case here, because image history offers guests no record at all, in any form.

`_get_authenticated_user_id()` is the shared guard function for chat history, image history, Claude, Gemini, and quota query routes. Guests and anonymous users are always treated as unauthenticated and get a 401.

The pure functions for model fallback are `determine_actual_model()` (text) and `determine_actual_image_model()` (image); the rules are in section 6. `init_result_object()`/`init_image_result_object()` build the standard result dict. `detect_and_truncate()` does duplicate detection and blocked-word filtering. `parse_peer_review_json()` extracts a score and a comment from a peer review answer; if parsing fails it falls back to `(80, original_text)`.

`test_g4f_provider()` and `test_g4f_image_provider()` are the core test functions for each of the two chains. Their retry logic and error classification order are fully independent of each other; the exact order is in section 6. `run_peer_review()` runs a single peer review request. `compare_providers()` runs the two-stage concurrent flow (test, then peer review); logged-in users trigger `save_chat_history()`. `generate_images()` runs the single-stage concurrent flow; logged-in users trigger `save_image_history()`, and there is no local file cleanup step anymore.

`call_claude_model(prompt, model_key, user_api_key=None)` is the core function for official Claude calls. If `user_api_key` has a value, the client is built with the user's own key; otherwise it is built with a zero-argument constructor that reads the developer's key from the environment. This is the single branch point for key routing, and no caller should ever bypass this function to instantiate the client itself. The key rule for exception classification: to detect "developer account balance exhausted," do not check a fixed status code. Instead check whether `error.message` contains the phrase "credit balance" (this is a stable signal, verified against a real account; the 403 + `billing_error` combination hinted at in the official docs is kept only as a compatibility fallback, not the primary check).

`call_gemini_image_model(prompt, model_key, user_api_key=None)` is the core function for official Gemini calls, an independent implementation of the same "key routing plus error classification" pattern as Claude. Detecting quota exhaustion checks `status_code == 429`, also a signal verified against a real account. This function reads exception attributes with `getattr()` duck typing on purpose. Do not change it to import specific exception classes, because those classes have no stable public import path in the google-genai package.

`_append_claude_result_to_history()` and `_append_gemini_result_to_image_history()` are thin wrapper functions that append this call's result to an existing history record. If `history_id` is empty, they just skip. If the append fails, it is only logged; it never affects the response for this request.

`quota_status()` (`GET /api/quota-status`) returns `{claude: {used, limit}, gemini: {used, limit}}`, using the same auth guard. `apikey_config()` (`GET /apikey-config`) only renders the page and has no login guard, because the page itself makes no request that needs permission; it only stores data in the browser's `localStorage`.

### Key points in `auth/db.py`

On init, it first looks for a local `firebase-key.json`, and only falls back to `ApplicationDefault()` (for GAE) if that file is not found. It must check whether the key file exists first, because `ApplicationDefault()` resolves credentials lazily, so its constructor throwing an exception cannot be used as the signal.

Any exception sets `FIREBASE_AVAILABLE` to `False`. Every history CRUD function checks this flag internally; none of them rely on the caller to check it. Except for create operations, every other operation reads the document first to verify that its `user_id` field matches. If it does not match, or the document does not exist, the operation is rejected and a fallback value is returned.

All queries use the new form `.where(filter=FieldFilter(field, op, value))`. Do not use the deprecated positional form `.where(field, op, value)`, which fills the logs with deprecation warnings.

`append_chat_history_result()`/`append_image_history_result()` can only append to a record that already exists; they cannot create a new one. The append logic is: read the existing results list, append the new result, re-sort using "success first, then shorter response time first," then write the whole thing back.

The `pinned_at` field controls pin sort order: pinning writes `SERVER_TIMESTAMP`; unpinning removes the field entirely with `DELETE_FIELD` (not by setting it to `None`). The sort rule is: within the pinned group, sort by `pinned_at` ascending; within the unpinned group, sort by `created_at` descending. Both chat history and image history follow this same rule.

`get_chat_history_list`/`get_image_history_list` both do a single-field equality query only. Sorting and pagination happen in the Python layer, which avoids depending on a composite index that would need to be created by hand in the Firebase console.

`get_claude_free_tier_usage()`/`increment_claude_free_tier_usage()` read and write the `claude_free_tier_usage` integer field on the document in the `users` collection. There is no need to pre-write an initial value when a user is created; reading with `.get('claude_free_tier_usage', 0)` as a fallback is enough. The increment operation uses `firestore.Increment(1)` for an atomic increment; do not read then write in two separate steps, to avoid a race under concurrency. Checking the quota and actually incrementing it are two separate Firestore operations with no transaction between them; this is a deliberate simplification. `get_gemini_free_tier_usage()`/`increment_gemini_free_tier_usage()` form a fully parallel set, reading and writing an independent `gemini_free_tier_usage` field. The two counters share nothing.

### `auth/routes.py`

Every route has an outer `try/except`, and on error it responds through `flash()`. A successful login or registration writes `session['user_id']`/`username` and clears `is_guest`. Logging out clears all three session keys. `/profile` first checks `session['user_id']` and redirects to the login page if it is missing.

### Key points in the frontend templates

`templates/index.html` uses a two-column layout: a 260px-wide dark sidebar on the left, and the main content area on the right. The sidebar uses `position:sticky` plus a pure CSS `calc()` height, instead of computing pixel heights in JS by hand. This avoids rounding errors that would otherwise stack up with the page zoom.

Text-to-image mode and chat mode are two mutually exclusive containers, switched by `switchToImageMode()`/`switchToCompareMode()`. Image provider checkboxes must use their own separate classes (`.image-provider-checkbox`/`.image-provider-trigger`) and must not share classes with the chat form, because some queries in the project use `querySelectorAll` without scoping to a container, and a shared class name would let the two forms cross-contaminate each other, causing wrong data to be submitted or the code to crash. This rule applies across the whole project: any new provider card must use its own separate class, never reuse someone else's.

The Recents sidebar supports both a chat history mode and an image history mode, switched by the `sidebarMode` variable, and both physically share the same `#sidebarRecents` container. The image version of Recents is only open to logged-in users; guests see a lock message and no network request is ever made. Chat history falls back to a sessionStorage mirror for guests; image history has no fallback at all for guests. This asymmetry is intentional.

Both the chat form and the image form use a "four-section" layout: first the frontier provider selection area (Claude or Gemini cards), then the matching model dropdown, then the free g4f provider checkbox area, then the free model dropdown. The frontier provider area and the free provider area are two separate containers and must not be merged.

The Claude card (`#claudeProviderCard`) and the Gemini card (`#geminiProviderCard`) must each use their own separate class, and must not mix with any existing class. Guests and anonymous users see a grayed-out card with the message "Log in to unlock frontier models." When the form is submitted, if these frontier cards are checked, the frontend sends one extra separate request after getting the g4f results, and merges the result into the same rendered list.

Every piece of user-visible text on the page must be in English. No Chinese text is allowed there. This rule does not govern code comments or this document itself; those can still be written in Chinese.

For scrollbars, the project draws its own draggable scroll indicator, and the native scrollbar is fully hidden. When a custom dropdown panel closes, it must use `max-height:0` plus `overflow:hidden`, not just `opacity:0`/`visibility:hidden`; otherwise the invisible box would still expand the page's scrollable area.

## 5. Core execution flow

1. On startup: load environment variables, register the auth blueprint, initialize Firebase, and probe whether g4f/anthropic/google-genai can be imported correctly.
2. Visiting `/`: `index()` checks login state and decides whether to render the home page or the main app page.
3. Guests go through `/api/auth/guest`; login and registration go through their own forms.
4. Chat comparison goes through `/api/compare`: it tests each provider concurrently first, runs peer review if the conditions are met, sorts the results, saves history for logged-in users, and returns the results.
5. Text-to-image goes through `/api/generate-images`: single-stage concurrent testing, sorting, saving history for logged-in users, and returning results. There is no peer review here.
6. A Claude call goes through `/api/claude-chat`: auth guard, check whether the user brought their own key, check quota, call the official API, classify the error, update the quota counter, and append to the history record.
7. A Gemini call goes through `/api/gemini-image`, following a flow fully symmetric to Claude, just for the image scenario instead.

## 6. Core business rules

### The three identity states

An anonymous user has no `user_id` and no `is_guest`. They see `home.html` on the home page, and nothing is ever stored for them. A guest has `is_guest=True`. They see the main app page plus a guest badge, and their data lives only in frontend memory and sessionStorage; nothing is written to the database. A logged-in user has `user_id`, and their data syncs with Firestore. These three keys are mutually exclusive: whenever `user_id` is present, `is_guest` must already be cleared, and the same holds in reverse.

### Model fallback rules

Text models follow three rules: if the requested model is in the mapping table, use it directly; if it is unsupported or not specified, use the first model in the mapping table; if the provider has no model config at all, fall back to `gpt-3.5-turbo`. Image models only follow the first two rules, with no third fallback: if the provider is not in the mapping table, it returns `None`, and the frontend shows it as `default`.

### Peer review rules

The trigger condition is: at least 2 providers were tested, and at least 2 of them succeeded. Every successful answer is reviewed by every other successful answer, but never reviews itself. If parsing a peer review result fails, it falls back to a score of 80 plus the raw text. A provider that failed does not take part in peer review at all: it neither reviews others nor gets reviewed.

### Sort rule

Successful results come first. Among results with the same success state, the one with the shorter response time comes first. Both the text chain and the image chain share this same sort expression.

### Error message classification order

On the text side, content moderation errors (like an Azure OpenAI moderation block) must be classified before network errors, because retrying a moderation error is pointless, and misclassifying it as "system busy" would push the user into a useless retry.

On the image side, GPU quota exhausted errors must be classified before network errors, because a quota exhausted error should never be retried: retrying does nothing for an already-exhausted quota, and it adds pressure to an already tight free resource pool.

### Image generation retry rule

Only transient rate-limit errors, like a 429 or a full queue, get retried, and only once, waiting 2 to 3 seconds of random jitter before the retry. GPU quota exhausted errors and content moderation errors are never retried.

### Image generation timeout budget

The default advisory timeout is 40 seconds. The outer timeout is not a hardcoded constant; it is computed on the fly with the formula `2 * advisory + 5 second buffer`, which comes out to 85 seconds in the default case. It uses double the advisory time because a retry can run up to two attempts, and each one might run close to the full advisory time before finishing; a single multiple of that time plus a small buffer is not enough. `AnyProvider`, an aggregator-style provider, measurably takes longer, so it gets its own advisory budget of 70 seconds; its outer time is still computed by the same formula, not hardcoded separately.

If some provider in the future needs more time, give it its own advisory override instead of raising the global default across the board, which would slow down the worst-case wait time for every provider batch.

### Claude access control

Guests and anonymous users are always blocked, with two layers of defense: the frontend shows a grayed-out card, and the backend returns 401. There is no third, degraded tier. Claude is simply unavailable to guests, unlike chat history, which at least gives guests a client-side temporary record.

Each account gets `CLAUDE_FREE_TIER_LIMIT` (currently 10) free calls. This is only checked and consumed when the user did not bring their own key, and only a successful call consumes it; a failed call does not count. Once the quota is used up, the backend blocks the request outright and never calls the official API at all, so it never consumes any of the developer account's own call budget. There is no transaction protecting the gap between checking the quota and incrementing it; this is a deliberate simplification.

One click consumes at most one quota unit. Under the current architecture this invariant holds automatically, because chat only has one frontier provider: Claude.

A user can enter their own key on the `/apikey-config` page. It is stored in the browser's `localStorage`, and every later request carries it in the `X-User-Claude-Key` header. The backend never persists a user's personal key; it only lives in the browser and for the lifetime of a single request.

When the developer account's balance is exhausted, this is converted into a `SERVER_CREDITS_EXHAUSTED` error code and returned as a 503, and it does not count against the user's free quota.

### Gemini access control

Fully parallel to Claude, just with "chat" swapped for "image generation." The quota field is `gemini_free_tier_usage`, fully independent from Claude's quota and sharing nothing with it. Developer account quota exhaustion is converted into a `SERVER_QUOTA_EXHAUSTED` error code. A personal key goes through the `X-User-Gemini-Key` header, and it is likewise never persisted on the backend.

Gemini's verification coverage is smaller than Claude's: only the quota-exhausted scenario has been verified against a real, zero-quota account. The "success path with sufficient quota" and the "403 from an invalid key" scenarios are only backed by reading the docs and by mock data, with no real-account, end-to-end verification yet. This is a known verification gap, to be closed once a real key with positive quota becomes available.

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

### Claude Result

```python
{
    'provider': 'Claude', 'success': bool, 'response': str, 'error': str,
    'response_time': float, 'model': str, 'type': 'anthropic',
}
```

A third DTO, similar in shape to LLM Result but independent from it: the `type` value is different, and there is no `peer_reviews`.

### Gemini Image Result

```python
{
    'provider': 'Gemini', 'success': bool, 'url': None, 'b64_json': str | None,
    'error': str, 'response_time': float, 'model': str, 'type': 'google_genai',
}
```

A fourth DTO, similar in shape to Image Result but independent from it: the official API returns the image bytes directly as base64. In the response returned for this request, this DTO's `url` is always `None`. But before it is appended to the Firestore history record, `_persist_image_result_local_copy()` converts `b64_json` into a local file and swaps it for a `url` instead (see the update log in section 14 for why). So the persisted copy read back from history has a non-empty `url` and a `None` `b64_json`. ChatGPT's Image Result (`type='openai_image'`) goes through this same persistence logic, with the same field shape.

### Firestore collection layout

The `users` collection stores username, email, password hash, creation time, and the two quota fields `claude_free_tier_usage` and `gemini_free_tier_usage`.

The `history` collection (logged-in users only) stores chat history, with fields `user_id`, `title`, `prompt`, `results`, `created_at`, `is_pinned`, `pinned_at`. The `results` array may mix g4f-shaped results with Claude-shaped results, so rendering code must defensively handle the case where `peer_reviews` may not exist.

The `image_history` collection (logged-in users only) has a similar structure, but is a fully separate collection storing image-type DTOs. Its `results` array may mix g4f image results with Gemini results.

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

The image history function set has identical names and an identical contract, just against a different collection. Checking whether `toggle_pin` failed must use `is None`, because `False` is a valid success result.

The Claude and Gemini quota counter functions (`get_claude_free_tier_usage`/`increment_claude_free_tier_usage`, etc.) have no concept of ownership checking, because `user_id` comes directly from the session and there is no need to look up a document to confirm it.

## 8. External interfaces

Chat: `GET /api/providers`, `POST /api/compare`, `POST /api/test-single`, `GET /health`.

Claude (login required): `POST /api/claude-chat`, body is `{prompt, model, history_id}`, optionally with an `X-User-Claude-Key` header.

Gemini (login required): `POST /api/gemini-image`, body is `{prompt, model, history_id}`, optionally with an `X-User-Gemini-Key` header.

Quota query (login required): `GET /api/quota-status`, returns `{claude: {used, limit}, gemini: {used, limit}}`.

Text-to-image: `GET /api/image-providers`, `POST /api/generate-images`, `GET /media/<filename>`.

Page routes: `GET /`, `GET /home`, `GET /history/<id>`, `GET /image-history/<id>` (login required, guests/anonymous get redirected), `GET /apikey-config` (no login guard).

Auth: `/login`, `/register`, `/logout`, `/profile`. Guest: `POST /api/auth/guest`.

Chat history (login required): `GET /api/history`, `PATCH /api/history/<id>/title`, `DELETE /api/history/<id>`, `POST /api/history/<id>/toggle-pin`. Image history routes have the same structure, with the prefix swapped to `/api/image-history`.

### Third-party integrations

g4f is used to call free channels with no credentials. The Firebase Admin SDK uses a local key file locally, and ADC on GAE. The official Anthropic API is the project's first integration that needs a real, paid key, and it wires up the `claude-sonnet-5` and `claude-haiku-4-5` models. The official Google Gemini API is the second integration that needs a paid key, wiring up three tiers of Nano Banana models. Gemini image generation through g4f's key-free path is still unavailable; that is a separate matter from the official paid API path, and the two should not be confused.

## 9. Known risks and limitations

Timeout values must stay in sync: the internal timeout for the peer review stage and the outer `future.result` timeout must be adjusted together, and the image generation advisory and outer timeouts must stay linked through their formula; do not hand-edit just one of them.

Files under `generated_media/` are not scoped per user, and they keep accumulating as long as their matching `image_history` record still exists; there is no automatic cleanup by age. Deleting a record explicitly does delete its matching files, but that only covers the "user actively deletes it" path. Continued growth in every other case is an accepted cost, in exchange for historical images staying viewable forever. The real long-term fix is to switch to shared storage such as Cloud Storage, plus a per-user quota, but that has not been built yet.

In production (GAE with multiple instances), local disk is independent per instance. If an image is written on instance A and a later request lands on instance B, that request gets a 404. This is a limitation of the local-disk storage architecture itself, unrelated to cleanup; fixing it requires switching to shared storage.

Both Claude's and Gemini's free quotas are counted per registered account, so anyone can register endless new accounts to get unlimited quota. There is currently no IP rate limiting or CAPTCHA protection. This is a known, unresolved problem. Three directions are being considered: IP-level rate limiting (first needs confirming whether a trustworthy client IP is even available under GAE deployment), a CAPTCHA, and email verification.

Gemini's error classification has only been verified against one real scenario (zero quota). The success path with sufficient quota, and the 403 path from an invalid key, have not been verified against a real account yet.

The frontend interaction layer (optimistic updates, animations, layout) has no automated tests. The project has not adopted any frontend test framework; verification relies on manual testing. If a frontend test framework is added later, the scenarios that have already been manually verified should be turned into formal test cases.

The ChatGPT API key input field is currently just a placeholder with no backend logic wired up. Both Claude and Gemini already have real storage and call chains connected.

Frontier text providers (Claude/ChatGPT/Gemini) can currently only append results to an existing history record; they cannot create a new record themselves, and they do not take part in g4f's concurrent scheduling. But starting 2026-07-07, they also peer-review each other and g4f through a separate `POST /api/peer-review` endpoint (see `run_cross_peer_review()`, and the update log in section 14). This is not a direct reuse of g4f's own scheduling logic; it is its own, separate cross-namespace scheduler. Frontier image providers (Gemini image/ChatGPT image) still have no concept of peer review.

## 10. Guide for extending and modifying

### Safe zone: adding a new text provider

After running the probe script to confirm it works, add it to the `G4F_PROVIDERS` list and to `PROVIDER_MODELS_MAP`. Optionally, give it an invisible style prompt and a peer review judge prompt. The frontend wiring is fully automatic; there is no need to touch any HTML or JS.

### Safe zone: adding a new image provider

Same process: add it to `IMAGE_PROVIDERS`/`IMAGE_PROVIDER_MODELS_MAP`. Do not let an image provider share a mapping table or a namespace with a text provider.

### Safe zone: adding a new page

If this page could ever be a redirect target, it must include the flash message display block, otherwise messages will keep piling up in the session.

### Safe zone: adding a new Claude/Gemini model

First confirm whether the official model ID has changed, then add one mapping entry in `CLAUDE_MODELS`/`GEMINI_IMAGE_MODELS`, and add one option to the frontend dropdown. There is no need to touch quota or permission logic; both of those functions are transparent to the model choice.

### Safe zone: adding a new frontier provider (like ChatGPT)

The template already reserves `#frontierProviderSelection`/`#frontierImageProviderSelection` for exactly this. When wiring one up, follow the existing pattern from Claude or Gemini: build an independent call function, model mapping, and quota constant on the backend, with its own route reusing the auth guard. On the frontend, add a new card with its own independent class. If there are multiple models to choose from, add an independent dropdown; do not reuse an existing one. In `apikey-config.html`, wire the matching input to its own independent `localStorage` key. Do not let the new provider fall into g4f's namespace or take part in g4f's concurrent scheduling.

### Danger zone: logic not to touch

Do not change the 7-field text contract, the 4-field peer review contract, or the 8-field image contract. Do not let text and image providers share a mapping table or a scheduling path. Do not let image provider checkboxes reuse the same class name as the text form.

Do not mix image history into the chat history collection, and do not let `generate_images()` call `save_chat_history()`. Do not let guests or anonymous users use the image version of Recents or the image history detail page.

Do not add a server-side proxy endpoint for image downloads; that introduces an SSRF risk. Downloads must happen in the browser. The `/media/<filename>` route itself is fine as is, because it only reads a file that has already been downloaded locally. Do not add "if the file is missing, fetch it from the URL parameter" logic to it.

Do not reintroduce any form of automatic cleanup for `get_media_dir()`, whether that is lazy cleanup by age or wiping the whole directory indiscriminately. Continuous growth of local disk is an accepted, deliberate cost.

Do not let the peer review and text-to-image timeouts drift out of sync. Do not remove the `provider_models_json`/`image_provider_models_json` injection in the root route. Do not reverse the classification order between content moderation errors and network errors, and on the image side do not reverse the order between GPU quota errors and network errors either; do not add a retry for GPU quota errors.

Do not change the image timeout formula back from "double the advisory plus a buffer" to "one times the advisory plus a fixed buffer"; a fixed buffer is not enough to cover a second retry attempt that also runs close to the full duration. Do not add a separate outer key to the per-provider timeout override table; the outer value must always be derived from the advisory value through the formula.

Do not set both `user_id` and `is_guest` in the session at the same time. Do not skip the `FIREBASE_AVAILABLE` check and call a CRUD function directly. Do not change the behavior of `GET /home`; it must not clear `user_id`. Do not remove the ownership check inside the history CRUD functions. Do not let the guest path call any history CRUD function. Do not use `if not new_pinned` to check whether a pin operation failed; it must be `is None`.

Do not let `get_chat_history_list` go back to a composite sort query on the Firestore side; sorting must stay in the Python layer. Do not change the pin sort field back from `pinned_at` to `created_at`. Do not bring back the deprecated pattern of "clicking a history item renders it in place into an editable form"; it must fully navigate to a read-only page. Do not switch the guest history storage medium from `sessionStorage` to `localStorage`.

Do not remove the `sticky` positioning or the CSS `calc()` fixed height on `.left-sidebar`. Do not bring back the native scrollbar. Do not add any "resubmit" form entry point back to the history detail pages.

Do not let guests or anonymous users call `/api/claude-chat` or `/api/gemini-image`; both must be fully unavailable to them, with no degraded experience at all. Do not let the Claude/Gemini cards reuse another provider's class. Do not merge the frontier provider selection area and the free provider selection area back into a single container. Do not let Claude/Gemini take part in g4f's concurrent scheduling or peer review.

Do not change the quota check order to "call the official API first, then check quota"; an over-limit request must be blocked first, so a real API call is never wasted. Do not let a request that brought its own key still check or increment the free quota. Do not persist a user's personal key on the backend. Do not count balance exhaustion or quota exhaustion against the user's free quota.

Do not let `claude_chat()`/`gemini_image_chat()` call `save_chat_history()`/`save_image_history()` themselves to create a new record; they can only append to an existing one. Do not let the append functions skip the ownership check, and do not let them turn into an endpoint that accepts arbitrary client-submitted content. Do not forget to swap the internal error code for a user-friendly message before appending to history on the balance-exhausted or quota-exhausted branch.

Do not reintroduce Chinese text anywhere user-visible, including page copy, flash messages, the message field in a JSON error body, or prompt text sent to an LLM. This rule does not govern code comments or this document itself.

Do not let the Trial Quota badge render for guests or anonymous users. Do not let the two quota constants reference the same value; their meaning and their fields are independent and must be adjustable separately. Do not let the frontend guess locally whether a call consumed quota; it must rely on the backend's real query endpoint. Do not change the quota query endpoint's guard to allow guests.

## 11. Build, run, and test commands

### Environment setup

```bash
python3 -m venv env
source env/bin/activate
pip install -r requirements.txt
```

### Prerequisites for running locally

You need a `firebase-key.json` in the project root, and a fixed `SECRET_KEY` in `.env`. `ANTHROPIC_API_KEY` and `GEMINI_API_KEY` are optional; the app starts fine without them, but the matching feature fails when actually called.

### Running

```bash
python main.py                    # default port 8080
PORT=5000 python main.py
gunicorn -b :8080 main:app        # simulates GAE
```

After changing anything under `templates/*.html`, you must restart the dev server, because template auto-reload is not enabled. A running process keeps caching the old template, and a hard refresh in the browser will not fix that.

Visit `http://localhost:8080`, and check status with `/health`.

### Automated tests

The project uses `unittest`, with test files under `tests/`. They fall into a few groups: white-box tests of internal functions (model fallback rules, DTO completeness, error classification, and so on), black-box tests of HTTP routes (request/response behavior for each route), auth-related tests, dedicated Claude and Gemini integration tests, regression tests for the English-only text policy, and regression tests for HTML structural integrity.

Commands to run the tests:

```bash
python -m unittest discover -s tests
python -m unittest discover -s tests -v
python -m unittest tests.test_main_whitebox
```

Frontend interactions (animations, optimistic updates, the scroll indicator) are not covered by unittest. The project has no frontend test framework, so this kind of verification relies on manual testing.

### Smoke tests

```bash
curl http://localhost:8080/health
curl http://localhost:8080/api/providers
curl -X POST http://localhost:8080/api/test-single -H "Content-Type: application/json" \
  -d '{"prompt": "What is 2+2?", "provider": "Yqcloud"}'
curl -X POST http://localhost:8080/api/compare -H "Content-Type: application/json" \
  -d '{"prompt": "Hello", "providers": ["Yqcloud", "OperaAria"]}'
curl -X POST http://localhost:8080/api/auth/guest
```

The Claude and Gemini endpoints need a logged-in session, for example:

```bash
curl -X POST http://localhost:8080/api/claude-chat -H "Content-Type: application/json" \
  -H "Cookie: session=<session cookie after login>" \
  -d '{"prompt": "What is 2+2?", "model": "claude-sonnet-5"}'

curl -X POST http://localhost:8080/api/gemini-image -H "Content-Type: application/json" \
  -H "Cookie: session=<session cookie after login>" \
  -d '{"prompt": "A single red apple", "model": "nano-banana-pro"}'
```

### Provider availability probe scripts

Only rerun these after a g4f library upgrade, when you suspect the existing conclusions are stale:

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

The `entrypoint` is `gunicorn -b :$PORT main:app`, the runtime is python312, and autoscaling runs 1 to 10 instances. `app.yaml` itself is committed to git as normal, but the `SECRET_KEY`/`ANTHROPIC_API_KEY`/`GEMINI_API_KEY` entries under `env_variables` only hold `"${VAR_NAME}"` placeholders, never real values. `gcloud` does not expand these placeholders automatically; before deploying, replace these three placeholders with real values locally by hand, and do not commit the replaced version. `firebase-key.json` is not deployed; GAE uses ADC instead.

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

Core chat path: the frontend submits the form, calls `/api/compare`, tests each provider concurrently, collects the results, runs peer review concurrently if the conditions are met, sorts, saves history for logged-in users, and returns JSON.

Core text-to-image path: calls `/api/generate-images`, tests each image provider concurrently (each with its own timeout budget), sorts, saves history for logged-in users, and returns JSON, with no peer review. Images are downloaded locally at the same time, served through the `/media/<filename>` route, and these files are now kept permanently with no cleanup.

Core Claude path: when the Claude card is checked, the normal `/api/compare` request completes first and returns a `history_id`; then one extra `/api/claude-chat` request is sent (carrying that `history_id`), passing through the auth guard and the quota check, calling the official API, classifying the error, incrementing the quota counter on success without a personal key, appending to the history record if `history_id` is non-empty, and finally the frontend merges the result into the same rendered list. None of this ever goes through the concurrent scheduler, and it never creates a new history record on its own.

Core Gemini path: fully symmetric to Claude, just with the chat scenario swapped for the image scenario.

Core auth path: the login form is submitted, goes through the auth blueprint, queries Firestore, writes the session, and redirects to the home page.

Key invariants checklist: result sort order is always success first, then shorter response time first. `user_id` and `is_guest` are never both present at once. Any redirect target page must have a flash display area. The 7-field text contract and the 8-field image contract must not be broken. The error message classification order must not be reversed. The peer review trigger condition is at least 2 providers tested and at least 2 successes. Text providers and image providers keep strictly separate namespaces. History list queries only do a single-field query plus Python-layer sorting and pagination. Checking whether pinning failed must use `is None`. Guest data is never persisted: chat history mirrors into sessionStorage, and image history has no fallback at all. Page zoom is controlled by the `--page-zoom` variable, and any CSS involving viewport height must use `calc(100vh/var(--page-zoom))`. `history` and `image_history` are two separate collections and must not be merged. Claude and Gemini are both call chains independent from g4f; they cannot create a new history record on their own, only append to an existing one. All user-visible text must be in English. The navbar quota badge only renders for logged-in users, and its numbers must come from a real backend query, never a frontend guess.

Core files: all backend logic is in `main.py`; auth logic is in `auth/routes.py` and `auth/db.py`; frontend templates are in `templates/`, with `index.html` as the main app page.

## 14. Update log

`[Stop Generating] Reason: needed correct messaging when the developer account's balance/quota is exhausted while the user's trial quota still has some left, plus support for mid-flight cancellation. Changes: 1. Split the SERVER_CREDITS_EXHAUSTED/SERVER_QUOTA_EXHAUSTED messages in claude_chat()/gemini_image_chat() into two branches based on using_own_key: exhausting your own key tells you to check your own account, exhausting the developer key (while trial quota remains) tells you to contact the developer. 2. Added a frontend Stop Generating button (one each for Compare/Generate Images), using AbortController to cancel /api/compare, /api/generate-images, /api/claude-chat, /api/gemini-image requests, without clearing the prompt or the checkboxes. 3. Added a one-time refund ledger keyed by request_id (main.py's _PENDING_FRONTIER_REFUNDS plus /api/claude-chat/refund, /api/gemini-image/refund) and auth/db.py's decrement_claude_free_tier_usage()/decrement_gemini_free_tier_usage(), used to refund free quota that had already been incremented when a request is interrupted; the ledger only recognizes increments that really happened, and cannot be replayed to farm quota.`

`[Stop Generating history persistence] Reason: after clicking Stop mid-flight, the server still finished the run and wrote it into chat/image history, so users would later find a record in Recents they thought they had cancelled. Changes: 1. Added a request_id cancellation registry, main._CANCELLED_HISTORY_REQUESTS (_mark_request_cancelled()/_is_request_cancelled()), an in-memory, TTL-evicted structure in the same spirit as the quota refund ledger. 2. compare_providers()/generate_images() now accept request_id, check the registry before saving, and skip save_chat_history()/save_image_history() on a hit; added two login-free endpoints, POST /api/compare/cancel and POST /api/generate-images/cancel, for the frontend to call when Stop is clicked. 3. claude_chat()/gemini_image_chat() also check the registry before appending to history; /api/claude-chat/refund and /api/gemini-image/refund now unconditionally mark the same request_id as cancelled (whether or not a quota refund actually happened). 4. The frontend's compareForm/imageForm each generate their own request_id before sending the request; stopBtn/stopImageBtn, besides calling abort(), also fire off a cancel call to the matching endpoint.`

`[Deleting the currently open entry in history.html/image_history.html] Reason: deleting the entry currently being viewed on its detail page called window.location.href to navigate before the DELETE fetch finished; the navigation cancelled the in-flight fetch, so the entry was never actually deleted even though the user got bounced back to the home page (the text detail page bounced back to the default text generation view). Changes: both pages' deleteHistoryItem() now, for the "currently viewed" branch, await the DELETE call succeeding before navigating, and show a toast and stay on the page if it fails; in history.html, the guest branch was also moved to splice + persistGuestHistory() before navigating. Non-current entries still use the original optimistic delete. See tests/test_history_delete_current_entry_blackbox.py for the regression test.`

`[Image history delete navigation + local file cleanup] Reason: after the previous fix, deleting the currently open entry in image history still bounced back to the text generation view (it navigated to a bare '/' instead of image mode), and deleting an image never freed its local file under generated_media. Changes: 1. image_history.html's deleteHistoryItem() now navigates to '/?mode=image' after successfully deleting the current entry, matching this page's own "+ New" button, instead of bouncing back to text mode. 2. Added main.py's _delete_local_media_files_for_image_results(), which only handles results with type=='g4f_image' (Gemini results always have url=None and no local file); it parses the filename out of the url field (shaped like /media/<filename>?url=...) and deletes it inside get_media_dir(), constrained with os.path.basename. 3. DELETE /api/image-history/<id> now takes a snapshot of results via get_image_history_by_id() before deleting the Firestore record, and cleans up the matching local files after the Firestore delete succeeds; this is a targeted cleanup tied to the user's explicit delete action, not an automatic cleanup by age, so it does not violate the "no automatic cleanup" rule in sections 9/10. See tests/test_image_history_media_cleanup_whitebox.py and the new cases under TestDeleteImageHistoryEndpoint in tests/test_image_history_blackbox.py.`

`[Added ChatGPT/Gemini frontier providers] Reason: following the existing Claude/Gemini image architecture, ChatGPT and Gemini were wired up as frontier providers for the chat scenario, and ChatGPT was wired up for the image generation scenario too, all fully independent and parallel to the existing Claude/Gemini image providers. Changes: 1. Added the official openai SDK as a dependency; three independent call functions: call_chatgpt_model() (chat, models gpt-5.5/gpt-5.4-mini), call_gemini_text_model() (chat, models gemini-3.5-flash/gemini-3.1-flash-lite, type='google_genai_text'), call_chatgpt_image_model() (image, models gpt-image-2/gpt-image-1.5); added three new routes, POST /api/chatgpt-chat, /api/gemini-chat, /api/chatgpt-image, plus a /refund endpoint for each, with permission/quota/key-routing/refund/cancellation-registry rules that mirror Claude/Gemini image one for one. 2. Added three independent free quota fields (chatgpt_free_tier_usage/gemini_text_free_tier_usage/chatgpt_image_free_tier_usage, each capped at 10, sharing nothing with each other); auth/db.py added generic get_free_tier_usage()/increment_free_tier_usage()/decrement_free_tier_usage(field_name) shared by all three (Claude/Gemini image keep their own dedicated counter functions untouched). Two error classification helpers, _classify_openai_error()/_classify_google_genai_error(), are each shared by their SDK's own text/image calls. 3. On the frontend, index.html got three new provider cards with their own classes (chatgpt-provider-checkbox/gemini-text-provider-checkbox/chatgpt-image-provider-checkbox), each with its own model dropdown; a new factory function, createFrontierProviderController(), centralizes the logic shared only by these three new providers (checkbox toggling the model dropdown's enabled state, the FREE_TIER_EXHAUSTED dialog, the request, the Stop refund, and the Clear reset); Claude/Gemini image keep their own separate original implementations untouched. The Trial Quota badge changed from "one number per mode" to "one pill per provider, per mode." apikey-config.html's ChatGPT key input is now wired to localStorage (user_chatgpt_key) for real, no longer a placeholder; the Gemini key now covers both the text and the image scenario. /api/quota-status and _get_frontier_quota_context() now both return quota for all five providers. See tests/test_new_frontier_providers.py.`

`[Two ChatGPT text/image bug fixes] Reason: gpt-5.5/gpt-5.4-mini reject the old parameter name max_tokens (they require max_completion_tokens), so text chat failed 100% of the time with a 400; ChatGPT image generation succeeded but then failed to append into image_history with a Firestore 400, "Property array contains an invalid nested entity." Verified against the real project: once a b64_json string embedded directly in the results array (an array of maps) crosses roughly 1MB in length, Firestore rejects the entire write with this error, and the gpt-image family's default output almost always crosses that threshold (the same applies in theory to Gemini images on the same code path, just usually smaller, so it has not actually been hit yet). Changes: 1. call_chatgpt_model()'s max_tokens was renamed to max_completion_tokens. 2. Added main._persist_image_result_local_copy(): before appending to image_history, it decodes the result's b64_json to a file under get_media_dir() (the same directory/route as g4f images), swaps it for a url, and clears b64_json; the result object returned to the frontend for this request is untouched and still carries the full b64_json. Both _append_gemini_result_to_image_history()/_append_frontier_image_result() run this function before appending. 3. _delete_local_media_files_for_image_results()'s type filter was widened from g4f_image only to also include openai_image/google_genai, otherwise local files created by these two providers' persistence would leak forever whenever a user deletes that history record. See tests/test_new_frontier_providers.py (max_completion_tokens regression) and tests/test_image_history_media_cleanup_whitebox.py (_persist_image_result_local_copy() and the widened cleanup filter).`

`[Frontier model personas + cross g4f/frontier peer review] Reason: the three frontier text providers (Claude/ChatGPT/Gemini) previously had neither an invisible persona nor any part in peer review at all (peer review only covered the g4f namespace); now they need personas grounded in their real companies' philosophies, and need to peer-review both the free g4f providers and each other. Changes: 1. Added FRONTIER_STYLE_PROMPTS_MAP (6 frontier model_keys, answering personas grounded in Anthropic's/OpenAI's/Google's real philosophies) and FRONTIER_JUDGE_PROMPTS_MAP (matching judge personas, merged into PEER_REVIEW_PROMPTS_MAP via .update(), independent of whether g4f is available); call_claude_model()/call_chatgpt_model()/call_gemini_text_model() all gained an apply_persona parameter (defaults to True, applied when answering; passed as False during peer review, the same way g4f's run_peer_review() never applies ROUTE_PROMPTS_MAP). Also lightly reworded the 4 free personas so they still read as clearly distinct sitting alongside the new frontier personas. 2. compare_providers() no longer runs peer review itself; it only initializes the peer_reviews:[] field. Added run_frontier_peer_review()/run_cross_peer_review() and POST /api/peer-review (added auth/db.py's update_chat_history_peer_reviews(), which writes the final peer review results back into an existing history record, update only, never create) — the frontend calls this new endpoint once results from both g4f and all three frontier providers are in, triggering two-way peer review across every successful result. 3. /api/peer-review has a safety cap: it accepts at most MAX_PEER_REVIEW_ENTRIES (10) results, and validates each entry's provider/model/type combination against the matching *_AVAILABLE flag one by one, silently dropping invalid entries; login is required as soon as any valid result comes from a frontier reviewer, while a pure g4f list stays guest-accessible; reviewing reuses the same key used for answering (the X-User-*-Key header), without checking or consuming any extra free quota. See tests/test_peer_review_cross_frontier.py; the existing peer review cases in tests/test_main_blackbox.py and tests/test_main_graybox.py have been updated to assert against the new architecture.`

`[UI consistency and copy polish] Reason: navigating between auth pages like /profile and /apikey-config caused a visible navbar jump, the guest black banner disappeared while scrolling, and a few pieces of copy and colors were inconsistent. Changes: 1. auth/base.html got the same --page-zoom:0.88 scaling, hidden native scrollbar, and edge-to-edge nav-container/.nav-left(260px) layout as index.html, removing the jump on navigation. 2. index.html/history.html now wrap .navbar and .guest-banner together in a new .page-header-sticky container (position:sticky;top:0), so the banner no longer disappears while scrolling; history.html/image_history.html's .nav-container dropped the outdated justify-content:space-between, switching to .nav-links{margin-left:auto} to match index.html. 3. .confirm-modal's max-width went from 360px to 440px (shared by things like the quota-exhausted dialog); added .btn-stop (amber) to replace the old black-and-white color scheme on the Stop Generating button; index.html's "Download PNG" became "Download Image" to match image_history.html; "Continue as guest" became "Continue as Guest," and "Testing providers.../Generating images..." became Title Case. 4. Flash messages in auth/routes.py and main.py now consistently end with a period (including "You have been logged out."). See tests/test_ui_consistency_polish_blackbox.py.`

`[SECRET_KEY leak fix] Reason: the real SECRET_KEY in app.yaml had been committed to git in plain text (this had already happened and been rotated once before). Changes: 1. Rotated a new SECRET_KEY. 2. Briefly moved app.yaml entirely out of git in favor of an app.yaml.example template, later reverted at the user's request back to the simpler approach (see the next entry). The old key still sits in past commits; if this repo was ever public, rewriting git history should be evaluated separately.`

`[Restored the simple deploy setup] Reason: the user wants a personal project to keep the simplest possible deploy setup, with no secret manager, no CI pipeline, no multi-environment config, and no separate deploy script; just app.yaml plus gcloud app deploy app.yaml. Changes: 1. Deleted app.yaml.example; app.yaml is committed to git normally again, and removed from .gitignore. 2. app.yaml's env_variables only keep the placeholder strings "${SECRET_KEY}"/"${ANTHROPIC_API_KEY}"/"${GEMINI_API_KEY}", with no real values; gcloud does not expand these placeholders automatically, so they need to be replaced with real values by hand locally before deploying, and the replaced version should not be committed. 3. No secret manager, CI/CD, multi-environment yaml, or deploy script was introduced.`

`[g4f free provider retest] Reason: BlackForestLabs_Flux1Dev/StabilityAI_SD35Large kept reporting GPU quota exhausted, so they needed retesting for a replacement, alongside a full search for new free text providers. Changes: 1. The shared free HuggingFace ZeroGPU quota pool that both of these depend on has been globally exhausted (reproduced 100% across several rounds); no free, key-free replacement was found on retest, so both were removed from IMAGE_PROVIDERS/IMAGE_PROVIDER_MODELS_MAP, leaving PollinationsImage/AnyProvider/OperaAria as the three remaining image providers. 2. After a full scan, added 3 new free text providers to G4F_PROVIDERS/PROVIDER_MODELS_MAP: CohereForAI_C4AI_Command (command-a-03-2025/command-r-08-2024), Groq (openai/gpt-oss-120b), OpenRouterFree (openrouter/free), each with a persona added to ROUTE_PROMPTS_MAP and a judge prompt added to PEER_REVIEW_PROMPTS_MAP. 3. Candidates that were excluded: Perplexity (roughly 50% chance of triggering a JsonConversation error from the g4f library itself, too unstable), OllamaSwarm (responses carry un-stripped raw `<think>` reasoning text, and it frequently times out on time-to-first-token). See tests/test_provider_registry_new_providers.py; the research scripts and findings docs under availability_g4f/ have been updated to match.`

`[Frontier-only mode] Reason: let a user lock the app to only use frontier providers (Claude/ChatGPT/Gemini, etc.) with one click, skipping the free g4f providers entirely. Changes: 1. Both the chat form and the image form in index.html gained a `.btn-frontier-toggle` button (`#frontierOnlyToggle`/`#frontierOnlyToggleImage`); clicking it disables and force-unchecks every free provider checkbox (`.is-locked` grays them out), with the same login guard as the other frontier cards (the whole card is disabled for guests/anonymous users). 2. On submit, if this mode is on and no frontier provider is checked, the frontend shows an alert and blocks the request. 3. `/api/compare` and `/api/generate-images` gained a new request body field, `frontier_only` (default false): when true, `providers_to_test` is forced empty, skipping the entire g4f concurrent stage (this does not fall back to the old legacy meaning of "empty providers array means test everything"), but it still calls `save_chat_history()`/`save_image_history()` normally to write an empty-results history record, for later frontier calls to append to. See tests/test_frontier_only_mode.py.`

`[Frontend visual polish] Reason: Frontier-only mode needed to also lock the free model dropdown, not just the checkboxes; the Trial Quota pill needed to gray out once its quota was exhausted; the frontier model `<select>` elements still had the browser's default blue-and-white look; and peer review scores needed color tiers by range. Changes: 1. `.custom-select-wrapper.is-locked` grays out the free model custom dropdowns (modelSelect/imageModelSelect); the click handlers for customTrigger/imageCustomTrigger now check for this class and block expansion, with frontierOnlyToggle(Image) and the Clear button both toggling/clearing it in sync. 2. Added `.trial-quota-badge.is-exhausted` styling, with the initial Jinja render deciding it from `limit - used <= 0`, and `updateOneQuotaBadge()` toggling it on the same condition after every refresh. 3. All 5 Claude/ChatGPT/Gemini model-select-group `<select>` elements gained `appearance:none` plus a custom arrow and a dark focus outline, matching the look of `.custom-select-trigger`. 4. `.review-score-badge` was split into three tiers, `score-low` (0-33, #E8F5E9), `score-mid` (34-66, #66BB6A), `score-high` (67-100, #1B5E20); `renderPeerReviews()` in both index.html and history.html now uses the new `scoreColorClass()` helper to decide between them. See tests/test_frontier_ui_polish.py.`

`[Peer review reliability fix] Reason: with 6+ providers, a single peer review round could send several concurrent requests to the same free provider (PollinationsAI/Groq/OpenRouterFree, etc.) at once, triggering a 429 storm; the retry count was too low and the backoff too short, so reviews kept falling into the 80-point fallback, and the fallback text sometimes still carried a leftover, unparsed second JSON blob that made the score not match the text. Changes: 1. `run_peer_review()`'s retry count went from 1 to 2 (3 attempts total, `PEER_REVIEW_MAX_ATTEMPTS`), with backoff that grows with the attempt number (`_peer_review_retry_wait()`) instead of a fixed 2 to 3 seconds. 2. `run_cross_peer_review()` gained a lock per reviewer identity (kind + provider), serializing tasks aimed "at the same reviewer" so the same rate-limited backend never gets hit by a whole concurrent batch at once; the outer `future.result()` timeout is now computed on the fly with the formula `max_reviewer_queue_depth * _peer_review_single_worst_case_seconds() + PEER_REVIEW_FUTURE_TIMEOUT_BUFFER`, instead of a hardcoded 32. 3. `parse_peer_review_json()` now scans for every candidate JSON substring by tracking brace depth (`_extract_balanced_json_candidates()`), and tries parsing starting from the last one, fixing the bug where a model's self-correction, or two JSON blobs appearing in the same text, got stitched together head-to-tail into an invalid string. See tests/test_peer_review_reliability.py; cases in `test_main_whitebox.py`/`test_main_graybox.py` that depended on the old retry count or the old timeout constant have been updated to match.`

`[Frontier select color patch + free model dropdown anchoring fix] Reason: the earlier "frontend visual polish" entry's `appearance:none` only repainted the collapsed `<select>` box; the native `<option>` popup, once expanded, still used the system's default blue-and-white color. Separately, the "Select free models" dropdown used `position:absolute`, and with no free provider checked (which merges every provider's models into the longest possible list), expanding it pushed the document's scrollHeight taller than the `.app-layout` box itself, leaving a blank gap below the anchored `.left-sidebar` when scrolled to the bottom. Changes: 1. Added black-and-white colors for the 5 model-select-group `<select> option`/`option:checked` elements (white background, black text when unselected; black background, white text when selected); the native popup's hover state is a browser platform limitation that CSS cannot reach. 2. `.custom-options` (`#customOptions`/`#imageCustomOptions`) switched to `position:fixed`, so it no longer counts toward the layout size of any non-fixed ancestor; added a shared `positionCustomOptions()` that computes top/left/width right before expanding, using `getBoundingClientRect()` (dividing by `--page-zoom` to convert back to logical pixels), and collapses both dropdowns on a real page scroll to prevent them from floating out of place, with `overscroll-behavior: contain` added while expanded to stop internal scrolling from chaining up to the outer page. See tests/test_free_model_dropdown_fixed_and_native_select_options.py.`

`[Free model dropdown rebuilt per provider] Reason: the shared "Select free models" dropdown used to merge the models of every checked provider into one union list, and applied whatever was selected globally to every checked provider; a user could not respectively pick a different model per provider, and could not see which model each provider was actually using. Changes: 1. Both the chat form and the image form gained providerCheckOrder/imageProviderCheckOrder (a queue tracking check order) plus providerModelSelections/imageProviderModelSelections (each provider's own remembered model); the shared dropdown now always binds to "whichever provider was checked most recently" (activeModelProvider()/activeImageModelProvider()), showing only that one provider's own model list; with 0 providers checked, the dropdown is left with only the default option and grayed out (reusing is-locked, decided by the new, centralized updateModelSelectLockState()/updateImageModelSelectLockState(), where locked = frontier-only mode or no bound target). 2. The small label under each provider card (refreshProviderModelLabel()/refreshImageProviderModelLabel()) now updates live with that provider's own selection: "Default: X" (no override, or the selection equals the default) versus "Selected: Y" (the selection differs from the default). 3. On submit, a single global model field is no longer sent; instead a provider_models dict ({provider: model}) is sent; main.py's compare_providers()/generate_images() gained a matching per-provider override parameter, falling back to the old global model field for any missing provider (for backward compatibility), and falling back to each provider's own default model after that. Clear Results also clears both of these state objects. See tests/test_per_provider_model_selection.py (backend) and tests/test_provider_model_dropdown_per_provider.py (frontend logic); two assertions in tests/test_frontier_ui_polish.py that checked the old locking implementation's details were updated to check the new centralized locking functions instead.`

`[Frontier model select rebuilt as a custom dropdown component] Reason: the CSS patch from the earlier "color patch" entry for native <select>/<option> elements could never fully deliver "selected is black, unselected is white, hover turns green, click applies instantly" — a native dropdown popup is composited by the operating system, so :hover and the selected background color can never be fully overridden by CSS, and the expanded popup kept its system blue-and-white highlight. Changes: all 5 model selects, for Claude/ChatGPT/Gemini (text) and Gemini/ChatGPT (image), were rebuilt as a custom component matching the same structure as "Select free models" (.custom-select-wrapper/.custom-select-trigger/.custom-options/.custom-option, via a new shared setupFrontierModelDropdown()); the native <select> is kept but hidden with display:none, used only to store the .value/.disabled state, so the existing enable/disable logic (syncClaudeModelSelectEnabled(), createFrontierProviderController(), etc.) needed zero changes, syncing the is-locked graying on the visual layer through a MutationObserver watching the disabled attribute. Removed the now-obsolete native select color CSS. See tests/test_frontier_model_custom_dropdown.py; tests/test_free_model_dropdown_fixed_and_native_select_options.py and tests/test_frontier_ui_polish.py had their assertions about the old native select color rules removed, and two `<label for=...>` assertions in tests/test_main_blackbox.py were updated to match the new `<label>` with no for attribute.`

`[Frontier model select black highlight delay] Reason: after rebuilding these as a custom dropdown component, clicking an option snapped the black highlight to the new option instantly, at the same time as the panel's collapse animation, which looked jarring; this mirrors the MODEL_HIGHLIGHT_DELAY_MS handling the "Select free models" dropdown already had. Changes: setupFrontierModelDropdown() was split into syncTriggerLabel() (updates the label text/select.value/change event immediately) and syncSelectedHighlight() (toggles the .selected class); clicking an option now delays the latter by MODEL_HIGHLIGHT_DELAY_MS (matching the panel's collapse animation) before running it, clearing that timeout and syncing immediately if the dropdown is reopened; the MODEL_HIGHLIGHT_DELAY_MS constant was moved above setupFrontierModelDropdown() so it is shared with the free model dropdown. See the new TestFrontierModelDropdownHighlightDelay in tests/test_frontier_model_custom_dropdown.py.`

`[Hide failed blind reviews instead of showing a fallback] Reason: showing a fallback message after a peer review failed ("80 pts: The system is busy...") added no value, and waiting for the full retry chain to complete slowed things down; response speed for a provider's own answer matters more than peer review does. Changes: 1. run_peer_review()/run_frontier_peer_review() now return None on failure instead of a fallback review_result; run_cross_peer_review()'s existing None-filtering logic now hides that entire review, without affecting the display of that provider's own response. 2. PEER_REVIEW_MAX_ATTEMPTS went from 3 down to 2 (1 retry), lowering the worst-case time for a single review, with the future_timeout formula updating automatically to match. See tests/test_peer_review_hidden_on_failure.py.`

`[Post-GAE-deploy bug fixes] Reason: real deploy surfaced three bugs. (a) Groq/OpenRouterFree return a 100% "Access from cloud provider blocked" 403 from any cloud IP. (b) .gcloudignore's ".env" rule was accidentally commented out, so .env got bundled into deploys; combined with app.yaml pre-setting ANTHROPIC_API_KEY/GEMINI_API_KEY to literal "${...}" placeholders whenever the deployer forgets to hand-replace them, load_dotenv()'s default no-override behavior meant .env's real keys never took effect for those two (OPENAI_API_KEY worked only because app.yaml never declared it, so nothing blocked it). (c) gunicorn's default 30s sync worker timeout is shorter than this app's own peer-review/image-generation worst-case budgets, so workers were killed mid-request under real cloud latency, making blind review intermittently vanish entirely. Changes: 1. Removed Groq/OpenRouterFree from G4F_PROVIDERS/PROVIDER_MODELS_MAP/ROUTE_PROMPTS_MAP/PEER_REVIEW_PROMPTS_MAP. 2. Restored the active ".env" ignore rule in .gcloudignore; added OPENAI_API_KEY to app.yaml's env_variables (placeholder form, same as the other three). 3. app.yaml's entrypoint now sets `--timeout 300` on gunicorn. See tests/test_provider_registry_new_providers.py (updated) and tests/test_gae_deploy_config_whitebox.py (new).`

`[ChatGPT image history entry vanishing] Reason: on a real GAE deploy, a successful ChatGPT image generation would render fine live but then be completely absent from the saved image_history record when reopened via Recents. Root cause: _persist_image_result_local_copy() fell back to returning the original, still-multi-MB b64_json result whenever the local get_media_dir() write failed for any reason (e.g. a full/read-only instance disk); that oversized result then crashed append_image_history_result()'s Firestore write with the same 1MB "invalid nested entity" error persistence was meant to prevent, and the caller only logged and swallowed that exception, so the entry never made it into 'results' at all. Changes: 1. On persist failure, _persist_image_result_local_copy() now returns a small failure result (success=False, url/b64_json cleared, explanatory error) instead of the oversized original, so the append always succeeds and the record still shows up (as a failure) rather than disappearing. 2. Updated tests/test_image_history_media_cleanup_whitebox.py's decode-failure case and added a write-failure case; added tests/test_frontier_history_persistence.py's TestEndToEndChatGPTImageSurvivesLocalDiskFailure end-to-end regression.`

`[Local media writes redirected to /tmp] Reason: after the previous fix shipped, both ChatGPT and Gemini image-history entries started showing the "local storage error" failure on every real GAE deploy, not just intermittently. Root cause: GAE Standard (including the python312 gen2 runtime) makes the local filesystem read-only everywhere except /tmp; g4f's get_media_dir() defaults to relative paths ('./generated_images'/'./generated_media') resolved against the process CWD, which is writable in local dev (masking the bug) but never writable in production, so every persist attempt failed 100% of the time for both providers. Changes: 1. Right after importing g4f, main.py now redirects g4f.image.copy_images's images_dir/media_dir module globals to tempfile.gettempdir(), since g4f's own internal image download and our _persist_image_result_local_copy()/serve_generated_media() all read the same get_media_dir()/module globals, so one redirect fixes reads and writes for both g4f's own images and Gemini/ChatGPT's persisted copies together. 2. The non-g4f-available fallback get_media_dir() lambda was updated the same way for consistency. See the new TestMediaDirRedirectedUnderSystemTempDir in tests/test_image_history_media_cleanup_whitebox.py.`
