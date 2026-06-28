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
