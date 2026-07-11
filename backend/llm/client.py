"""
llm/client.py — Inference engine singleton and async streaming generator.

Responsibilities:
  - Load the GGUF model into RAM exactly once at application startup.
  - Track model operational state (loading / idle / processing / error).
  - Expose generate_stream(), an async generator that yields tokens without
    blocking the FastAPI event loop.

Bug-fixes applied vs. llm_architecture_design.md:
  [Fix 1] The streaming pattern uses a queue.Queue bridge so that the entire
           token iteration loop runs inside the worker thread — not on the event
           loop. The original pseudocode only offloaded the Llama() constructor
           to the thread pool, leaving per-token blocking on the event loop.
  [Fix 2] The is_busy property is exposed so the router can check it BEFORE
           persisting the user message, preventing orphaned DB rows on 423.
  [Fix 3] ModelStatus tracks all four states required by /internal/model/status.
  [Fix 4] asyncio.get_running_loop() replaces the deprecated get_event_loop().
"""
from __future__ import annotations

import asyncio
import queue
import threading
from enum import Enum
from typing import AsyncGenerator

from llama_cpp import Llama

from core.config import settings

import logging
_logger = logging.getLogger("llm.client")


from core.metrics import model_status_gauge, model_busy_gauge

# ── Sentinel — signals end-of-stream from the worker thread ──────────────────
_SENTINEL = object()

# Structural stop token for tool-calling. Code-level constant, not .env —
# it is part of the tool-call protocol, not a tunable inference parameter.
_TOOL_CALL_STOP = "</tool_call>"


# ── Model state enum ─────────────────────────────────────────────────────────

class ModelStatus(str, Enum):
    LOADING = "loading"       # Model is being read into RAM at startup
    IDLE = "idle"             # Loaded and ready for a request
    PROCESSING = "processing" # Currently generating tokens
    ERROR = "error"           # Failed to load — inference unavailable


# ── Singleton client ──────────────────────────────────────────────────────────

