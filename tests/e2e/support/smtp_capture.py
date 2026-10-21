"""Real SMTP capture for synthetic F03 invitation acceptance only."""
import asyncio
import argparse
from email import policy
from email.parser import BytesParser
from pathlib import Path
from uuid import uuid4
from aiosmtpd.controller import Controller


class Capture:
    def __init__(self, directory):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):
        if not address.lower().endswith("@example.invalid"):
            return "550 Only synthetic example.invalid recipients are permitted"
        envelope.rcpt_tos.append(address)
        return "250 OK"

    async def handle_DATA(self, server, session, envelope):
        self.directory.joinpath(uuid4().hex + ".eml").write_bytes(envelope.original_content)
        return "250 Captured synthetic test message"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    controller = Controller(Capture(args.directory), hostname="127.0.0.1", port=11025)
    controller.start()
    asyncio.run(asyncio.Event().wait())
