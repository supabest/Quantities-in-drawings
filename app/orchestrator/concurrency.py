"""并发管理器 — 信号量限流、超时、部分失败不阻塞。

Phase 1 用线程池（OpenCV 解析是 CPU 密集 + GIL 释放型，线程池足够）。
Phase 2 若引入 LLM Agent，切换为 asyncio + Semaphore。
Phase 5a 后：写锁已删；SQLite 仍保留 busy timeout，但 worker 内写入不再需要串行。
"""
import logging
import concurrent.futures

logger = logging.getLogger(__name__)

# 并发上限（Phase 1 图纸级并行）
MAX_DRAWING_WORKERS = 4
GLOBAL_TIMEOUT_PER_TASK = 300      # 秒


def run_tasks(tasks: list, worker_fn, max_workers: int = MAX_DRAWING_WORKERS) -> list:
    """并发执行任务列表。

    Args:
        tasks: 任务描述列表（任意可哈希对象，如 drawing.id）
        worker_fn: callable(task) -> (result, error_detail|None)
                   worker 内部需自行管理 app context 与异常。

    Returns:
        [(task, result, error_detail), ...] — 失败任务 error_detail 非空，不阻塞其他任务。
    """
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(worker_fn, t): t for t in tasks}
        for future in concurrent.futures.as_completed(futures):
            task = futures[future]
            try:
                result, err = future.result(timeout=GLOBAL_TIMEOUT_PER_TASK)
                results.append((task, result, err))
            except concurrent.futures.TimeoutError:
                logger.error("任务 %s 超时(%ss)", task, GLOBAL_TIMEOUT_PER_TASK)
                results.append((task, None, f"timeout after {GLOBAL_TIMEOUT_PER_TASK}s"))
            except Exception as exc:  # 单任务失败不阻塞整体
                logger.exception("任务 %s 异常", task)
                results.append((task, None, f"{type(exc).__name__}: {exc}"))
    return results
