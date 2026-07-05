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

    # 只做单字段等值查询（Firestore 对每个字段都自带自动索引，无需额外配置）。
    # 原实现在这里链式调用了两次 order_by（is_pinned + created_at），那属于复合查询，
    # Firestore 要求为其手动创建复合索引——该索引不随代码提交，每个 Firebase 项目
    # 都需要单独在控制台创建，新环境下若忘记创建会直接抛 FAILED_PRECONDITION 500。
    # 为了不依赖这一外部手动步骤，排序和分页改为取回该用户的全部历史后在应用层完成；
    # 单个用户的对话历史量级很小，这里的开销可以忽略。
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
# 追加前沿模型（Claude）结果到一条已存在的对话历史（2026-07-05 新增）。
#
# 背景：/api/compare 在拿到 g4f 结果后立即调用 save_chat_history() 落库并返回
# history_id；此时前端的 compareForm 提交处理器才刚开始额外发起
# POST /api/claude-chat（见 templates/index.html 的 fetchClaudeResult()）。因此
# Claude 的结果一定是在这条历史记录已经落库之后才计算出来的——之前的实现只是把它
# 追加进浏览器内存里的 data.results 数组用于当次页面渲染，从未回写 Firestore，
# 导致用户在 /history/<id> 里重新打开这条记录时，Claude 那一张结果卡片凭空消失
# （历史 bug，2026-07-05 修复，见 CLAUDE.md 第 9 节）。
#
# 本函数只做"追加"，不做"创建"——不接受也不需要 title/prompt 等字段，调用前该
# history_id 对应的文档必须已经存在（由 save_chat_history() 创建）。归属校验与其余
# 对话历史 CRUD 函数一致：history_id 必须存在且属于 user_id，否则返回 False，调用方
# （main.py 的 claude_chat()）据此只记一条警告日志，不影响本次 Claude 请求本身的
# 响应（持久化失败不应该让用户看不到刚生成的回答）。
#
# 排序：追加后立即按"成功优先、耗时短优先"重新排序整个 results 数组再整体写回——
# 与 compare_providers()/generate_images() 返回给前端的排序契约保持一致，这样用户
# 之后重新打开这条历史记录时，Claude 结果卡片出现的位置和当时在页面上实时看到的
# 位置相同，而不是永远被追加在数组末尾。
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
# 追加前沿模型（Gemini）结果到一条已存在的图片生成历史（2026-07-05 新增）。
#
# 与 append_chat_history_result() 是同一个 bug 的图片版镜像：/api/generate-images
# 已经在 save_image_history() 落库之后才返回 history_id，Gemini 的结果要等前端
# 额外发起的 POST /api/gemini-image 完成才计算出来，因此同样需要一次显式的"追加"
# 才能进入已保存的文档，而不是像修复前那样只存在于浏览器内存里、刷新/重新打开
# /image-history/<id> 后就消失。
#
# 同样只做"追加"，不做"创建"；同样的归属校验、同样"追加后按成功优先/耗时短优先
# 重新排序整个 results 数组再整体写回"的处理，与对话历史版本逐一同构。
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
# Claude 免费额度计数器（2026-07-04 新增）：为每个注册用户在 'users' 集合文档上维护一个
# 整型字段 claude_free_tier_usage，记录"已用掉开发者账户额度调用 Claude 的次数"。字段
# 初始值隐式为 0——不需要在 create_user() 里预先写入，读取时用
# doc.to_dict().get('claude_free_tier_usage', 0) 兜底即可，与 is_pinned 等字段的
# 处理方式一致。仅在用户未自带 Key、且调用成功时才由 main.py 的 claude_chat() 路由
# 调用 increment_claude_free_tier_usage()——本模块的两个函数只负责读/写这一个字段，
# 不关心调用方是否携带了自带 Key（那部分路由逻辑见 main.py）。
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


# "Stop Generating"按钮退款专用（2026-07-05 新增）：先读后写而不是无条件
# Increment(-1)，避免在极端情况下（比如账本被重复命中）把计数减到负数——
# 与 increment 那侧"检查和递增中间没有事务保护"的既有简化一致,这里同样不加事务，
# 只加一个"不为负"的下限。调用方（main.py 的 /api/claude-chat/refund）已经用
# 一次性 request_id 账本保证了不会被重复调用，这里的下限只是防御性兜底。
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
# Gemini（Nano Banana）免费额度计数器（2026-07-04 新增）：与 Claude 的两个计数器函数
# 逐一同构，但读写 users 集合文档上一个独立的整型字段 gemini_free_tier_usage，与
# claude_free_tier_usage 互不共享额度——一个用户可以分别免费试用 1 次 Claude 和 1 次
# Gemini，两边互不影响。字段同样不需要在 create_user() 里预先写入，读取时用
# doc.to_dict().get('gemini_free_tier_usage', 0) 兜底默认 0 即可。同样没有归属校验
# 的概念（user_id 直接来自 session['user_id']），也存在与 Claude 计数器一致的、
# 刻意接受的非原子性（检查与递增是两次独立的 Firestore 读写）。
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


# "Stop Generating"按钮退款专用（2026-07-05 新增），与 decrement_claude_free_tier_usage
# 同构，见其上方注释。
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
