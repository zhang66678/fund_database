"""并发请求共享限频和冷却, 不依赖真实网络或 Token."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pandas as pd
import pytest

from api import tushare_client as client


class Clock:
    def __init__(self):
        self.now = 100.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def test_parallel_requests_share_spacing_without_serializing_network(monkeypatch):
    clock = Clock()
    barrier = Barrier(4)
    starts = []

    class FakeClient:
        def query(self, endpoint, fields, **params):
            starts.append(clock.now)
            # 如果网络请求也持有调度锁, 其他线程无法进入此处, 屏障会失败.
            barrier.wait(timeout=5)
            return pd.DataFrame({"ts_code": [params["ts_code"]]})

    monkeypatch.setenv("TUSHARE_TOKEN", "test-only-token")
    monkeypatch.setattr(client.ts, "pro_api", lambda *args, **kwargs: FakeClient())
    monkeypatch.setattr(client, "time", SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep))
    source = client.Source(interval=0.25, attempts=1)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda code: source.query("fund_nav", "ts_code", ts_code=code), range(4)))
    assert len(results) == 4
    assert sorted(starts) == pytest.approx([100, 100.25, 100.5, 100.75])


@pytest.mark.parametrize("message", ["每分钟访问频次超限", "每分钟最多访问该接口500次, 权限的具体详情请访问帮助文档"])
def test_rate_limit_blocks_next_request_even_when_failing_request_has_no_retries(monkeypatch, message):
    clock = Clock()
    starts = []

    class FakeClient:
        def query(self, endpoint, fields, **params):
            starts.append(clock.now)
            if len(starts) == 1:
                raise Exception(message)
            return pd.DataFrame({"ts_code": ["000001.OF"]})

    monkeypatch.setenv("TUSHARE_TOKEN", "test-only-token")
    monkeypatch.setattr(client.ts, "pro_api", lambda *args, **kwargs: FakeClient())
    monkeypatch.setattr(client, "time", SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep))
    source = client.Source(interval=0.15, attempts=1)
    with pytest.raises(client.FetchError, match="rate_limit"):
        source.query("fund_nav", "ts_code")
    source.query("fund_nav", "ts_code")
    assert starts == pytest.approx([100, 130])


def test_source_cannot_exceed_500_per_minute_with_shorter_configured_interval(monkeypatch):
    clock = Clock()
    starts = []

    class FakeClient:
        def query(self, endpoint, fields, **params):
            starts.append(clock.now)
            return pd.DataFrame({"ts_code": ["000001.OF"]})

    monkeypatch.setenv("TUSHARE_TOKEN", "test-only-token")
    monkeypatch.setattr(client.ts, "pro_api", lambda *args, **kwargs: FakeClient())
    monkeypatch.setattr(client, "time", SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep))
    source = client.Source(interval=0.01)
    for _ in range(4):
        source.query("fund_nav", "ts_code")
    assert starts == pytest.approx([100, 100.12, 100.24, 100.36])
