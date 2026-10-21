"""Strict shared source timestamp parsing. Never consult the host timezone."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def source_datetime(value, source_timezone=None):
    if isinstance(value,str):
        # Require an actual time; dates alone do not establish a timestamp.
        if "T" not in value and " " not in value: raise ValueError("Timestamp required")
        parsed=datetime.fromisoformat(value.replace("Z","+00:00"))
    elif isinstance(value,datetime): parsed=value
    else: raise ValueError("Timestamp required")
    if parsed.tzinfo is None:
        if not source_timezone: raise ValueError("Explicit timezone required")
        try: zone=ZoneInfo(source_timezone)
        except (KeyError,ValueError): raise ValueError("Unknown source timezone") from None
        first,second=parsed.replace(tzinfo=zone,fold=0),parsed.replace(tzinfo=zone,fold=1)
        if first.utcoffset()!=second.utcoffset() or first.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)!=parsed:
            raise ValueError("Ambiguous or nonexistent source time")
        parsed=first
    return parsed.astimezone(timezone.utc)
