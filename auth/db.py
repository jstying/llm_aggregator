import logging
import os

from werkzeug.security import generate_password_hash

logger = logging.getLogger(__name__)

FIREBASE_AVAILABLE = False
db = None

try:
    import firebase_admin
    from firebase_admin import credentials, firestore
    from google.cloud.firestore_v1.base_query import FieldFilter

    if not firebase_admin._apps:
        # Prefer key file locally; ApplicationDefault() resolves lazily and fails at firestore.client()
        if os.path.exists('firebase-key.json'):
            cred = credentials.Certificate('firebase-key.json')
        else:
            cred = credentials.ApplicationDefault()
        firebase_admin.initialize_app(cred)

    db = firestore.client()
    FIREBASE_AVAILABLE = True
    logger.info("Firebase initialized successfully")
except ImportError as e:
    logger.warning(f"firebase_admin not available: {e}")
except Exception as e:
    logger.warning(f"Firebase initialization failed: {e}", exc_info=True)


def get_user_by_username(username):
    query = db.collection('users').where(filter=FieldFilter('username', '==', username)).limit(1).stream()
    for doc in query:
        user_data = doc.to_dict()
        user_data['id'] = doc.id
        return user_data
    return None


def get_user_by_email(email):
    query = db.collection('users').where(filter=FieldFilter('email', '==', email)).limit(1).stream()
    for doc in query:
        user_data = doc.to_dict()
        user_data['id'] = doc.id
        return user_data
    return None


def create_user(username, email, password):
    user_data = {
        'username': username,
        'email': email,
        'password_hash': generate_password_hash(password),
        'created_at': firestore.SERVER_TIMESTAMP,
    }
    _, doc_ref = db.collection('users').add(user_data)
    user_data['id'] = doc_ref.id
    return user_data


def get_user_by_id(user_id):
    doc = db.collection('users').document(user_id).get()
    if doc.exists:
        user_data = doc.to_dict()
        user_data['id'] = doc.id
        return user_data
    return None


def save_chat_history(user_id, prompt, results):
    if not FIREBASE_AVAILABLE:
        logger.warning("save_chat_history called but Firebase unavailable")
        return None

    history_data = {
        'user_id': user_id,
        'title': prompt[:15] + ('...' if len(prompt) > 15 else ''),
        'prompt': prompt,
        'results': results,
        'created_at': firestore.SERVER_TIMESTAMP,
        'is_pinned': False,
    }
    _, doc_ref = db.collection('history').add(history_data)
    history_data['id'] = doc_ref.id
    logger.info(f"Saved chat history {doc_ref.id} for user {user_id}")
    return history_data


def get_chat_history_list(user_id, limit=20, offset=0):
    if not FIREBASE_AVAILABLE:
        logger.warning("get_chat_history_list called but Firebase unavailable")
        return []

    # Only a single-field equality query (Firestore auto-indexes every field, so no
    # extra config is needed). The original implementation chained two order_by calls
    # here (is_pinned + created_at), which counts as a composite query — Firestore
    # requires a manually created composite index for that, and that index does not
    # travel with the code commit; every Firebase project needs to create it separately
    # in the console, and forgetting to do so in a new environment throws a
    # FAILED_PRECONDITION 500 outright. To avoid depending on this external manual step,
    # sorting and pagination were moved to fetch this user's entire history and finish
    # the work in the application layer instead; a single user's chat history volume is
    # small, so the overhead here is negligible.
    query = db.collection('history').where(filter=FieldFilter('user_id', '==', user_id))
    history_list = []
    for doc in query.stream():
        item = doc.to_dict()
        item['id'] = doc.id
        history_list.append(item)

    def sort_key(item):
        # Pinned items sort as a block ahead of unpinned ones (group 0 vs 1). Within the
        # pinned block, order is by pinned_at ascending — i.e. by *when each item was
        # pinned*, oldest pin action first — so the first thing a user pins stays on top
        # and each subsequent pin lands below it, instead of newer pins displacing older
        # ones (that used to happen when this was sorted by created_at like the unpinned
        # block below). Within the unpinned block, order is by created_at descending
        # (most recent conversation first), unchanged from before pinning existed.
        if item.get('is_pinned', False):
            pinned_at = item.get('pinned_at')
            timestamp = pinned_at.timestamp() if pinned_at else 0
            return (0, timestamp)
        created_at = item.get('created_at')
        timestamp = created_at.timestamp() if created_at else 0
        return (1, -timestamp)

    history_list.sort(key=sort_key)
    return history_list[offset:offset + limit]


