"""
OpenRouter Provider Implementation.
Provides access to 400+ models from multiple providers.
"""
import logging
from openai import OpenAI
from typing import List
import requests
from .base_provider import BaseLLMProvider, LLMResponse, ModelInfo

logger = logging.getLogger(__name__)

class OpenRouterProvider(BaseLLMProvider):
    """OpenRouter provider for accessing 400+ models."""
    
    def __init__(self, api_key: str):
        super().__init__(api_key, 'openrouter')
        self.client = OpenAI(
            base_url='https://openrouter.ai/api/v1',
            api_key=self.api_key
        )
        self.base_url = 'https://openrouter.ai/api/v1'
    
    def generate(
        self, 
        prompt: str, 
        system_prompt: str, 
        model_id: str,
        thinking_mode: bool = False,
        thinking_budget: int = 0,
        max_tokens: int = 4096
    ) -> LLMResponse:
        """Generate response using any OpenRouter model."""
        try:
            # Check if it's an o1 model (no system prompt support)
            is_o1_model = 'o1' in model_id.lower()
            
            if is_o1_model:
                combined_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
                messages = [{"role": "user", "content": combined_prompt}]
                actual_thinking_mode = True
            else:
                messages = []
                if system_prompt:
                    messages.append({"role": "system", "content": system_prompt})
                
                if thinking_mode:
                    enhanced_prompt = f"""Think step-by-step before providing your analysis:
1. Identify key metrics
2. Analyze patterns
3. Consider context
4. Draw conclusions

{prompt}"""
                else:
                    enhanced_prompt = prompt
                
                messages.append({"role": "user", "content": enhanced_prompt})
                actual_thinking_mode = thinking_mode
            
            # Generate completion
            extra_body = {}
            if thinking_mode:
                extra_body['include_reasoning'] = True
                # Some OpenRouter providers might support reasoning_effort or similar
                # We can pass it if we know the model supports it, but for now include_reasoning is key
            
            response = self.client.chat.completions.create(
                model=model_id,
                messages=messages,
                max_tokens=max_tokens if not is_o1_model else None,
                temperature=0.7 if not is_o1_model else 1.0,
                extra_body=extra_body
            )
            
            # Extract content
            content = response.choices[0].message.content
            
            # Get token usage
            tokens_input = response.usage.prompt_tokens if response.usage else 0
            tokens_output = response.usage.completion_tokens if response.usage else 0
            
            # Cost will be calculated from database pricing
            cost_usd = 0.0  # Will be updated by LLMService
            
            return LLMResponse(
                content=content,
                model_id=model_id,
                provider_name=self.provider_name,
                thinking_mode_used=actual_thinking_mode,
                tokens_input=tokens_input,
                tokens_output=tokens_output,
                cost_usd=cost_usd,
                raw_response=response.model_dump()
            )
            
        except Exception as e:
            raise Exception(f"OpenRouter generation failed: {str(e)}")
    
    def list_models(self) -> List[ModelInfo]:
        """Fetch all available models from OpenRouter API."""
        try:
            response = requests.get(
                f"{self.base_url}/models",
                headers={'Authorization': f'Bearer {self.api_key}'}
            )
            response.raise_for_status()
            data = response.json()
            
            models = []
            for model_data in data.get('data', []):
                model_id = model_data.get('id')
                
                # Determine if it supports thinking
                # OpenRouter doesn't explicitly flag thinking models always, but we can check ID or pricing
                # Some models like o1, gemini-thinking, deepseek-r1 have specific markers
                supports_thinking = (
                    'o1' in model_id.lower() or 
                    'thinking' in model_id.lower() or 
                    'reasoning' in model_id.lower() or
                    'deepseek-r1' in model_id.lower()
                )
                
                # Get context window
                context_window = model_data.get('context_length', 32000)
                
                # Get pricing (OpenRouter provides this)
                pricing = model_data.get('pricing', {})
                cost_input = float(pricing.get('prompt', '0')) * 1000  # Convert to per 1M
                cost_output = float(pricing.get('completion', '0')) * 1000
                
                # Get display name
                display_name = model_data.get('name', model_id)
                
                models.append(ModelInfo(
                    model_id=model_id,
                    display_name=display_name,
                    supports_thinking=supports_thinking,
                    context_window=context_window,
                    cost_per_1m_input=cost_input,
                    cost_per_1m_output=cost_output,
                    provider_name=self.provider_name,
                    max_output_tokens=model_data.get('top_provider', {}).get('max_completion_tokens', 4096) or 4096
                ))
            
            return models
            
        except Exception as e:
            logger.warning("Error fetching OpenRouter models: %s", e)
            return []
    
    def validate_api_key(self) -> bool:
        """Test if the OpenRouter API key is valid."""
        try:
            # Try to fetch models as validation
            response = requests.get(
                f"{self.base_url}/models",
                headers={'Authorization': f'Bearer {self.api_key}'}
            )
            return response.status_code == 200
        except Exception as e:
            logger.warning("OpenRouter API key validation failed: %s", e)
            return False
