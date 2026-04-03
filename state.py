"""
state.py — Firebase Firestore persistent state layer.
Optional: nếu FIREBASE_CREDENTIALS_JSON không được set, bot fallback về file local.
"""
import os
import json
import time
from datetime import datetime

from config import (
    FIREBASE_CREDENTIALS_JSON, FIRESTORE_COLLECTION,
    LEARNING_ENABLED, LEARNING_FILE,
)
from utils import now_vn

# ==================== Firebase Init ====================

try:
    import firebase_admin
    from firebase_admin import credentials as fb_credentials, firestore as fb_firestore
    _FIREBASE_AVAILABLE = True
except ImportError:
    _FIREBASE_AVAILABLE = False

_firestore_client = None
_firestore_init_done = False


def _get_firestore_client():
    """Khởi tạo Firestore client 1 lần duy nhất (lazy init)."""
    global _firestore_client, _firestore_init_done
    if _firestore_init_done:
        return _firestore_client
    _firestore_init_done = True
    if not _FIREBASE_AVAILABLE or not FIREBASE_CREDENTIALS_JSON:
        return None
    try:
        if not firebase_admin._apps:  # type: ignore[attr-defined]
            cred_dict = json.loads(FIREBASE_CREDENTIALS_JSON)
            cred = fb_credentials.Certificate(cred_dict)  # type: ignore[attr-defined]
            firebase_admin.initialize_app(cred)  # type: ignore[attr-defined]
        _firestore_client = fb_firestore.client()  # type: ignore[attr-defined]
        print("[FIREBASE] Firestore client khởi tạo thành công.")
    except Exception as e:
        print(f"[FIREBASE] Khởi tạo thất bại, dùng file local: {e}")
        _firestore_client = None
    return _firestore_client


def _firestore_set(doc_id, data):
    """Ghi dict vào Firestore document. Trả về True nếu thành công."""
    client = _get_firestore_client()
    if client is None:
        return False
    try:
        client.collection(FIRESTORE_COLLECTION).document(doc_id).set(data)
        return True
    except Exception as e:
        print(f"[FIREBASE] _firestore_set({doc_id}) lỗi: {e}")
        return False


def _firestore_get(doc_id):
    """Lấy dict từ Firestore document. Trả về None nếu không tìm thấy."""
    client = _get_firestore_client()
    if client is None:
        return None
    try:
        doc = client.collection(FIRESTORE_COLLECTION).document(doc_id).get()
        return doc.to_dict() if doc.exists else None
    except Exception as e:
        print(f"[FIREBASE] _firestore_get({doc_id}) lỗi: {e}")
        return None


# ==================== Learning State ====================

def load_learning_state():
    if not LEARNING_ENABLED:
        return {}
    # 1) Thử Firebase trước
    fb_data = _firestore_get("learning_state")
    if fb_data and isinstance(fb_data.get("state"), dict):
        print(f"[FIREBASE] Đã tải learning_state từ Firestore ({len(fb_data['state'])} keys).")
        return fb_data["state"]
    # 2) Fallback: file local
    if not os.path.exists(LEARNING_FILE):
        return {}
    try:
        with open(LEARNING_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"[WARN] load_learning_state failed: {e}")
        return {}


def save_learning_state(state):
    if not LEARNING_ENABLED:
        return
    # 1) Lưu Firebase
    if _firestore_set("learning_state", {"state": state, "updated_at": time.time()}):
        print(f"[FIREBASE] learning_state đã lưu lên Firestore ({len(state)} keys).")
    # 2) Luôn lưu file local làm backup
    try:
        with open(LEARNING_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[WARN] save_learning_state (file) failed: {e}")


def mark_learning_dirty(meta):
    if LEARNING_ENABLED:
        meta["dirty"] = True


def maybe_flush_learning_state(state, meta, force=False):
    if not LEARNING_ENABLED:
        return
    now_ts = time.time()
    if not meta.get("dirty"):
        return
    last_save_ts = float(meta.get("last_save_ts", 0.0))
    from config import LEARNING_SAVE_INTERVAL
    if force or (now_ts - last_save_ts >= LEARNING_SAVE_INTERVAL):
        save_learning_state(state)
        meta["dirty"] = False
        meta["last_save_ts"] = now_ts


# ==================== Active Positions ====================

def save_active_positions(positions_by_symbol):
    """
    Lưu active_positions_by_symbol lên Firestore/file local để bot khôi phục khi restart.
    """
    try:
        serializable = {}
        for sym, positions in positions_by_symbol.items():
            serializable[sym] = []
            for pos in positions:
                p = dict(pos)
                if hasattr(p.get("opened_at"), "isoformat"):
                    p["opened_at"] = p["opened_at"].isoformat()
                serializable[sym].append(p)
        payload = {"positions": serializable, "updated_at": time.time()}
        if not _firestore_set("active_positions", payload):
            # Fallback: file local
            with open("active_positions.json", "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[WARN] save_active_positions failed: {e}")


def load_active_positions(symbols):
    """
    Tải lại active_positions_by_symbol khi bot restart.
    """
    default = {s: [] for s in symbols}
    try:
        data = _firestore_get("active_positions")
        if data is None:
            if os.path.exists("active_positions.json"):
                with open("active_positions.json", "r", encoding="utf-8") as f:
                    data = json.load(f)
        if not data or not isinstance(data.get("positions"), dict):
            return default
        result = {s: [] for s in symbols}
        for sym in symbols:
            saved = data["positions"].get(sym, [])
            for p in saved:
                if isinstance(p.get("opened_at"), str):
                    try:
                        p["opened_at"] = datetime.fromisoformat(p["opened_at"])
                    except Exception:
                        p["opened_at"] = now_vn()
                result[sym].append(p)
        total = sum(len(v) for v in result.values())
        if total > 0:
            print(f"[FIREBASE] Đã tải lại {total} active positions từ persistent state.")
        return result
    except Exception as e:
        print(f"[WARN] load_active_positions failed: {e}")
        return default
