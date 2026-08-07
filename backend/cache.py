import time
import hashlib
from typing import Dict, Optional, Tuple

# TTL of 5 minutes (300 seconds)
DEFAULT_TTL = 300

class ComparisonCache:
    def __init__(self, ttl: int = DEFAULT_TTL):
        self.ttl = ttl
        # Map: url_hash (str) -> (run_id (str), timestamp (float))
        self._cache: Dict[str, Tuple[str, float]] = {}

    def _get_hash(self, ref_url: str, live_url: str) -> str:
        # Standardize and hash URLs
        ref = ref_url.strip().lower()
        live = live_url.strip().lower()
        combined = f"{ref}|||{live}"
        return hashlib.sha256(combined.encode('utf-8')).hexdigest()

    def get(self, ref_url: str, live_url: str) -> Optional[str]:
        h = self._get_hash(ref_url, live_url)
        if h in self._cache:
            run_id, timestamp = self._cache[h]
            # Check TTL
            if time.time() - timestamp < self.ttl:
                return run_id
            else:
                # Expired
                del self._cache[h]
        return None

    def set(self, ref_url: str, live_url: str, run_id: str) -> None:
        h = self._get_hash(ref_url, live_url)
        self._cache[h] = (run_id, time.time())

    def clear(self) -> None:
        self._cache.clear()

# Global cache instance
comparison_cache = ComparisonCache()