def get_chat_history_by_id(user_id, history_id):
    if not FIREBASE_AVAILABLE:
        logger.warning("get_chat_history_by_id called but Firebase unavailable")
        return None

    doc_ref = db.collection('history').document(history_id)
    doc = doc_ref.get()
    if not doc.exists or doc.to_dict().get('user_id') != user_id:
        logger.warning(f"get_chat_history_by_id denied: {history_id} not owned by {user_id}")
        return None

    item = doc.to_dict()
    item['id'] = doc.id
    return item


def delete_chat_history(user_id, history_id):
    if not FIREBASE_AVAILABLE:
        logger.warning("delete_chat_history called but Firebase unavailable")
        return False

    doc_ref = db.collection('history').document(history_id)
    doc = doc_ref.get()
    if not doc.exists or doc.to_dict().get('user_id') != user_id:
        logger.warning(f"delete_chat_history denied: {history_id} not owned by {user_id}")
        return False

    doc_ref.delete()
    logger.info(f"Deleted chat history {history_id} for user {user_id}")
    return True


def update_chat_history_title(user_id, history_id, new_title):
    if not FIREBASE_AVAILABLE:
        logger.warning("update_chat_history_title called but Firebase unavailable")
        return False

    doc_ref = db.collection('history').document(history_id)
    doc = doc_ref.get()
    if not doc.exists or doc.to_dict().get('user_id') != user_id:
        logger.warning(f"update_chat_history_title denied: {history_id} not owned by {user_id}")
        return False

    doc_ref.update({'title': new_title})
    logger.info(f"Updated title for chat history {history_id}")
    return True


def toggle_pin_chat_history(user_id, history_id):
    if not FIREBASE_AVAILABLE:
        logger.warning("toggle_pin_chat_history called but Firebase unavailable")
        return None

    doc_ref = db.collection('history').document(history_id)
    doc = doc_ref.get()
    if not doc.exists or doc.to_dict().get('user_id') != user_id:
        logger.warning(f"toggle_pin_chat_history denied: {history_id} not owned by {user_id}")
        return None

    new_pinned = not doc.to_dict().get('is_pinned', False)
    # pinned_at records *when this pin action happened*, used by get_chat_history_list to
    # order pinned items oldest-pin-first (see sort_key there). Cleared on unpin so a later
    # re-pin gets a fresh timestamp rather than resurrecting its old position.
    update_data = {
        'is_pinned': new_pinned,
        'pinned_at': firestore.SERVER_TIMESTAMP if new_pinned else firestore.DELETE_FIELD,
    }
    doc_ref.update(update_data)
    logger.info(f"Toggled pin for chat history {history_id} to {new_pinned}")
    return new_pinned


