from google import genai
from google.genai.types import Part, UserContent, ModelContent, GenerateContentConfig

import os
from google.genai import errors  # <-- Make sure this is imported at the top of your file
from fastapi import HTTPException  # <-- Make sure this is imported at the top of your file

class LLMService:
    """
    Handles interactions with Google Gemini using the new google-genai SDK.
    """
    
    def __init__(self, api_key: str, model_name: str):
        self.client = genai.Client(api_key=api_key)
        # Upgraded default models (e.g., gemini-2.5-flash) are recommended with the new SDK
        self.model_name = model_name if "2.5" in model_name else "gemini-2.5-flash"
        
    async def generate_response(
        self, 
        query: str, 
        context_code: list[dict],
        conversation_history: list[dict]
    ):
        """
        Generates a response using Gemini, incorporating RAG context and history.
        """
        
        # 1. Prepare System/Context Prompt
        system_instruction = """You are an expert coding assistant with deep knowledge of multiple programming languages.
        You have access to the user's codebase and must provide accurate answers based on the provided context.
        
        When answering:
        1. Reference specific files and line numbers from the context.
        2. Provide complete code examples when helpful.
        3. Explain complex concepts clearly.
        4. If the answer is not in the context, say so, but try to help based on general knowledge if safe.
        """
        
        context_str = self._format_context(context_code)
        full_system_prompt = f"{system_instruction}\n\nCODE CONTEXT:\n{context_str}"

        # 2. Convert History to Gemini Format
        history = []
        for msg in conversation_history:
            if msg["role"] == "user":
                history.append(UserContent(parts=[Part(text=msg["content"])]))
            else:
                history.append(ModelContent(parts=[Part(text=msg["content"])]))

        # 3. Initialize Async Chat Session
        chat = self.client.aio.chats.create(
            model=self.model_name,
            history=history,
            config=GenerateContentConfig(
                system_instruction=full_system_prompt,
                temperature=0.7,
            )
        )
        
        # 4. Send Message with Error Handling
        try:
            response = await chat.send_message(query)
            return response.text
            
        except errors.ServerError as e:
            # Check if it's the 503 high demand error
            if e.code == 503:
                raise HTTPException(
                    status_code=503, 
                    detail="The AI model is experiencing a high volume of requests right now. Please try again in a few moments."
                )
            # Catch any other 5xx errors from Google's servers
            raise HTTPException(
                status_code=500, 
                detail=f"Google API Server Error: {str(e)}"
            )
            
        except Exception as e:
            # Fallback for unexpected connection errors, timeouts, etc.
            raise HTTPException(
                status_code=500, 
                detail=f"An unexpected error occurred while communicating with the AI: {str(e)}"
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


# import google.generativeai as genai
# from google.generativeai.types import HarmCategory, HarmBlockThreshold

# class LLMService:
#     """
#     Handles interactions with Google Gemini API.
#     """
    
#     def __init__(self, api_key: str, model_name: str):
#         genai.configure(api_key=api_key)
#         self.model = genai.GenerativeModel(model_name)
        
#     async def generate_response(
#         self, 
#         query: str, 
#         context_code: list[dict],
#         conversation_history: list[dict]
#     ):
#         """
#         Generates a response using Gemini, incorporating RAG context and history.
#         """
        
#         # 1. Prepare System/Context Prompt
#         system_instruction = """You are an expert coding assistant with deep knowledge of multiple programming languages.
#         You have access to the user's codebase and must provide accurate answers based on the provided context.
        
#         When answering:
#         1. Reference specific files and line numbers from the context.
#         2. Provide complete code examples when helpful.
#         3. Explain complex concepts clearly.
#         4. If the answer is not in the context, say so, but try to help based on general knowledge if safe.
#         """
        
#         context_str = self._format_context(context_code)
#         full_system_prompt = f"{system_instruction}\n\nCODE CONTEXT:\n{context_str}"

#         # 2. Convert History to Gemini Format
#         # Gemini expects history as a list of content objects or a chat session.
#         # Since we are stateless per request, we reconstruct the chat history.
#         chat_history = []
        
#         # Add system prompt as the first "user" message or setup 
#         # (Gemini API handles system instructions differently in v1beta, 
#         # but for stability we can prepend it to the history or first message).
        
#         for msg in conversation_history:
#             role = "user" if msg["role"] == "user" else "model"
#             chat_history.append({
#                 "role": role,
#                 "parts": [msg["content"]]
#             })

#         # Initialize Chat Session
#         chat = self.model.start_chat(history=chat_history)
        
#         # 3. Send Message
#         # We combine the system prompt with the user's latest query for immediate context
#         final_prompt = f"{full_system_prompt}\n\nUSER QUERY:\n{query}"
        
#         response = chat.send_message(
#             final_prompt,
#             safety_settings={
#                 HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
#                 HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
#                 HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
#                 HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
#             }
#         )
        
#         return response.text

#     def _format_context(self, context_code: list[dict]) -> str:
#         formatted = []
#         for chunk in context_code:
#             formatted.append(
#                 f"\nFile: {chunk.get('filename')}\n"
#                 f"Location: {chunk.get('location')}\n"
#                 f"```{chunk.get('language', 'text')}\n{chunk.get('code_text')}\n```\n"
#             )
#         return "\n".join(formatted)