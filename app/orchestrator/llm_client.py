"""LLM 客户端（Phase 4）— 复用套价系统的 GLM 调用约定。

与 decoration-pricing-system 的 call_deepseek / parse_ai_response 保持协议一致：
- OpenAI 兼容 POST {base}/chat/completions，Bearer 鉴权，temperature=0
- model 以 glm 开头 → 智谱按量计费端点优先、Coding Plan 套餐端点兜底；
  鉴权/余额类错误（401/402/403/1113）判定为 key 与端点不匹配，自动换备端点
- 可重试状态（429/500/502/503/504）按退避序列重试
- 推理模型兜底 reasoning_content；响应按健壮 JSON 解析（剥代码块/平衡提取/截断修复）

编排器为线程模型，此处提供同步版本（套价系统为 asyncio）。
"""
import json
import logging
import re
import time
from typing import List, Optional

import requests
from flask import current_app

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}
RETRY_BACKOFF_SECONDS = [2, 4, 8]
ENDPOINT_MISMATCH_STATUS = {401, 402, 403}


def _candidates(model: str) -> List[str]:
    """按 model 返回候选端点（依序尝试）——对齐套价系统 _llm_candidates。"""
    if model.lower().startswith('glm'):
        urls = []
        main = (current_app.config.get('LLM_BASE_URL_ZHIPU') or '').rstrip('/')
        if main:
            urls.append(main)
        fb = (current_app.config.get('LLM_BASE_URL_ZHIPU_FALLBACK') or '').rstrip('/')
        if fb and fb not in urls:
            urls.append(fb)
        return urls
    base = (current_app.config.get('LLM_BASE_URL') or '').rstrip('/')
    return [base] if base else []


def _is_endpoint_mismatch(status_code: int, body: str) -> bool:
    """鉴权/余额类错误 → key 类型与端点不匹配，值得换端点重试。"""
    if status_code in ENDPOINT_MISMATCH_STATUS:
        return True
    return '1113' in body   # 智谱「余额不足或无可用资源包」（可能包在 HTTP 429 里）


def call_llm(system_prompt: str, user_prompt: str, api_key: str,
             model: str = None, max_tokens: int = None,
             timeout: int = None) -> str:
    """同步调用 LLM。失败抛异常，由调用方优雅降级。"""
    use_model = model or current_app.config['LLM_MODEL']
    payload = {
        'model': use_model,
        'max_tokens': max_tokens or current_app.config['LLM_MAX_TOKENS'],
        'temperature': 0,
        'messages': [{'role': 'system', 'content': system_prompt},
                     {'role': 'user', 'content': user_prompt}],
    }
    headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {api_key}'}
    timeout = timeout or current_app.config['LLM_TIMEOUT_SECONDS']
    candidates = _candidates(use_model)
    if not candidates:
        raise RuntimeError('未配置 LLM 端点')

    last_err: Optional[Exception] = None
    for base in candidates:
        url = base + '/chat/completions'
        for attempt in range(len(RETRY_BACKOFF_SECONDS) + 1):
            try:
                r = requests.post(url, json=payload, headers=headers, timeout=timeout)
            except requests.RequestException as exc:
                last_err = exc
                break   # 网络异常 → 换备端点
            if r.status_code == 200:
                msg = (r.json().get('choices') or [{}])[0].get('message') or {}
                content = (msg.get('content') or '').strip()
                if not content:
                    content = (msg.get('reasoning_content') or '').strip()
                if content:
                    return content
                last_err = RuntimeError('LLM 返回空内容')
                break
            body = r.text[:200]
            mismatch = _is_endpoint_mismatch(r.status_code, body)
            if r.status_code in RETRYABLE_STATUS and not mismatch \
                    and attempt < len(RETRY_BACKOFF_SECONDS):
                time.sleep(RETRY_BACKOFF_SECONDS[attempt])
                continue
            if mismatch:
                last_err = RuntimeError(f'LLM error {r.status_code}: {body}')
                break   # 换备端点
            raise RuntimeError(f'LLM error {r.status_code}: {body}')
        # 内层循环耗尽（无 return/break）→ 尝试下一个候选端点
    raise last_err or RuntimeError('LLM error: all endpoints exhausted')


def parse_json_response(raw: str) -> Optional[dict]:
    """解析 LLM 返回的 JSON——剥代码块、平衡提取、截断修复（对齐套价系统）。"""
    if not raw or not str(raw).strip():
        return None
    text = re.sub(r'```json|```', '', str(raw)).strip()
    try:
        return json.loads(text)
    except Exception:
        pass

    # 1) 提取第一个平衡的 JSON 对象（兼容前后杂质）
    start = text.find('{')
    if start == -1:
        return None
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == '\\':
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except Exception:
                        return None

    # 2) 截断修复：闭合未完成的字符串与括号后重试
    buf, stack = [], []
    in_str, esc = False, False
    for c in text[start:]:
        buf.append(c)
        if in_str:
            if esc:
                esc = False
            elif c == '\\':
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c in '{[':
                stack.append(c)
            elif c == '}' and stack and stack[-1] == '{':
                stack.pop()
            elif c == ']' and stack and stack[-1] == '[':
                stack.pop()
    if in_str:
        buf.append('"')
    for b in reversed(stack):
        buf.append('}' if b == '{' else ']')
    try:
        return json.loads(''.join(buf))
    except Exception:
        return None


class LLMCallBudget:
    """项目级调用预算（架构文档 §8.3）——超出即拒绝，防止审计成本失控。"""

    def __init__(self, max_calls: int):
        self.max_calls = max_calls
        self.calls_made = 0

    def allow(self) -> bool:
        return self.calls_made < self.max_calls

    def count(self) -> None:
        self.calls_made += 1
