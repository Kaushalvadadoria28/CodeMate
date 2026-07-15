from google import genai
from google.genai.types import Part, UserContent, ModelContent, GenerateContentConfig
from google.genai import errors

from fastapi import HTTPException
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log
)
import logging

logger = logging.getLogger(__name__)

# retry on these two error types only
def _is_retryable(exception):
    if isinstance(exception, errors.ServerError):
        return exception.code in (503, 429)
    return False

class LLMService:
    def __init__(self, api_key: str, model_name: str):
        self.client = genai.Client(api_key=api_key)
        self.model_name = model_name if "2.5" in model_name else "gemini-2.5-flash"

    async def generate_response(
        self,
        query: str,
        context_code: list[dict],
        conversation_history: list[dict],
        context_map: str = ""
    ):
        system_instruction = """You are an expert coding assistant with deep knowledge of multiple programming languages.
        You have access to the user's codebase and must provide accurate answers based on the provided context.
        
        When answering:
        1. Reference specific files and line numbers from the context.
        2. Provide complete code examples when helpful.
        3. Explain complex concepts clearly.
        4. If the answer is not in the context, say so, but try to help based on general knowledge if safe.
        """

        context_str = self._format_context(context_code)

        # about cross-file relationships instead of guessing from code text alone.
        context_map_block = (
            f"\n\nCODE CONTEXT MAP (actual import/call relationships extracted via "
            f"static analysis — treat this as ground truth for cross-file impact, "
            f"do not infer connections that aren't listed here):\n{context_map}"
            if context_map else ""
        )
        
        full_system_prompt = f"{system_instruction}\n\nCODE CONTEXT:\n{context_str}"

        history = []
        for msg in conversation_history:
            if msg["role"] == "user":
                history.append(UserContent(parts=[Part(text=msg["content"])]))
            else:
                history.append(ModelContent(parts=[Part(text=msg["content"])]))

        chat = self.client.aio.chats.create(
            model=self.model_name,
            history=history,
            config=GenerateContentConfig(
                system_instruction=full_system_prompt,
                temperature=0.7,
            )
        )

        return await self._send_with_retry(chat, query)

    @retry(
        retry=retry_if_exception_type(errors.ServerError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True
    )
    async def _send_with_retry(self, chat, query: str):
        try:
            response = await chat.send_message(query)
            return response.text

        except errors.ServerError as e:
            if e.code in (503, 429):
                logger.warning(f"Gemini returned {e.code}, will retry...")
                raise   # re-raise so tenacity catches and retries
            # non-retryable server error — fail immediately
            raise HTTPException(
                status_code=500,
                detail=f"Google API Server Error: {str(e)}"
            )

        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Unexpected error communicating with AI: {str(e)}"
            )

    def _format_context(self, context_code: list[dict]) -> str:
        formatted = []
        for chunk in context_code:
            formatted.append(
                f"\nFile: {chunk.get('filename')}\n"
                f"Location: {chunk.get('location')}\n"
                f"```{chunk.get('language', 'text')}\n{chunk.get('code_text')}\n```\n"
            )
        return "\n".join(formatted)

    async def generate_document(self, prompt: str) -> str:
        return await self._send_document_with_retry(prompt)

    @retry(
        retry=retry_if_exception_type(errors.ServerError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True
    )
    async def _send_document_with_retry(self, prompt: str):
        try:
            response = await self.client.aio.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=GenerateContentConfig(temperature=0.3),
            )
            return response.text

        except errors.ServerError as e:
            if e.code in (503, 429):
                logger.warning(f"Gemini returned {e.code}, will retry...")
                raise
            raise HTTPException(
                status_code=500,
                detail=f"Google API Server Error: {str(e)}"
            )

        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Unexpected error communicating with AI: {str(e)}"
            )