# ==================================================
# Append a frontier model (Claude) result to an existing chat history record
# (added 2026-07-05).
#
# Background: /api/compare calls save_chat_history() to persist the record and
# return history_id as soon as it has the g4f results; only at that point does
# the frontend's compareForm submit handler start firing an extra
# POST /api/claude-chat (see fetchClaudeResult() in templates/index.html). So
# the Claude result is always computed after this history record has already
# been persisted — the old implementation just appended it into the
# in-browser data.results array for that single page render, never writing it
# back to Firestore, so when the user reopened this record at /history/<id>
# the Claude result card had vanished into thin air (a historical bug, fixed
# 2026-07-05, see CLAUDE.md section 9).
#
# This function only does "append," never "create" — it does not accept or
# need fields like title/prompt, and the document for this history_id must
# already exist before calling it (created by save_chat_history()). Ownership
# checking is the same as the rest of the chat history CRUD functions:
# history_id must exist and belong to user_id, otherwise it returns False, and
# the caller (main.py's claude_chat()) only logs a warning in that case — it
# does not affect the response for this Claude request itself (a persistence
# failure should never keep the user from seeing the answer that was just
# generated).
#
# Sorting: right after appending, the whole results array is re-sorted using
# "success first, then shorter response time first" and written back in
# full — matching the same sort contract that compare_providers()/
# generate_images() return to the frontend, so when the user later reopens
# this history record, the Claude result card appears in the same position it
# was shown in live on the page, instead of always being stuck at the end of
# the array.
# ==================================================
def append_chat_history_result(user_id, history_id, result):
    if not FIREBASE_AVAILABLE:
        logger.warning("append_chat_history_result called but Firebase unavailable")
        return False

    doc_ref = db.collection('history').document(history_id)
    doc = doc_ref.get()
    if not doc.exists or doc.to_dict().get('user_id') != user_id:
        logger.warning(f"append_chat_history_result denied: {history_id} not owned by {user_id}")
        return False

    existing_results = doc.to_dict().get('results', [])
    updated_results = existing_results + [result]
    updated_results.sort(key=lambda r: (not r.get('success', False), r.get('response_time', 0)))
    doc_ref.update({'results': updated_results})
    logger.info(f"Appended frontier model result to chat history {history_id} for user {user_id}")
    return True


# ==================================================
# Write the unified cross g4f/frontier-model peer review results (produced by
# main.py's run_cross_peer_review(), triggered by the new POST /api/peer-review
# route) back into an existing chat history record (added 2026-07-07).
# Like append_chat_history_result(), this can "only update an existing record,
# never create a new one," but the semantics differ: instead of appending a new
# result entry, it matches by provider name and replaces the existing entry's
# peer_reviews field in place with the final version passed in. Why this step
# is needed: when compare_providers() saves the history record, peer review has
# not run yet (peer review is now deferred until all frontier model calls have
# returned, then triggered together in one pass — see the comment above
# main.py's run_cross_peer_review()); without writing it back, the history
# detail page would permanently lose the peer review content.
# peer_reviews_by_provider has the shape {provider_name: [review_item, ...]};
# it only overwrites the providers that appear in it, leaving peer_reviews on
# every other entry unchanged.
# ==================================================
def update_chat_history_peer_reviews(user_id, history_id, peer_reviews_by_provider):
    if not FIREBASE_AVAILABLE:
        logger.warning("update_chat_history_peer_reviews called but Firebase unavailable")
        return False

    doc_ref = db.collection('history').document(history_id)
    doc = doc_ref.get()
    if not doc.exists or doc.to_dict().get('user_id') != user_id:
        logger.warning(f"update_chat_history_peer_reviews denied: {history_id} not owned by {user_id}")
        return False

    existing_results = doc.to_dict().get('results', [])
    for r in existing_results:
        provider = r.get('provider')
        if provider in peer_reviews_by_provider:
            r['peer_reviews'] = peer_reviews_by_provider[provider]

    doc_ref.update({'results': existing_results})
    logger.info(f"Updated peer reviews on chat history {history_id} for user {user_id}")
    return True


# ==================================================
# Text-to-image history CRUD: mirrors the 6 chat history functions above one
# for one, but writes into an independent 'image_history' collection instead
# of reusing 'history'. This is consistent with the existing principle of
# strictly isolating the text/image g4f call chains (ChatCompletion vs
# images.generate()) and their two provider mapping tables
# (PROVIDER_MODELS_MAP vs IMAGE_PROVIDER_MODELS_MAP) — the 8-key image DTO and
# the 7-key text DTO (+ peer_reviews) have different field shapes, and mixing
# them into the same collection would make every document's schema depend on
# the implicit discriminator of "is this actually a chat or an image entry";
# also, chat history records have already been accumulating in the 'history'
# collection historically and should not be polluted by image documents.
# ==================================================
def save_image_history(user_id, prompt, results):
    if not FIREBASE_AVAILABLE:
        logger.warning("save_image_history called but Firebase unavailable")
        return None

    history_data = {
        'user_id': user_id,
        'title': prompt[:15] + ('...' if len(prompt) > 15 else ''),
        'prompt': prompt,
        'results': results,
        'created_at': firestore.SERVER_TIMESTAMP,
        'is_pinned': False,
    }
    _, doc_ref = db.collection('image_history').add(history_data)
    history_data['id'] = doc_ref.id
    logger.info(f"Saved image history {doc_ref.id} for user {user_id}")
    return history_data


