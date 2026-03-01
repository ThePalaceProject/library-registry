import datetime

import pytz


def utc_now() -> datetime.datetime:
    """Get the current time in UTC.

    :return: datetime object
    """
    return datetime.datetime.now(tz=pytz.UTC)
