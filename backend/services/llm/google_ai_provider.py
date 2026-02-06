"""
Google AI Studio (Gemini) Provider Implementation.
"""
import logging

try:
    from google import genai
    from google.genai import types
    NEW_SDK = True
except ImportError:
    import google.generativeai as genai
    NEW_SDK = False

from typing import List
from .base_provider import BaseLLMProvider, LLMResponse, ModelInfo

logger = logging.getLogger(__name__)

class GoogleAIProvider(BaseLLMProvider):
    """Google AI Studio provider for Gemini models."""
    
    def __init__(self, api_key: str):
        super().__init__(api_key, 'google_ai')
        if NEW_SDK:
            self.client = genai.Client(api_key=self.api_key)
        else:
            genai.configure(api_key=self.api_key)
    
    def generate(
        self, 
        prompt: str, 
        system_prompt: str, 
        model_id: str,
        thinking_mode: bool = False,
        thinking_budget: int = 0,
        max_tokens: int = 4096
    ) -> LLMResponse:
        """Generate response using Gemini model."""
        try:
            if NEW_SDK:
                # Determine if model supports thinking
                # Gemini 2.5, 2.0 thinking variants, and 3.x models all support thinking
                is_thinking_model = (
                    'thinking' in model_id.lower() or 
                    'gemini-3' in model_id.lower() or
                    'gemini-2.5' in model_id.lower()
                )
                
                config_args = {
                    "max_output_tokens": max_tokens,
                    "temperature": 0.7
                }

                if thinking_mode and is_thinking_model:
                    # Gemini 2.5 models use thinking_budget (cannot be disabled for Pro)
                    if 'gemini-2.5' in model_id.lower():
                        # Default to dynamic thinking (-1) if no budget specified
                        # Gemini 2.5 Pro: min 128, max 32768 tokens
                        budget = thinking_budget if thinking_budget > 0 else -1  # -1 = dynamic
                        config_args["thinking_config"] = types.ThinkingConfig(
                            thinking_budget=budget,
                            include_thoughts=True
                        )
                    # Gemini 2.0 Flash Thinking uses thinking_config with include_thoughts
                    elif 'gemini-2.0-flash-thinking' in model_id.lower():
                        config_args["thinking_config"] = types.ThinkingConfig(
                            include_thoughts=True
                        )
                        # Map budget to thinking_token_limit if supported (currently experimental)
                        if thinking_budget > 0:
                             config_args["thinking_config"].thinking_token_limit = thinking_budget

                    # Gemini 3 uses thinking_level
                    elif 'gemini-3' in model_id.lower():
                        # Map budget/mode to thinking_level
                        # Valid levels: low, high (dynamic might be default or handled differently)
                        if thinking_budget == 0:
                            thinking_level = "low"
                        elif thinking_budget < 16000:
                            thinking_level = "low" # 'medium' not supported, fallback to low
                        else:
                            thinking_level = "high"
                            
                        config_args["thinking_config"] = types.ThinkingConfig(
                            thinking_level=thinking_level,
                            include_thoughts=True
                        )

                config = types.GenerateContentConfig(**config_args)
                
                # Add system instruction to contents
                contents = prompt
                if system_prompt:
                    contents = f"{system_prompt}\n\n{prompt}"
                
                response = self.client.models.generate_content(
                    model=model_id,
                    contents=contents,
                    config=config
                )
                
                content = response.text
                tokens_input = len(prompt) // 4  # Estimate if usage metadata missing
                tokens_output = len(content) // 4
                
                if hasattr(response, 'usage_metadata'):
                    tokens_input = response.usage_metadata.prompt_token_count
                    tokens_output = response.usage_metadata.candidates_token_count

            else:
                # Use old SDK for Gemini 1.5/2.0/2.5
                import google.generativeai as old_genai
                old_genai.configure(api_key=self.api_key)
                
                model = old_genai.GenerativeModel(
                    model_name=model_id,
                    system_instruction=system_prompt if system_prompt else None
                )
                
                generation_config = old_genai.types.GenerationConfig(
                    max_output_tokens=max_tokens,
                    temperature=0.7,
                )
                
                response = model.generate_content(
                    prompt,
                    generation_config=generation_config
                )
                
                content = response.text
                
                try:
                    tokens_input = response.usage_metadata.prompt_token_count
                    tokens_output = response.usage_metadata.candidates_token_count
                except:
                    tokens_input = len(prompt) // 4
                    tokens_output = len(content) // 4
            
            # Calculate cost (placeholder, real cost in LLMService)
            cost_usd = 0.0
            
            return LLMResponse(
                content=content,
                model_id=model_id,
                provider_name=self.provider_name,
                thinking_mode_used=thinking_mode,
                tokens_input=tokens_input,
                tokens_output=tokens_output,
                cost_usd=cost_usd,
                raw_response=None
            )
            
        except Exception as e:
            raise Exception(f"Google AI generation failed: {str(e)}")
    
    def list_models(self) -> List[ModelInfo]:
        """Fetch available Gemini models from API."""
        try:
            models = []
            for model in genai.list_models():
                # Only include generative models
                if 'generateContent' in model.supported_generation_methods:
                    # Parse model name
                    model_id = model.name.replace('models/', '')
                    
                    # Determine context window
                    context_window = getattr(model, 'input_token_limit', 32000)
                    
                    # Check if model supports thinking mode
                    # All Gemini 2.5+ and 3.x models support native thinking
                    supports_thinking = (
                        'gemini-2.5' in model_id.lower() or 
                        'gemini-3' in model_id.lower() or
                        'gemini-2.0' in model_id.lower()  # Gemini 2.0 also has thinking variants
                    )
                    
                    # Estimate costs (these are approximate, update with actual pricing)
                    if 'pro' in model_id.lower():
                        cost_input = 1.25
                        cost_output = 5.0
                    elif 'flash' in model_id.lower():
                        cost_input = 0.075
                        cost_output = 0.30
                    else:
                        cost_input = 0.5
                        cost_output = 1.5
                    
                    models.append(ModelInfo(
                        model_id=model_id,
                        display_name=model.display_name or model_id,
                        supports_thinking=supports_thinking,
                        context_window=context_window,
                        cost_per_1m_input=cost_input,
                        cost_per_1m_output=cost_output,
                        provider_name=self.provider_name,
                        max_output_tokens=getattr(model, 'output_token_limit', 8192)
                    ))
            
            return models
            
        except Exception as e:
            logger.warning("Error fetching Google AI models: %s", e)
            # Fallback to known models if list fails or returns empty
            return [
                ModelInfo(
                    model_id='gemini-2.0-flash-thinking-exp-01-21',
                    display_name='Gemini 2.0 Flash Thinking',
                    supports_thinking=True,
                    context_window=1000000,
                    cost_per_1m_input=0.0, # Free in preview
                    cost_per_1m_output=0.0,
                    provider_name=self.provider_name,
                    max_output_tokens=65536
                ),
                ModelInfo(
                    model_id='gemini-2.0-flash-exp',
                    display_name='Gemini 2.0 Flash',
                    supports_thinking=False,
                    context_window=1000000,
                    cost_per_1m_input=0.0,
                    cost_per_1m_output=0.0,
                    provider_name=self.provider_name,
                    max_output_tokens=8192
                )
            ]
    
    def validate_api_key(self) -> bool:
        """Test if the Google AI API key is valid."""
        try:
            # Try to list models as a validation check
            list(genai.list_models())
            return True
        except Exception as e:
            logger.warning("Google AI API key validation failed: %s", e)
            return False
