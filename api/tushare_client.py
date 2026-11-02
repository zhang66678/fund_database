"""通用 Tushare 请求封装, 不负责数据保存或业务计算."""

import logging
import os
import time
from pathlib import Path
from threading import Lock

import pandas as pd
import tushare as ts
from dotenv import dotenv_values
from requests.exceptions import RequestException

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
MIN_REQUEST_INTERVAL = 60 / 500


class FetchError(RuntimeError):
    """数据获取的可预期错误; 错误信息应不含凭证, 可写入日志."""


class Source:
    """加载凭证, 并统一处理请求间隔, 有限重试和错误分类."""

    def __init__(self, interval: float = 0.15, attempts: int = 3):
        """默认间隔 0.15 秒, 所有工作线程合计理论上限约 400 次/分钟.

        interval 为请求最小间隔(秒), attempts 为包含首次请求的总尝试次数.
        按本项目账号的 500 次/分钟上限, 实际间隔至少 0.12 秒.
        """
        # 优先使用环境变量, 否则读取项目根目录的 .env, 不依赖终端当前目录.
        # 关闭变量插值, 按配置原值读取 Token; 不向环境或其他文件回写凭证.
        token = os.environ.get('TUSHARE_TOKEN') or dotenv_values(
            ROOT / '.env', interpolate=False
        ).get('TUSHARE_TOKEN')
        if not token or not token.strip():
            raise FetchError('TUSHARE_TOKEN is missing')
        self._token = token.strip()
        # 直接传入 Token 初始化, 避免 ts.set_token 将凭证另存到本地文件.
        self._client = ts.pro_api(self._token, timeout=30)
        self.interval = interval
        self.attempts = attempts
        self._last_request = 0.0
        self._request_lock = Lock()
        self._blocked_until = 0.0

    def _wait_for_request(self):
        # 所有工作线程共享请求节奏; 锁仅覆盖调度, 网络等待可以重叠.
        with self._request_lock:
            now = time.monotonic()
            interval = max(MIN_REQUEST_INTERVAL, self.interval)
            time.sleep(max(0, self._last_request + interval - now, self._blocked_until - now))
            self._last_request = time.monotonic()

    def query(self, endpoint: str, fields: str, **params) -> pd.DataFrame:
        """请求指定接口并返回 DataFrame; 不记录原始异常, 请求体或凭证.

        endpoint 是接口名, fields 是逗号分隔的字段名, params 为接口筛选参数.
        网络等临时错误有限重试; 权限或凭证问题直接失败, 避免无效请求.
        """
        for attempt in range(1, self.attempts + 1):
            # 单调时钟不受系统时间校准影响; 只等待距离最小请求间隔尚差的时间.
            self._wait_for_request()
            try:
                frame = self._client.query(endpoint, fields=fields, **params)
                # 空结果也应包含请求列; 缺列不能直接当成"没有基金数据".
                if set(fields.split(',')) - set(frame.columns):
                    raise FetchError('Response schema missing requested columns')
                LOGGER.info('%s code=%s rows=%s', endpoint, params.get('ts_code', '-'), len(frame))
                return frame
            except Exception as exc:
                # SDK 对接口错误直接抛出普通 Exception, 因此在请求边界统一捕获.
                # 原始消息仅在内存中脱敏和分类, 不写日志, 也不输出异常堆栈.
                message = str(exc).replace(self._token, '[REDACTED]')
                category = type(exc).__name__
                # 限频提示也可能附带"权限详情"链接, 先识别限频再判断权限错误.
                if not isinstance(exc, RequestException) and any(word in message for word in ('每分钟', '频次', '频率')):
                    category = 'rate_limit'
                    # 任一线程遇到限流, 所有线程一起冷却, 避免其他线程继续冲击接口.
                    with self._request_lock:
                        self._blocked_until = max(self._blocked_until, time.monotonic() + 30)
                elif not isinstance(exc, RequestException) and any(word in message for word in ('权限', '积分', 'token', 'TOKEN')):
                    raise FetchError(f'{endpoint}: permission or credential error') from None
                LOGGER.warning('%s attempt=%s failure=%s', endpoint, attempt, category)
                if attempt == self.attempts:
                    raise FetchError(f'{endpoint}: {category} after {attempt} attempts') from None
                # 限频时等待更久; 其他临时错误按 2, 4..秒逐步延长重试等待.
                if category != 'rate_limit':
                    time.sleep(2 ** attempt)
        raise FetchError('No request attempts configured')
