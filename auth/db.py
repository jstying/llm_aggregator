import logging
import os

from werkzeug.security import generate_password_hash

logger = logging.getLogger(__name__)

FIREBASE_AVAILABLE = False
db = None

try:
    import firebase_admin
    from firebase_admin import credentials, firestore

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
    query = db.collection('users').where('username', '==', username).limit(1).stream()
    for doc in query:
        user_data = doc.to_dict()
        user_data['id'] = doc.id
        return user_data
    return None


def get_user_by_email(email):
    query = db.collection('users').where('email', '==', email).limit(1).stream()
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

    # 只做单字段等值查询（Firestore 对每个字段都自带自动索引，无需额外配置）。
    # 原实现在这里链式调用了两次 order_by（is_pinned + created_at），那属于复合查询，
    # Firestore 要求为其手动创建复合索引——该索引不随代码提交，每个 Firebase 项目
    # 都需要单独在控制台创建，新环境下若忘记创建会直接抛 FAILED_PRECONDITION 500。
    # 为了不依赖这一外部手动步骤，排序和分页改为取回该用户的全部历史后在应用层完成；
    # 单个用户的对话历史量级很小，这里的开销可以忽略。
    query = db.collection('history').where('user_id', '==', user_id)
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
# 文生图历史 CRUD：与上面 6 个对话历史函数逐一同构，但写入独立的 'image_history'
# 集合，而不是复用 'history'。这与文本/图片两条 g4f 调用链路（ChatCompletion vs
# images.generate()）、两套 Provider 映射表（PROVIDER_MODELS_MAP vs
# IMAGE_PROVIDER_MODELS_MAP）严格隔离的既有原则一致——8-key 图片 DTO 与 7-key 文本
# DTO（+ 互评 peer_reviews）字段结构不同，混进同一个集合会让每条文档的 schema
# 依赖 "这条到底是聊天还是图片" 这一隐性判别，且历史上聊天记录已经积累在 'history'
# 集合里，不应该被图片文档污染。
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

    # 与 get_chat_history_list 同理：只做单字段等值查询，排序/分页留在 Python 层，
    # 避免依赖需要在 Firebase 控制台手动创建的复合索引。
    query = db.collection('image_history').where('user_id', '==', user_id)
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