class LLMClient:
    """
    Singleton wrapper around a llama_cpp.Llama instance.

    Usage:
        # In main.py lifespan (startup):
        await LLMClient.initialize()

        # In a service or router:
        client = LLMClient.get()
        if client.is_busy:
            raise HTTPException(423, "Model is busy")
        async for token in client.generate_stream(prompt):
            ...
    """

    _instance: "LLMClient | None" = None

    def __init__(self, llm: Llama) -> None:
        self._llm: Llama = llm
        self._status: ModelStatus = ModelStatus.IDLE
        self._last_finish_reason: str = "stop"
        # asyncio.Semaphore must be created inside a running event loop.
        # It is set to None here and initialized in initialize() which is async.
        self._semaphore: asyncio.Semaphore | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    @classmethod
    async def initialize(cls) -> None:
        """
        Load the GGUF model and store the singleton instance.

        Must be called exactly once from the FastAPI lifespan startup hook.
        Sets _status to LOADING before the Llama() constructor runs (which
        may take 5–15 seconds for a 2.5 GB model), and to IDLE when done.
        Sets _status to ERROR on any exception — the API process stays alive
        so /internal/health still responds; only inference is unavailable.
        """
        if cls._instance is not None:
            return  # Already initialized — idempotent


        # Create a temporary placeholder so status is visible before load completes.
        placeholder = object.__new__(cls)
        placeholder._status = ModelStatus.LOADING  # type: ignore[attr-defined]
        cls._instance = placeholder  # type: ignore[assignment]
        model_status_gauge.set(0) # loading

        try:
            # Llama() reads and mmaps the GGUF file. This is the slow step.
            llm = Llama(
                model_path=settings.model_path,
                chat_format="chatml",
                n_threads=settings.n_threads,
                n_gpu_layers=settings.n_gpu_layers,
                n_ctx=settings.model_max_context,
                verbose=False,  # Suppress llama.cpp progress output to stderr
            )
            instance = cls(llm)

            _logger.info(
                "Model loaded | %s | GPU Layers: %s | Context: %d Stop tokens: %s",
                settings.model_path,
                "all" if settings.n_gpu_layers == -1 else (
                    "CPU-only" if settings.n_gpu_layers == 0 else settings.n_gpu_layers
                ),
                settings.model_max_context,
                settings.llm_stop_tokens,
            )
            
            # Semaphore must be created in the running event loop.
            instance._semaphore = asyncio.Semaphore(1)
            cls._instance = instance
            model_status_gauge.set(1) # idle

        except Exception as exc:
            # Keep the placeholder alive so status reads as ERROR, not LOADING.
            placeholder._status = ModelStatus.ERROR  # type: ignore[attr-defined]
            model_status_gauge.set(3)  # error
            raise RuntimeError(f"LLMClient failed to load model: {exc}") from exc

    @classmethod
    def get(cls) -> "LLMClient":
        """
        Return the singleton instance.

        Raises RuntimeError if called before initialize() has been awaited.
        This is a programming error — the lifespan must have failed silently.
        """
        if cls._instance is None:
            raise RuntimeError(
                "LLMClient.get() called before initialize(). "
                "Ensure initialize() is awaited in the FastAPI lifespan."
            )
        return cls._instance  # type: ignore[return-value]

    # ── State properties ──────────────────────────────────────────────────────

    @property
    def status(self) -> ModelStatus:
        """Current operational state. Read by /internal/model/status."""
        return self._status

    @property
    def is_busy(self) -> bool:
        """
        True if inference is currently running.

        The router MUST check this BEFORE persisting the user message.
        Returning 423 after the user message is written would leave an
        orphaned row with no corresponding assistant reply.
        """
        if self._semaphore is None:
            return False
        return self._semaphore.locked()

    @property
    def last_finish_reason(self) -> str:
        """
        The finish_reason from the most recent completed generation.
        Values: 'stop' (natural end), 'length' (hit max_new_tokens), 'error'.
        Safe to read after the generate_stream() async generator is exhausted.
        """
        return self._last_finish_reason

    # ── Inference ─────────────────────────────────────────────────────────────

    async def generate_stream(self, prompt: str) -> AsyncGenerator[str, None]:
        """
        Async generator that yields one token string at a time.

        The entire llama_cpp iteration loop runs inside a worker thread via
        a queue.Queue bridge. The event loop is never blocked — it only wakes
        up when a token (or the sentinel) is placed in the queue.

        The semaphore ensures only one inference runs at a time. The caller
        must check is_busy BEFORE calling this method and raise 423 if True.

        Yields:
            Raw token strings as produced by llama_cpp (e.g. " the", "\\n").

        Raises:
            RuntimeError: If the model was not loaded (status == ERROR).
        """
        if self._status == ModelStatus.ERROR:
            raise RuntimeError("Model is in ERROR state and cannot generate.")

        assert self._semaphore is not None, "Semaphore not initialized"

        loop = asyncio.get_running_loop()
        token_queue: queue.Queue = queue.Queue()
        stop_event = threading.Event()

        def _run_inference() -> None:
            """
            Runs entirely in a worker thread (via run_in_executor).

            Iterates over the llama_cpp streaming generator and puts each token
            into the queue. Captures the finish_reason from the final chunk.
            The stop_event allows the async side to signal early termination
            (e.g., client disconnect). A _SENTINEL is always placed last so the
            async reader knows the thread has finished.
            """
            last_finish_reason = "stop"
            try:
                stream = self._llm(
                    prompt,
                    max_tokens=settings.max_new_tokens,
                    stop=[*settings.llm_stop_tokens, _TOOL_CALL_STOP],
                    temperature=settings.llm_temperature,
                    top_p=settings.llm_top_p,
                    top_k=settings.llm_top_k,
                    repeat_penalty=settings.llm_repeat_penalty,
                    min_p=settings.llm_min_p,
                    stream=True,
                )
                for chunk in stream:
                    if stop_event.is_set():
                        break
                    choice = chunk["choices"][0]
                    # finish_reason is non-None only on the very last chunk
                    fr = choice.get("finish_reason")
                    if fr:
                        last_finish_reason = fr
                    token_text: str = choice["text"]
                    if token_text:  # llama.cpp can emit empty strings
                        token_queue.put(token_text)
            except Exception as exc:
                token_queue.put(exc)
                last_finish_reason = "error"
            finally:
                # Set before sentinel so the async side can read it immediately
                # after the generator exits.
                self._last_finish_reason = last_finish_reason
                token_queue.put(_SENTINEL)

        async with self._semaphore:
            self._status = ModelStatus.PROCESSING
            model_status_gauge.set(2)  # processing
            model_busy_gauge.set(1)
            try:
                # Fire the blocking inference into the thread pool.
                loop.run_in_executor(None, _run_inference)

                # Drain the queue token-by-token on the event loop.
                # Each queue.get() call is offloaded to a thread so it doesn't
                # block the event loop while waiting for the next token.
                while True:
                    item = await loop.run_in_executor(None, token_queue.get)

                    if item is _SENTINEL:
                        break

                    if isinstance(item, Exception):
                        raise item

                    yield item

            finally:
                # Signal the inference thread to stop iterating (client disconnect,
                # exception, or normal completion all land here).
                stop_event.set()
                self._status = ModelStatus.IDLE
                model_status_gauge.set(1)  # idle
                model_busy_gauge.set(0)