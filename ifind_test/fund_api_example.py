"""简单演示 iFinD SDK 和 HTTP 获取公募基金资料, 净值与公告."""

import os
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv
from iFinDPy import (
    THS_BD, THS_DS, THS_HQ, THS_ReportQuery, THS_iFinDLogin, THS_iFinDLogout,
)


load_dotenv(Path(__file__).resolve().parents[1] / ".env")

# 修改这里即可更换基金和查询日期.
CODE = "003349.OF"
START_DATE = "2026-09-21"
END_DATE = "2026-09-24"
REPORT_START_DATE = "2026-09-01"  # 公告从月初开始查询, 便于看到记录.
BASE_URL = "https://quantapi.51ifind.com/api/v1/"


def test_sdk():
    """登录 SDK, 依次打印基金资料, 两种净值接口和公告, 最后登出."""
    login = THS_iFinDLogin(os.environ["IFIND_USERNAME"], os.environ["IFIND_PASSWORD"])
    print("SDK 登录:", login)
    if login not in (0, -201):
        return
    try:
        print("\nSDK 基金资料:")
        print(THS_BD(CODE, "ths_fund_short_name_fund;ths_fund_establishment_date_fund;ths_fund_type_fund", ";;"))
        print("\nSDK 日期序列单位净值:")
        print(THS_DS(CODE, "ths_unit_nv_fund", "", "Interval:D,Days:Tradedays,Fill:Blank",
                     START_DATE, END_DATE))
        print("\nSDK 历史净值:")
        print(THS_HQ(CODE, "netAssetValue,accumulatedNAV,adjustedNAV",
                     "Interval:D,Fill:Blank", START_DATE, END_DATE))
        print("\nSDK 基金公告:")
        print(THS_ReportQuery(CODE, f"beginrDate:{REPORT_START_DATE};endrDate:{END_DATE}",
                              "reportDate:Y,thscode:Y,reportTitle:Y"))
    finally:
        THS_iFinDLogout()


def test_http():
    """用 refresh_token 获取 access_token, 再依次请求基金资料, 净值和公告."""
    auth = requests.post(
        BASE_URL + "get_access_token",
        headers={"refresh_token": os.environ["IFIND_REFRESH_TOKEN"]}, timeout=30,
    ).json()
    print("\nHTTP 鉴权:", auth["errorcode"])
    headers = {"Content-Type": "application/json", "access_token": auth["data"]["access_token"]}

    basic = requests.post(
        BASE_URL + "basic_data_service", headers=headers,
        json={"codes": CODE, "indipara": [
            {"indicator": "ths_fund_short_name_fund", "indiparams": [""]},
            {"indicator": "ths_fund_establishment_date_fund", "indiparams": [""]},
            {"indicator": "ths_fund_type_fund", "indiparams": [""]},
        ]}, timeout=30,
    ).json()
    print("\nHTTP 基金资料, errorcode =", basic["errorcode"])
    print(pd.DataFrame(basic["tables"][0]["table"]))

    series = requests.post(
        BASE_URL + "date_sequence", headers=headers,
        json={"codes": CODE, "startdate": START_DATE, "enddate": END_DATE,
              "indipara": [{"indicator": "ths_unit_nv_fund", "indiparams": [""]}],
              "functionpara": {"Interval": "D", "Days": "Tradedays", "Fill": "Blank"}}, timeout=30,
    ).json()
    print("\nHTTP 日期序列单位净值, errorcode =", series["errorcode"])
    table = series["tables"][0]
    print(pd.DataFrame({"date": table["time"], **table["table"]}))

    nav = requests.post(
        BASE_URL + "cmd_history_quotation", headers=headers,
        json={"codes": CODE, "indicators": "netAssetValue,accumulatedNAV,adjustedNAV",
              "startdate": START_DATE, "enddate": END_DATE,
              "functionpara": {"Interval": "D", "Fill": "Blank"}}, timeout=30,
    ).json()
    print("\nHTTP 历史净值, errorcode =", nav["errorcode"])
    table = nav["tables"][0]
    print(pd.DataFrame({"date": table["time"], **table["table"]}))

    reports = requests.post(
        BASE_URL + "report_query", headers=headers,
        json={"codes": CODE, "beginrDate": REPORT_START_DATE, "endrDate": END_DATE,
              "functionpara": {}, "outputpara": "reportDate:Y,thscode:Y,reportTitle:Y"}, timeout=30,
    ).json()
    print("\nHTTP 基金公告, errorcode =", reports["errorcode"])
    if reports["tables"]:
        print(pd.DataFrame(reports["tables"][0]["table"]))
    else:
        print("该区间未返回公告记录")


if __name__ == "__main__":
    test_sdk()
    # test_http()
