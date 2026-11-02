"""在服务器筛选基金候选, 不把全量名单发送给浏览器控件."""


SEARCH_LIMIT = 50


def fund_candidates(funds: dict[str, str], keyword: str,
                    selected: list[str]) -> tuple[dict[str, str], str]:
    """返回最多 50 条匹配及全部已选项, 已选项不因关键词改变而丢失."""
    kept = {code: funds[code] for code in selected if code in funds}
    needle = keyword.strip().casefold()
    if not needle:
        return kept, "输入基金代码或名称并按 Enter 搜索, 已选基金会保留."
    matches = []
    for code, name in funds.items():
        if needle in f"{code} {name}".casefold():
            matches.append((code, name))
            if len(matches) > SEARCH_LIMIT:
                break
    candidates = {**kept, **dict(matches[:SEARCH_LIMIT])}
    if len(matches) > SEARCH_LIMIT:
        note = f"匹配超过 {SEARCH_LIMIT} 只基金, 显示前 {SEARCH_LIMIT} 条及已选基金. 请补充关键词缩小范围."
    elif matches:
        note = f"匹配 {len(matches)} 只基金, 已选基金始终保留."
    else:
        note = "未找到匹配基金, 请调整关键词或直接输入基金代码. 已选基金仍保留."
    return candidates, note
