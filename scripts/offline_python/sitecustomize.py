"""Inherited by acceptance Python children only when explicitly enabled."""
import os
if os.environ.get('LZO_ACCEPTANCE_OFFLINE') == '1':
    from offline_guard import install
    install()
