"""Pre-create bounded partitions with an explicitly supplied maintenance account."""
import argparse
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend/src'))
from lzo.telemetry.maintenance import maintain_partitions


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', required=True, help='Inclusive month, YYYY-MM-01')
    parser.add_argument('--end', required=True, help='Exclusive month, YYYY-MM-01; at most 36 months')
    args = parser.parse_args()
    url = os.environ.get('LZO_TIMESERIES_MAINTENANCE_DATABASE_URL')
    if not url:
        raise RuntimeError('Explicit maintenance account URL required')
    for name in maintain_partitions(url, args.start, args.end):
        print(name)


if __name__ == '__main__':
    main()