def get_image_history_list(user_id, limit=20, offset=0):
    if not FIREBASE_AVAILABLE:
        logger.warning("get_image_history_list called but Firebase unavailable")
        return []

    # Same as get_chat_history_list: only a single-field equality query, with
    # sorting/pagination left to the Python layer, to avoid depending on a
    # composite index that would need to be created by hand in the Firebase
    # console.
    query = db.collection('image_history').where(filter=FieldFilter('user_id', '==', user_id))
    history_list = []
    for doc in query.stream():
        item = doc.to_dict()
        item['id'] = doc.id
        history_list.append(item)

    def sort_key(item):
        if item.get('is_pinned', False):
            pinned_at = item.get('pinned_at')
            timestamp = pinned_at.timestamp() if pinned_at else 0
            return (0, timestamp)
        created_at = item.get('created_at')
        timestamp = created_at.timestamp() if created_at else 0
        return (1, -timestamp)

    history_list.sort(key=sort_key)
    return history_list[offset:offset + limit]


def get_image_history_by_id(user_id, history_id):
    if not FIREBASE_AVAILABLE:
        logger.warning("get_image_history_by_id called but Firebase unavailable")
        return None

    doc_ref = db.collection('image_history').document(history_id)
    doc = doc_ref.get()
    if not doc.exists or doc.to_dict().get('user_id') != user_id:
        logger.warning(f"get_image_history_by_id denied: {history_id} not owned by {user_id}")
        return None

    item = doc.to_dict()
    item['id'] = doc.id
    return item


def delete_image_history(user_id, history_id):
    if not FIREBASE_AVAILABLE:
        logger.warning("delete_image_history called but Firebase unavailable")
        return False

    doc_ref = db.collection('image_history').document(history_id)
    doc = doc_ref.get()
    if not doc.exists or doc.to_dict().get('user_id') != user_id:
        logger.warning(f"delete_image_history denied: {history_id} not owned by {user_id}")
        return False

    doc_ref.delete()
    logger.info(f"Deleted image history {history_id} for user {user_id}")
    return True


def update_image_history_title(user_id, history_id, new_title):
    if not FIREBASE_AVAILABLE:
        logger.warning("update_image_history_title called but Firebase unavailable")
        return False

    doc_ref = db.collection('image_history').document(history_id)
    doc = doc_ref.get()
    if not doc.exists or doc.to_dict().get('user_id') != user_id:
        logger.warning(f"update_image_history_title denied: {history_id} not owned by {user_id}")
        return False

    doc_ref.update({'title': new_title})
    logger.info(f"Updated title for image history {history_id}")
    return True


def toggle_pin_image_history(user_id, history_id):
    if not FIREBASE_AVAILABLE:
        logger.warning("toggle_pin_image_history called but Firebase unavailable")
        return None

    doc_ref = db.collection('image_history').document(history_id)
    doc = doc_ref.get()
    if not doc.exists or doc.to_dict().get('user_id') != user_id:
        logger.warning(f"toggle_pin_image_history denied: {history_id} not owned by {user_id}")
        return None

    new_pinned = not doc.to_dict().get('is_pinned', False)
    update_data = {
        'is_pinned': new_pinned,
        'pinned_at': firestore.SERVER_TIMESTAMP if new_pinned else firestore.DELETE_FIELD,
    }
    doc_ref.update(update_data)
    logger.info(f"Toggled pin for image history {history_id} to {new_pinned}")
    return new_pinned


