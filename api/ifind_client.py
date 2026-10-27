"""iFinD HTTP 请求, 凭证和短期缓存仅保留在当前客户端内存中."""

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

from dotenv import dotenv_values
import requests


BASE_URL = "https://quantapi.51ifind.com/api/v1/"
ROOT = Path(__file__).resolve().parents[1]


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

    def _post(self, endpoint, headers, payload=None):
        try:
            response = requests.post(BASE_URL + endpoint, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError):
            raise ValueError(f"iFinD {endpoint} 请求失败, 请检查网络和接口服务") from None
        if not isinstance(data, dict) or "errorcode" not in data:
            raise ValueError(f"iFinD {endpoint} 返回格式异常")
        if data["errorcode"] != 0:
            # 原始错误和响应可能携带账号信息, 页面仅显示接口和错误码.
            raise ValueError(f"iFinD {endpoint} 错误码 {data['errorcode']}, 请检查凭证、权限或请求参数")
        return data

    def query(self, endpoint: str, payload: dict) -> dict:
        """成功结果缓存 10 分钟, 缓存命中保留原始获取时间, 失败不缓存."""
        key = (endpoint, json.dumps(payload, sort_keys=True))
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
        data = self._post(endpoint, {
            "Content-Type": "application/json", "access_token": self._access_token,
        }, payload)
        data["_fetched_at"] = datetime.now(timezone.utc).isoformat()
        if len(self._cache) >= 128:
            self._cache.pop(next(iter(self._cache)))
        self._cache[key] = (time.monotonic() + 600, deepcopy(data))
        return data
