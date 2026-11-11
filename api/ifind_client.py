"""iFinD HTTP 请求, 凭证和短期缓存仅保留在当前客户端内存中."""

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from threading import Lock, local
import time

from dotenv import dotenv_values
import requests


BASE_URL = "https://quantapi.51ifind.com/api/v1/"
ROOT = Path(__file__).resolve().parents[1]
MIN_REQUEST_INTERVAL = 0.1


class IfindClient:
    def __init__(self):
        self._refresh_token = os.environ.get("IFIND_REFRESH_TOKEN") or dotenv_values(
            ROOT / ".env", interpolate=False,
        ).get("IFIND_REFRESH_TOKEN")
        if not self._refresh_token or not self._refresh_token.strip():
            raise ValueError("请在项目根目录 .env 中配置 IFIND_REFRESH_TOKEN")
        self._refresh_token = self._refresh_token.strip()
        self._access_token = None
        self._token_expires = 0
        self._cache = {}
        self.interval = 0.15
        self._state_lock = Lock()
        self._request_lock = Lock()
        self._last_request = 0.0
        self._local = local()
        self._sessions = []
        self._sessions_lock = Lock()

    def _session(self):
        # Session 按线程独立, 避免共享可变状态, 同一线程复用 TCP/TLS 连接.
        if not hasattr(self._local, "session"):
            session = requests.Session()
            with self._sessions_lock:
                self._sessions.append(session)
            self._local.session = session
        return self._local.session

    def close(self):
        """所有取数线程结束后释放连接, 客户端仍可再次查询."""
        with self._sessions_lock:
            for session in self._sessions:
                session.close()
            self._sessions.clear()
        self._local = local()

    def _wait_for_request(self):
        # 各线程共用调度, 网络请求在锁外重叠; 单客户端最多每秒 10 次.
        with self._request_lock:
            interval = max(MIN_REQUEST_INTERVAL, self.interval)
            time.sleep(max(0, self._last_request + interval - time.monotonic()))
            self._last_request = time.monotonic()

    def _post(self, endpoint, headers, payload=None):
        self._wait_for_request()
        try:
            response = self._session().post(BASE_URL + endpoint, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError):
            raise ValueError(f"iFinD {endpoint} 请求失败, 请检查网络和接口服务") from None
        if not isinstance(data, dict) or "errorcode" not in data:
            raise ValueError(f"iFinD {endpoint} 返回格式异常")
        if endpoint == "data_pool" and data["errorcode"] == -4001 and data.get("errmsg") == "no data.":
            # 专题报表没有匹配记录时返回此码, 与权限、参数错误分别处理.
            return {"errorcode": 0, "tables": []}
        if data["errorcode"] != 0:
            # 原始错误和响应可能携带账号信息, 页面仅显示接口和错误码.
            raise ValueError(f"iFinD {endpoint} 错误码 {data['errorcode']}, 请检查凭证、权限或请求参数")
        return data

    def query(self, endpoint: str, payload: dict) -> dict:
        """成功结果缓存 10 分钟, 缓存命中保留原始获取时间, 失败不缓存."""
        key = (endpoint, json.dumps(payload, sort_keys=True))
        # 只串行鉴权和内存状态, 避免多个工作线程同时换取 access_token.
        with self._state_lock:
            cached = self._cache.get(key)
            if cached and time.monotonic() < cached[0]:
                return deepcopy(cached[1])
            if time.monotonic() >= self._token_expires:
                auth = self._post("get_access_token", {"refresh_token": self._refresh_token})
                self._access_token = auth.get("data", {}).get("access_token")
                if not self._access_token:
                    raise ValueError("iFinD 鉴权未返回 access_token")
                # access_token 有效期为 7 天, 提前一天重新获取, 不轮换 refresh_token.
                self._token_expires = time.monotonic() + 6 * 24 * 60 * 60
            access_token = self._access_token
        data = self._post(endpoint, {
            "Content-Type": "application/json", "access_token": access_token,
        }, payload)
        data["_fetched_at"] = datetime.now(timezone.utc).isoformat()
        with self._state_lock:
            if len(self._cache) >= 128:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = (time.monotonic() + 600, deepcopy(data))
        return data
