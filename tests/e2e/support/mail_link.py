"""Read a synthetic captured email for a browser test; never an application API."""
from email import policy
from email.parser import BytesParser
from pathlib import Path
import re
import sys

recipient = sys.argv[1]
if not recipient.endswith("@example.invalid"):
    raise SystemExit("Synthetic recipients only")
for file in Path(".runtime/f03-mail").glob("*.eml"):
    message = BytesParser(policy=policy.default).parsebytes(file.read_bytes())
    if recipient not in str(message["To"]):
        continue
    body = message.get_body(preferencelist=("plain",))
    if body:
        match = re.search(r"https://[^\s<>]+/login-actions/action-token\?[^\s<>]+", body.get_content())
        if match:
            print(match.group(0))
            break
else:
    raise SystemExit("No captured synthetic invitation found")
