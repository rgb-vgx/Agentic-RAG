"""Qwen3-Reranker-4B scoring service.

Qwen3-Reranker is a causal LM: a (query, document) pair is scored by the
probability of the next token being "yes" vs "no", using the prompt from the
model card. llama.cpp's `/rerank` uses rank pooling and is not correct for
this model, so the official transformers implementation runs here instead.
"""

import asyncio
import os
import threading
from contextlib import asynccontextmanager

import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

MODEL_ID = os.getenv("MODEL_ID", "Qwen/Qwen3-Reranker-4B")
LOAD_IN_4BIT = os.getenv("LOAD_IN_4BIT", "true").lower() in ("1", "true", "yes")
BATCH_SIZE = max(1, int(os.getenv("BATCH_SIZE", "8")))
MAX_LENGTH = int(os.getenv("MAX_LENGTH", "8192"))
DEFAULT_INSTRUCTION = (
    "Given a web search query, retrieve relevant passages that answer the query"
)

SYSTEM = (
    "Judge whether the Document meets the requirements based on the Query "
    'and the Instruct provided. Note that the answer can only be "yes" or "no".'
)
PREFIX = f"<|im_start|>system\n{SYSTEM}<|im_end|>\n<|im_start|>user\n"
SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"

_model = None
_tokenizer = None
_yes_id = 0
_no_id = 0
_lock = threading.Lock()


def _load() -> None:
    global _model, _tokenizer, _yes_id, _no_id
    quantization = None
    if LOAD_IN_4BIT:
        quantization = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.float16,
        )
    _tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, padding_side="left")
    _model = AutoModelForCausalLM.from_pretrained(
        MODEL_ID,
        quantization_config=quantization,
        device_map="auto",
        torch_dtype=torch.float16 if quantization is None else None,
    )
    _model.eval()
    _yes_id = _tokenizer.convert_tokens_to_ids("yes")
    _no_id = _tokenizer.convert_tokens_to_ids("no")


@asynccontextmanager
async def lifespan(_: FastAPI):
    _load()
    yield


app = FastAPI(title="Qwen3-Reranker", lifespan=lifespan)


class RerankRequest(BaseModel):
    query: str = Field(min_length=1)
    documents: list[str]
    instruction: str | None = None
    top_n: int | None = None
    model: str | None = None


def _build_prompt(query: str, document: str, instruction: str) -> str:
    return (
        f"{PREFIX}"
        f"<Instruct>: {instruction}\n"
        f"<Query>: {query}\n"
        f"<Document>: {document}"
        f"{SUFFIX}"
    )


def _score_sync(prompts: list[str]) -> list[float]:
    scores: list[float] = []
    with _lock:
        for start in range(0, len(prompts), BATCH_SIZE):
            batch = prompts[start : start + BATCH_SIZE]
            inputs = _tokenizer(
                batch,
                padding=True,
                truncation=True,
                max_length=MAX_LENGTH,
                return_tensors="pt",
            ).to(_model.device)
            with torch.inference_mode():
                logits = _model(**inputs).logits[:, -1, :]
                pair = torch.stack(
                    [logits[:, _no_id], logits[:, _yes_id]], dim=1
                )
                probs = torch.nn.functional.log_softmax(pair, dim=1)[:, 1].exp()
            scores.extend(float(value) for value in probs.tolist())
    return scores


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "model_loaded": _model is not None}


@app.post("/rerank")
async def rerank(request: RerankRequest) -> dict:
    if _model is None or _tokenizer is None:
        raise HTTPException(503, "Model is still loading")
    if not request.documents:
        return {"model": MODEL_ID, "object": "list", "results": []}

    instruction = request.instruction or DEFAULT_INSTRUCTION
    prompts = [
        _build_prompt(request.query, document, instruction)
        for document in request.documents
    ]
    scores = await asyncio.to_thread(_score_sync, prompts)

    results = [
        {"index": index, "relevance_score": score}
        for index, score in enumerate(scores)
    ]
    results.sort(key=lambda item: item["relevance_score"], reverse=True)
    if request.top_n is not None:
        results = results[: max(0, request.top_n)]
    return {"model": MODEL_ID, "object": "list", "results": results}
