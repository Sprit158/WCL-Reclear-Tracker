from __future__ import annotations

from api.wcl_api_v1 import WCLApiError, WCLV1Client


class WCLClient(WCLV1Client):
    """
    Compatibility wrapper so the rest of the app can keep importing WCLClient.

    Current mode:
        v1_api_key
    """
    pass
