import inspect

import akshare as ak


def main() -> None:
    candidates = [
        "stock_news_em",
        "stock_news_main_cx",
        "stock_telegraph_cls",
        "stock_news_cx",
        "news_cctv",
        "js_news",
    ]

    for name in candidates:
        if hasattr(ak, name):
            fn = getattr(ak, name)
            print("FOUND", name)
            try:
                print("SIGNATURE", str(inspect.signature(fn)))
            except Exception:
                print("SIGNATURE", "<unknown>")
            doc = (getattr(fn, "__doc__", "") or "").strip().replace("\n", " ")
            print("DOC", doc[:800])
            print("-" * 60)

    print("dir contains stock_news?", [x for x in dir(ak) if "news" in x and "stock" in x][:80])


if __name__ == "__main__":
    main()

