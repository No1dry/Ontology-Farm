"""Explicit operations command; application delivery never performs DDL."""
from datetime import date
from sqlalchemy import create_engine, text


def maintain_partitions(url, start, end, *, owner=False):
    start, end = date.fromisoformat(str(start)), date.fromisoformat(str(end))
    count = (end.year-start.year)*12 + end.month-start.month
    if start.day != 1 or end.day != 1 or not 1 <= count <= 36:
        raise ValueError('Use first-of-month bounds for 1..36 months, end exclusive')
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            user = connection.execute(text('SELECT current_user')).scalar_one()
            if user != ('lzo_timeseries_owner' if owner else 'lzo_timeseries_maintainer'):
                raise RuntimeError('Dedicated timeseries maintenance account required')
            result = []
            month = start
            while month < end:
                result.append(connection.execute(text('SELECT public.telemetry_ensure_month(:month)'), {'month': month}).scalar_one())
                month = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
            return result
    finally:
        engine.dispose()
