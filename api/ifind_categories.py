"""通过已有 iFinD 客户端读取投资类型分类, 不接入其他数据来源."""

import re

import pandas as pd


SOURCE = "ifind"
CATEGORY_FIELDS = {
    "ths_invest_type_first_classi_fund": "class_level1",
    "ths_invest_type_second_classi_fund": "class_level2",
    "ths_tzlxsjfl_fund": "class_level3",
}


def fetch_categories(client, codes: list[str]) -> pd.DataFrame:
    """批量读取一级至三级原文; 同类分组固定采用一级和二级完整路径.

    用户提供的 THS_BD 示例中, 这三个指标均无参数, HTTP 使用空数组.
    未返回的代码仍保留空分类记录, 防止采集后继续沿用过期分类.
    """
    if not codes or len(set(codes)) != len(codes) or any(
        re.fullmatch(r"[0-9]{6}\.OF", code) is None for code in codes
    ):
        raise ValueError("分类查询需要非空且不重复的完整场外基金代码")
    data = client.query("basic_data_service", {
        "codes": ",".join(codes), "indipara": [
            {"indicator": field, "indiparams": []} for field in CATEGORY_FIELDS
        ],
    })
    records = {code: {"ts_code": code, **dict.fromkeys(CATEGORY_FIELDS.values()), "category": None}
               for code in codes}
    seen = set()
    for table in data.get("tables") or []:
        code = table.get("thscode")
        if code not in records or code in seen:
            raise ValueError("iFinD 分类返回了非请求代码或重复代码")
        seen.add(code)
        raw = pd.DataFrame(table.get("table") or {})
        if raw.empty:
            continue
        if len(raw) != 1 or set(CATEGORY_FIELDS) - set(raw.columns):
            raise ValueError("iFinD 分类返回行数异常或缺少请求字段")
        record = records[code]
        for field, column in CATEGORY_FIELDS.items():
            value = raw[field].iloc[0]
            if pd.isna(value):
                continue
            if not isinstance(value, str):
                raise ValueError("iFinD 分类名称不是文本")
            value = value.strip()
            record[column] = None if value in ("", "--") else value
        if record["class_level1"] and record["class_level2"]:
            record["category"] = f"{record['class_level1']} / {record['class_level2']}"
    frame = pd.DataFrame(records.values())
    frame.attrs["fetched_at"] = data.get("_fetched_at")
    return frame