# ==================================================
# Append a frontier model (Gemini) result to an existing image generation
# history record (added 2026-07-05).
#
# This is the image-side mirror of the same bug as
# append_chat_history_result(): /api/generate-images already returns
# history_id after save_image_history() has persisted the record, and the
# Gemini result is only computed once the frontend's extra POST
# /api/gemini-image call finishes, so an explicit "append" is likewise needed
# to get it into the already-saved document — instead of, as before the fix,
# only ever existing in browser memory and vanishing on a refresh or on
# reopening /image-history/<id>.
#
# Likewise only does "append," never "create"; the same ownership check, and
# the same "re-sort the whole results array by success first / shorter
# response time first, then write it back in full" handling, mirroring the
# chat history version one for one.
# ==================================================
def append_image_history_result(user_id, history_id, result):
    if not FIREBASE_AVAILABLE:
        logger.warning("append_image_history_result called but Firebase unavailable")
        return False

    doc_ref = db.collection('image_history').document(history_id)
    doc = doc_ref.get()
    if not doc.exists or doc.to_dict().get('user_id') != user_id:
        logger.warning(f"append_image_history_result denied: {history_id} not owned by {user_id}")
        return False

    existing_results = doc.to_dict().get('results', [])
    updated_results = existing_results + [result]
    updated_results.sort(key=lambda r: (not r.get('success', False), r.get('response_time', 0)))
    doc_ref.update({'results': updated_results})
    logger.info(f"Appended frontier model result to image history {history_id} for user {user_id}")
    return True


# ==================================================
# Claude free-tier usage counter (added 2026-07-04): maintains an integer
# field, claude_free_tier_usage, on each registered user's document in the
# 'users' collection, recording "how many times the developer account's quota
# has been used to call Claude." The field's initial value is implicitly 0 —
# there is no need to pre-write it in create_user(); reading with
# doc.to_dict().get('claude_free_tier_usage', 0) as a fallback is enough,
# consistent with how fields like is_pinned are handled. main.py's
# claude_chat() route only calls increment_claude_free_tier_usage() when the
# user did not bring their own key and the call succeeded — the two functions
# in this module only handle reading/writing this one field and do not care
# whether the caller brought their own key (that part of the routing logic is
# in main.py).
# ==================================================
def get_claude_free_tier_usage(user_id):
    if not FIREBASE_AVAILABLE:
        logger.warning("get_claude_free_tier_usage called but Firebase unavailable")
        return 0

    doc = db.collection('users').document(user_id).get()
    if not doc.exists:
        return 0
    return doc.to_dict().get('claude_free_tier_usage', 0)


def increment_claude_free_tier_usage(user_id):
    if not FIREBASE_AVAILABLE:
        logger.warning("increment_claude_free_tier_usage called but Firebase unavailable")
        return None

    doc_ref = db.collection('users').document(user_id)
    doc_ref.update({'claude_free_tier_usage': firestore.Increment(1)})
    logger.info(f"Incremented claude_free_tier_usage for user {user_id}")
    return True


# For the "Stop Generating" button's refund path only (added 2026-07-05):
# reads before writing instead of unconditionally doing Increment(-1), to
# avoid driving the count negative in an extreme case (e.g. the ledger being
# hit twice). This is consistent with the existing simplification on the
# increment side, "no transaction between the check and the increment" —
# there's likewise no transaction added here, just a "never negative" floor.
# The caller (main.py's /api/claude-chat/refund) already guarantees it can't
# be called twice via a one-time request_id ledger, so the floor here is only
# a defensive backstop.
def decrement_claude_free_tier_usage(user_id):
    if not FIREBASE_AVAILABLE:
        logger.warning("decrement_claude_free_tier_usage called but Firebase unavailable")
        return None

    doc_ref = db.collection('users').document(user_id)
    doc = doc_ref.get()
    current = doc.to_dict().get('claude_free_tier_usage', 0) if doc.exists else 0
    if current <= 0:
        return False
    doc_ref.update({'claude_free_tier_usage': firestore.Increment(-1)})
    logger.info(f"Decremented claude_free_tier_usage for user {user_id}")
    return True


