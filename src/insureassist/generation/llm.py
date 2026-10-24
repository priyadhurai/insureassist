"""The LLM connection: send instructions + prompt, get back a structured JSON answer.

`LLMProvider` is the shape every provider must have. The rest of the code only uses
this shape, so Gemini could be swapped for another model by writing one new class,
and tests use FakeProvider (no API key, no cost, instant).
"""

import time
from collections.abc import Callable
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from insureassist.config import settings

Status = Literal["answered", "insufficient_evidence", "conflicting_evidence"]


class LlmOutput(BaseModel):
    """The JSON shape we force the model to return (structured output)."""

    status: Status
    answer: str = Field(description="1-4 sentences; cite facts like [E1]")
    citations: list[str] = Field(description="evidence ids used, e.g. ['E1', 'E3']")


class GenerationError(Exception):
    """The LLM could not produce an answer. The message is safe to show to users;
    provider details (which may include request info) stay in __cause__ for debugging."""


class RateLimitError(GenerationError):
    """Rate limit or quota reached (HTTP 429), even after retries. Batch jobs should stop
    instead of sending more requests that will fail too."""


class LLMProvider(Protocol):
    model_name: str

    def generate(self, system_instruction: str, prompt: str) -> LlmOutput: ...


class GeminiProvider:
    # HTTP status codes worth retrying: rate limit and temporary server problems.
    RETRYABLE = {429, 500, 502, 503, 504}

    def __init__(self, client: object | None = None) -> None:
        from google import genai  # imported here so tests never need the SDK set up

        if client is None:
            if settings.gemini_api_key is None or not settings.gemini_api_key.get_secret_value():
                raise GenerationError("GEMINI_API_KEY is not set. Add it to your .env file.")
            client = genai.Client(
                api_key=settings.gemini_api_key.get_secret_value(),
                http_options={"timeout": settings.llm_timeout_seconds * 1000},  # milliseconds
            )
        self._client = client
        self.model_name = settings.gemini_model

    def generate(self, system_instruction: str, prompt: str) -> LlmOutput:
        from google.genai import errors, types

        config = types.GenerateContentConfig(
            system_instruction=system_instruction,  # kept separate from the evidence
            temperature=settings.llm_temperature,
            max_output_tokens=settings.llm_max_output_tokens,
            response_mime_type="application/json",
            response_schema=LlmOutput,  # the model must return exactly this JSON shape
        )
        for attempt in range(settings.llm_max_retries + 1):
            try:
                response = self._client.models.generate_content(  # type: ignore[attr-defined]
                    model=self.model_name, contents=prompt, config=config
                )
                break
            except errors.APIError as error:
                retry = error.code in self.RETRYABLE and attempt < settings.llm_max_retries
                if not retry:
                    error_type = RateLimitError if error.code == 429 else GenerationError
                    raise error_type(_safe_message(error.code)) from error
                time.sleep(2**attempt)  # wait 1 s, 2 s, ... before trying again

        return parse_llm_json(response.text or "")


def parse_llm_json(text: str) -> LlmOutput:
    """Read the model's JSON answer. Small local models sometimes wrap the JSON in
    ```json fences or add a sentence around it, so we take the part from the first "{"
    to the last "}". Anything that still isn't valid becomes a safe error."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise GenerationError("The language model returned an invalid answer.")
    try:
        return LlmOutput.model_validate_json(text[start : end + 1])
    except ValueError as error:
        raise GenerationError("The language model returned an invalid answer.") from error


# Gemini is FORCED into this shape by its API (response_schema). A local model is not,
# so we describe the shape in words and show an example.
JSON_FORMAT_RULE = """
Reply with ONLY one JSON object, no other text, in exactly this format:
{"status": "answered", "answer": "The deductible is $500 per claim [E1].", "citations": ["E1"]}
"status" must be one of: "answered", "insufficient_evidence", "conflicting_evidence".
"""


class HuggingFaceProvider:
    """Runs an open model from Hugging Face locally (CPU is fine for small models).

    No API key, no quota, and the documents never leave this computer. Slower and less
    capable than Gemini. The model is downloaded once into the Hugging Face cache.
    """

    def __init__(self, model_name: str | None = None) -> None:
        # Imported here: loading transformers/torch takes seconds and lots of memory.
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.model_name = model_name or settings.hf_llm_model
        self._torch = torch
        self._tokenizer = AutoTokenizer.from_pretrained(self.model_name)
        # bfloat16 = half the memory of float32 (1.5B parameters: ~3 GB instead of ~6 GB).
        # Typed as Any: transformers' own type hints are too broad to be useful here.
        # (from_pretrained already returns the model in inference/eval mode.)
        self._model: Any = AutoModelForCausalLM.from_pretrained(
            self.model_name, dtype=getattr(torch, settings.hf_dtype)
        )

    def generate(self, system_instruction: str, prompt: str) -> LlmOutput:
        messages = [
            {"role": "system", "content": system_instruction + JSON_FORMAT_RULE},
            {"role": "user", "content": prompt},
        ]
        # The chat template adds the model's special "who is speaking" markers.
        # transformers' type hints allow several return types; with return_dict=True
        # it is a dict of tensors, so we type it loosely here.
        inputs: Any = self._tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, return_tensors="pt", return_dict=True
        )
        with self._torch.no_grad():
            output = self._model.generate(
                **inputs,
                max_new_tokens=settings.hf_max_new_tokens,
                do_sample=False,  # greedy: same input -> same answer (repeatable evaluation)
            )
        new_tokens = output[0][inputs["input_ids"].shape[1] :]  # drop the echoed prompt
        return parse_llm_json(str(self._tokenizer.decode(new_tokens, skip_special_tokens=True)))


def create_llm() -> "LLMProvider":
    """Pick the provider from the LLM_PROVIDER setting ("gemini" or "huggingface")."""
    if settings.llm_provider == "huggingface":
        return HuggingFaceProvider()
    return GeminiProvider()


def _safe_message(code: int) -> str:
    """A helpful message that reveals nothing from the provider's response."""
    if code in (401, 403):
        return "The Gemini API key was rejected. Check GEMINI_API_KEY in your .env file."
    if code == 404:
        return "The Gemini model was not found. Check GEMINI_MODEL in your .env file."
    if code == 429:
        return "The Gemini rate limit or quota was reached. Wait a moment and try again."
    return "The language model is unavailable. Try again later."


class FakeProvider:
    """For tests: returns a prepared answer and remembers every prompt it received."""

    def __init__(self, respond: Callable[[str], LlmOutput]) -> None:
        self.model_name = "fake"
        self.respond = respond
        self.prompts: list[str] = []

    def generate(self, system_instruction: str, prompt: str) -> LlmOutput:
        self.prompts.append(prompt)
        return self.respond(prompt)
