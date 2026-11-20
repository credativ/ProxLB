"""
Relax only the RFC 5280 "strict" X.509 checks for HTTPS made through
urllib3/requests (proxmoxer, ProxLB).

Why: Python 3.13+ and urllib3 2.x enable ``VERIFY_X509_STRICT``. Proxmox VE
root CAs created before pve-cluster 9.1.2 have no keyUsage extension, so
every verified connection to such a cluster fails with
"CA cert does not include key usage extension" (Proxmox bug #6701).

What stays: certificate chain and hostname are still fully verified -
this restores the behaviour of Python <= 3.12. Nothing is changed when
``PROXLB_TLS_STRICT=1`` is set.

proxlb-agent imports this file itself and puts its directory on PYTHONPATH
for the ProxLB runs it starts, where Python loads it as ``sitecustomize``.
"""
import os
import ssl
from typing import Any


def relax_x509_strict() -> bool:
    if os.environ.get("PROXLB_TLS_STRICT") == "1":
        return False
    strict = getattr(ssl, "VERIFY_X509_STRICT", 0)
    if not strict:
        return False
    try:
        import urllib3.connection
        import urllib3.util.ssl_ as u3ssl
    except ImportError:
        return False
    original = getattr(u3ssl.create_urllib3_context, "__wrapped__", u3ssl.create_urllib3_context)

    def create_urllib3_context(*args: Any, **kwargs: Any) -> ssl.SSLContext:
        ctx: ssl.SSLContext = original(*args, **kwargs)
        ctx.verify_flags &= ~strict
        return ctx

    setattr(create_urllib3_context, "__wrapped__", original)
    setattr(u3ssl, "create_urllib3_context", create_urllib3_context)
    setattr(urllib3.connection, "create_urllib3_context", create_urllib3_context)
    try:  # requests >= 2.32 builds a context at import time
        import requests.adapters as ra
        if hasattr(ra, "create_urllib3_context"):
            setattr(ra, "create_urllib3_context", create_urllib3_context)
        pre = getattr(ra, "_preloaded_ssl_context", None)
        if pre is not None:
            pre.verify_flags &= ~strict
    except ImportError:
        pass
    return True


relax_x509_strict()
