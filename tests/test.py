from api.tushare_client import Source

def main() -> None:
    source = Source()

    funds = source.query(
        endpoint='fund_adj',
        fields='ts_code,adj_factor',
        ts_code='040001.OF',
    )

    print(funds.to_string(index=False))


if __name__ == '__main__':
    main()