# ==================================================
# Gemini (Nano Banana) free-tier usage counter (added 2026-07-04): mirrors
# Claude's two counter functions one for one, but reads/writes an independent
# integer field, gemini_free_tier_usage, on the users collection document,
# sharing no quota at all with claude_free_tier_usage — a user can separately
# get 1 free trial call each for Claude and for Gemini, with no effect on each
# other. This field likewise needs no pre-write in create_user(); reading with
# doc.to_dict().get('gemini_free_tier_usage', 0) as a default fallback is
# enough. There is likewise no concept of ownership checking (user_id comes
# directly from session['user_id']), and the same deliberately accepted
# non-atomicity as the Claude counter exists here too (checking and
# incrementing are two separate Firestore reads/writes).
# ==================================================
def get_gemini_free_tier_usage(user_id):
    if not FIREBASE_AVAILABLE:
        logger.warning("get_gemini_free_tier_usage called but Firebase unavailable")
        return 0

    doc = db.collection('users').document(user_id).get()
    if not doc.exists:
        return 0
    return doc.to_dict().get('gemini_free_tier_usage', 0)


def increment_gemini_free_tier_usage(user_id):
    if not FIREBASE_AVAILABLE:
        logger.warning("increment_gemini_free_tier_usage called but Firebase unavailable")
        return None

    doc_ref = db.collection('users').document(user_id)
    doc_ref.update({'gemini_free_tier_usage': firestore.Increment(1)})
    logger.info(f"Incremented gemini_free_tier_usage for user {user_id}")
    return True


# For the "Stop Generating" button's refund path only (added 2026-07-05),
# mirrors decrement_claude_free_tier_usage(), see the comment above it.
def decrement_gemini_free_tier_usage(user_id):
    if not FIREBASE_AVAILABLE:
        logger.warning("decrement_gemini_free_tier_usage called but Firebase unavailable")
        return None

    doc_ref = db.collection('users').document(user_id)
    doc = doc_ref.get()
    current = doc.to_dict().get('gemini_free_tier_usage', 0) if doc.exists else 0
    if current <= 0:
        return False
    doc_ref.update({'gemini_free_tier_usage': firestore.Increment(-1)})
    logger.info(f"Decremented gemini_free_tier_usage for user {user_id}")
    return True


# ==================================================
# Generic free-tier usage counter (added 2026-07-06): Claude/Gemini each have
# their own set of dedicated, specifically named functions (above), which are
# a historical leftover; newly added frontier providers (ChatGPT text/image,
# Gemini text) instead use this generic, field_name-parameterized version, to
# avoid writing three near-identical functions for every new counter. The
# semantics, the non-atomicity simplification, and the "refund floor never
# goes negative" rule are all fully identical to the Claude/Gemini dedicated
# versions above; only the field name to read/write is swapped from being
# hardcoded to being a parameter.
# ==================================================
def get_free_tier_usage(user_id, field_name):
    if not FIREBASE_AVAILABLE:
        logger.warning(f"get_free_tier_usage({field_name}) called but Firebase unavailable")
        return 0

    doc = db.collection('users').document(user_id).get()
    if not doc.exists:
        return 0
    return doc.to_dict().get(field_name, 0)


def increment_free_tier_usage(user_id, field_name):
    if not FIREBASE_AVAILABLE:
        logger.warning(f"increment_free_tier_usage({field_name}) called but Firebase unavailable")
        return None

    doc_ref = db.collection('users').document(user_id)
    doc_ref.update({field_name: firestore.Increment(1)})
    logger.info(f"Incremented {field_name} for user {user_id}")
    return True


def decrement_free_tier_usage(user_id, field_name):
    if not FIREBASE_AVAILABLE:
        logger.warning(f"decrement_free_tier_usage({field_name}) called but Firebase unavailable")
        return None

    doc_ref = db.collection('users').document(user_id)
    doc = doc_ref.get()
    current = doc.to_dict().get(field_name, 0) if doc.exists else 0
    if current <= 0:
        return False
    doc_ref.update({field_name: firestore.Increment(-1)})
    logger.info(f"Decremented {field_name} for user {user_id}")
    return True
