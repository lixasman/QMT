from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.xtdata_parsing import xtdata_field_dict_to_df
from etf_chip_engine.data import xtdata_provider
from core.adapters.data_adapter import XtDataAdapter

print("imports_ok")
