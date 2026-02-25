import akshare as ak


def main() -> None:
    df = ak.stock_news_em(symbol="300750")
    print(type(df), getattr(df, "shape", None))
    try:
        print(df.head(3).to_string(index=False))
    except Exception:
        print(df.head(3))


if __name__ == "__main__":
    main()

