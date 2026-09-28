from __future__ import annotations
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Any

@dataclass(frozen=True)
class LoadResult:
    value: Any = None
    error: Exception|None = None

class AssetWorker:
    """Small cross-platform worker pool for non-blocking asset operations."""
    def __init__(self, workers:int=2): self._pool=ThreadPoolExecutor(max_workers=max(1,int(workers)),thread_name_prefix="character-assets")
    def submit(self, operation:Callable, *args, done:Callable[[LoadResult],None]|None=None, **kwargs) -> Future:
        future=self._pool.submit(operation,*args,**kwargs)
        if done:
            def finish(f):
                try: result=LoadResult(value=f.result())
                except Exception as exc: result=LoadResult(error=exc)
                done(result)
            future.add_done_callback(finish)
        return future
    def close(self): self._pool.shutdown(wait=False,cancel_futures=True)
