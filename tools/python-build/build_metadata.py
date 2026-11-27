"""Stable UTC metadata for source-built CPython."""

from datetime import datetime, timezone


SOURCE_DATE_EPOCH = "1700000000"
_stamp = datetime.fromtimestamp(int(SOURCE_DATE_EPOCH), timezone.utc)
# Match the C __DATE__ spelling without depending on the build locale.
_month = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()[_stamp.month - 1]
BUILD_DATE = f"{_month} {_stamp.day:2d} {_stamp.year:04d}"
BUILD_TIME = _stamp.strftime("%H:%M:%S")
BUILD_INFO_SUFFIX = f", {BUILD_DATE}, {BUILD_TIME}"
