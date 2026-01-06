"""
OpenAI Provider Implementation.
Supports GPT-5, GPT-4, GPT-3.5, and o1 thinking models.
"""
from openai import OpenAI
from typing import List
import tiktoken
from .base_provider import BaseLLMProvider, LLMResponse, ModelInfo

class OpenAIProvider(BaseLLMProvider):
    """OpenAI provider for GPT models."""
    
    def __init__(self, api_key: str):
        super().__init__(api_key, 'openai')
        self.client = OpenAI(api_key=self.api_key)
    
    def generate(
        self, 
        prompt: str, 
        system_prompt: str, 
        model_id: str,
        thinking_mode: bool = False,
        thinking_budget: int = 0,
        max_tokens: int = 4096
    ) -> LLMResponse:
        """Generate response using OpenAI model."""
        try:
            # Check model type
            is_gpt5 = 'gpt-5' in model_id.lower()
            is_o1_model = model_id.startswith('o1')
            
            if is_gpt5:
                # GPT-5 uses developer role and reasoning_effort
                messages = []
                if system_prompt:
                    messages.append({"role": "developer", "content": system_prompt})
                messages.append({"role": "user", "content": prompt})
                
                # Set reasoning effort
                reasoning_config = None
                if thinking_mode:
                    effort = "low" if thinking_budget == 0 else "high"
                    if thinking_budget > 0 and thinking_budget < 20000:
                        effort = "medium"
                    reasoning_config = {"effort": effort}
                
                response = self.client.responses.create(
                    model=model_id,
                    input=messages, # Map messages to input
                    reasoning=reasoning_config
                )
                
                content = response.output_text
                tokens_input = len(prompt) // 4 # Estimate
                tokens_output = len(content) // 4
                actual_thinking_mode = thinking_mode
                
            elif is_o1_model:
                # o1 models: use max_completion_tokens, no system role (use user)
                messages = []
                if system_prompt:
                    # o1 supports developer role in newer versions
                    messages.append({"role": "developer", "content": system_prompt})
                messages.append({"role": "user", "content": prompt})
                
                response = self.client.chat.completions.create(
                    model=model_id,
                    messages=messages,
                    max_completion_tokens=max_tokens
                )
                
                content = response.choices[0].message.content
                tokens_input = response.usage.prompt_tokens
                tokens_output = response.usage.completion_tokens
                actual_thinking_mode = True
                
            else:
                # Standard GPT-4/3.5 models
                messages = []
                if system_prompt:
                    messages.append({"role": "system", "content": system_prompt})
                
                # Add thinking mode instructions if enabled (manual prompting)
                if thinking_mode:
                    enhanced_prompt = f"""Think through this step-by-step before providing your analysis:
1. Identify key financial metrics and data points
2. Analyze trends, patterns, and anomalies  
3. Consider industry context and market conditions
4. Formulate evidence-based conclusions

Then provide your comprehensive analysis.

{prompt}"""
                else:
                    enhanced_prompt = prompt
                
                messages.append({"role": "user", "content": enhanced_prompt})
                actual_thinking_mode = thinking_mode
                
                response = self.client.chat.completions.create(
                    model=model_id,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=0.7
                )
                
                content = response.choices[0].message.content
                tokens_input = response.usage.prompt_tokens
                tokens_output = response.usage.completion_tokens
            
            # Calculate cost (placeholder)
            cost_usd = 0.0
            
            return LLMResponse(
                content=content,
                model_id=model_id,
                provider_name=self.provider_name,
                thinking_mode_used=actual_thinking_mode,
                tokens_input=tokens_input,
                tokens_output=tokens_output,
                cost_usd=cost_usd,
                raw_response=None
            )
            
        except Exception as e:
            raise Exception(f"OpenAI generation failed: {str(e)}")
    
    def list_models(self) -> List[ModelInfo]:
        """Fetch available OpenAI models from API."""
        try:
            models = []
            response = self.client.models.list()
            
            for model in response.data:
                model_id = model.id
                
                # Only include chat/completion models
                if any(x in model_id for x in ['gpt', 'o1']):
                    # Determine if it's a thinking model
                    supports_thinking = model_id.startswith('o1')
                    
                    # Estimate context window
                    if 'gpt-5' in model_id:
                        context_window = 200000
                    elif 'o1' in model_id:
                        context_window = 128000
                    elif 'gpt-4' in model_id:
                        context_window = 128000
                    else:
                        context_window = 16385
                    
                    # Estimate costs
                    if 'gpt-5' in model_id:
                        cost_input, cost_output = 2.5, 10.0
                    elif 'o1' in model_id:
                        cost_input, cost_output = 15.0, 60.0
                    elif 'gpt-4' in model_id:
                        cost_input, cost_output = 5.0, 15.0
                    else:
                        cost_input, cost_output = 0.5, 1.5
                    
                    # Create display name
                    display_name = model_id.upper().replace('-', ' ').title()
                    
                    # Determine max output tokens
                    if 'o1' in model.id:
                        max_output_tokens = 32768 # o1 models support high output
                    elif 'gpt-4' in model.id:
                        max_output_tokens = 4096
                    else:
                        max_output_tokens = 4096

                    models.append(ModelInfo(
                        model_id=model_id,
                        display_name=display_name,
                        supports_thinking=supports_thinking,
                        context_window=context_window,
                        cost_per_1m_input=cost_input,
                        cost_per_1m_output=cost_output,
                        provider_name=self.provider_name,
                        max_output_tokens=max_output_tokens
                    ))
            
            return models
            
            return models
            
        except Exception as e:
            print(f"Error fetching OpenAI models: {e}")
            # Fallback list if API fails
            return [
                ModelInfo(
                    model_id='gpt-5',
                    display_name='GPT-5',
                    supports_thinking=True,
                    context_window=200000,
                    cost_per_1m_input=2.5,
                    cost_per_1m_output=10.0,
                    provider_name=self.provider_name,
                    max_output_tokens=16384
                ),
                ModelInfo(
                    model_id='o1',
                    display_name='O1',
                    supports_thinking=True,
                    context_window=128000,
                    cost_per_1m_input=15.0,
                    cost_per_1m_output=60.0,
                    provider_name=self.provider_name,
                    max_output_tokens=32768
                )
            ]
    
    def validate_api_key(self) -> bool:
        """Test if the OpenAI API key is valid."""
        try:
            # Try to list models as a validation check
            self.client.models.list()
            return True
        except Exception as e:
            print(f"OpenAI API key validation failed: {e}")
            return False
