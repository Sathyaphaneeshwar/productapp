"""
Anthropic (Claude) Provider Implementation.
Supports Claude 3 Opus, Sonnet, and Haiku models.
"""
from anthropic import Anthropic
from typing import List
from .base_provider import BaseLLMProvider, LLMResponse, ModelInfo

class AnthropicProvider(BaseLLMProvider):
    """Anthropic provider for Claude models."""
    
    def __init__(self, api_key: str):
        super().__init__(api_key, 'anthropic')
        self.client = Anthropic(api_key=self.api_key)
    
    def generate(
        self, 
        prompt: str, 
        system_prompt: str, 
        model_id: str,
        thinking_mode: bool = False,
        max_tokens: int = 4096
    ) -> LLMResponse:
        """Generate response using Claude model."""
        try:
            # Add thinking mode instructions if enabled
            if thinking_mode:
                enhanced_prompt = f"""Before providing your final analysis, think through the problem systematically:

<thinking>
1. Identify key financial metrics and data points
2. Analyze trends, patterns, and anomalies
3. Consider industry context and market conditions
4. Formulate evidence-based conclusions
</thinking>

Then provide your comprehensive analysis.

{prompt}"""
            else:
                enhanced_prompt = prompt
            
            # Generate completion
            response = self.client.messages.create(
                model=model_id,
                max_tokens=max_tokens,
                system=system_prompt if system_prompt else "",
                messages=[
                    {"role": "user", "content": enhanced_prompt}
                ],
                temperature=0.7
            )
            
            # Extract content
            content = response.content[0].text
            
            # Get token usage
            tokens_input = response.usage.input_tokens
            tokens_output = response.usage.output_tokens
            
            # Calculate cost (will be fetched from DB in production)
            # Approximate costs for Claude models
            if 'opus' in model_id.lower():
                cost_input = 15.0
                cost_output = 75.0
            elif 'sonnet' in model_id.lower():
                cost_input = 3.0
                cost_output = 15.0
            else:  # haiku
                cost_input = 0.25
                cost_output = 1.25
            
            cost_usd = (tokens_input / 1_000_000 * cost_input) + (tokens_output / 1_000_000 * cost_output)
            
            return LLMResponse(
                content=content,
                model_id=model_id,
                provider_name=self.provider_name,
                thinking_mode_used=thinking_mode,
                tokens_input=tokens_input,
                tokens_output=tokens_output,
                cost_usd=cost_usd,
                raw_response=response.model_dump()
            )
            
        except Exception as e:
            raise Exception(f"Anthropic generation failed: {str(e)}")
    
    def list_models(self) -> List[ModelInfo]:
        """
        Fetch available Claude models.
        Note: Anthropic doesn't have a list models API endpoint,
        so we return a curated list of known models.
        """
        # Known Claude models as of 2024/2025
        known_models = [
            {
                'model_id': 'claude-3-opus-20240229',
                'display_name': 'Claude 3 Opus',
                'supports_thinking': False,
                'context_window': 200000,
                'cost_input': 15.0,
                'cost_output': 75.0
            },
            {
                'model_id': 'claude-3-5-sonnet-20241022',
                'display_name': 'Claude 3.5 Sonnet',
                'supports_thinking': False,
                'context_window': 200000,
                'cost_input': 3.0,
                'cost_output': 15.0
            },
            {
                'model_id': 'claude-3-5-haiku-20241022',
                'display_name': 'Claude 3.5 Haiku',
                'supports_thinking': False,
                'context_window': 200000,
                'cost_input': 0.25,
                'cost_output': 1.25
            },
            {
                'model_id': 'claude-3-haiku-20240307',
                'display_name': 'Claude 3 Haiku',
                'supports_thinking': False,
                'context_window': 200000,
                'cost_input': 0.25,
                'cost_output': 1.25
            }
        ]
        
        models = []
        for model_data in known_models:
            models.append(ModelInfo(
                model_id=model_data['model_id'],
                display_name=model_data['display_name'],
                supports_thinking=model_data['supports_thinking'],
                context_window=model_data['context_window'],
                cost_per_1m_input=model_data['cost_input'],
                cost_per_1m_output=model_data['cost_output'],
                provider_name=self.provider_name,
                max_output_tokens=8192  # Claude 3.5 Sonnet supports 8192
            ))
        
        return models
    
    def validate_api_key(self) -> bool:
        """Test if the Anthropic API key is valid."""
        try:
            # Try a minimal message creation as validation
            self.client.messages.create(
                model="claude-3-haiku-20240307",
                max_tokens=10,
                messages=[{"role": "user", "content": "Hi"}]
            )
            return True
        except Exception as e:
            print(f"Anthropic API key validation failed: {e}")
            return False
