"""
Base LLM Provider interface and response models.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional

@dataclass
class LLMResponse:
    """Response from LLM generation."""
    content: str
    model_id: str
    provider_name: str
    thinking_mode_used: bool
    tokens_input: int
    tokens_output: int
    cost_usd: float
    raw_response: dict = None  # Store full API response for debugging

@dataclass
class ModelInfo:
    """Information about an available model."""
    model_id: str
    display_name: str
    supports_thinking: bool
    context_window: int
    cost_per_1m_input: float
    cost_per_1m_output: float
    provider_name: str
    max_output_tokens: int = 4096

class BaseLLMProvider(ABC):
    """Abstract base class for all LLM providers."""
    
    def __init__(self, api_key: str, provider_name: str):
        self.api_key = api_key
        self.provider_name = provider_name
    
    @abstractmethod
    def generate(
        self, 
        prompt: str, 
        system_prompt: str, 
        model_id: str,
        thinking_mode: bool = False,
        thinking_budget: int = 0,
        max_tokens: int = 4096
    ) -> LLMResponse:
        """
        Generate a response from the LLM.
        
        Args:
            prompt: The user prompt/question
            system_prompt: System instructions for the model
            model_id: Specific model to use
            thinking_mode: Whether to enable thinking/reasoning mode
            max_tokens: Maximum tokens to generate
            
        Returns:
            LLMResponse with content and metadata
        """
        pass
    
    @abstractmethod
    def list_models(self) -> List[ModelInfo]:
        """
        Fetch available models from the provider's API.
        
        Returns:
            List of ModelInfo objects
        """
        pass
    
    @abstractmethod
    def validate_api_key(self) -> bool:
        """
        Test if the API key is valid.
        
        Returns:
            True if valid, False otherwise
        """
        pass
    
    def estimate_cost(self, input_tokens: int, output_tokens: int, model_id: str) -> float:
        """
        Estimate cost for a given token usage.
        Override if provider has special pricing logic.
        """
        # This will be implemented by fetching from database
        return 0.0
