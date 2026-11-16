"""Limit actual synchronous model generation, including structured-output calls."""
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from app.runtime import resource_slot


class GenerationCapacity:
    def _generate(self, *args, **kwargs):
        with resource_slot('inference'):
            return super()._generate(*args, **kwargs)


class SlotChatOllama(GenerationCapacity, ChatOllama):
    pass


class SlotChatOpenAI(GenerationCapacity, ChatOpenAI):
    pass


class SlotChatNVIDIA(SlotChatOpenAI):
    def _get_request_payload(self, *args, **kwargs):
        payload = super()._get_request_payload(*args, **kwargs)
        # NVIDIA expects max_tokens; ChatOpenAI renames it for OpenAI.
        if "max_completion_tokens" in payload:
            payload["max_tokens"] = payload.pop("max_completion_tokens")
        return payload
