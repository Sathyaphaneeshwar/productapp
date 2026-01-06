"""
LLM Service - Main service for managing LLM providers and models.
"""
import sqlite3
from typing import List, Optional
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from scripts.config import DATABASE_PATH
from backend.services.llm.base_provider import BaseLLMProvider, LLMResponse, ModelInfo
from backend.services.llm.google_ai_provider import GoogleAIProvider
from backend.services.llm.openai_provider import OpenAIProvider
from backend.services.llm.anthropic_provider import AnthropicProvider
from backend.services.llm.openrouter_provider import OpenRouterProvider

class LLMService:
    """Main service for LLM operations."""
    
    def __init__(self):
        self._provider_cache = {}
    
    def get_db_connection(self):
        conn = sqlite3.connect(str(DATABASE_PATH))
        conn.row_factory = sqlite3.Row
        return conn
    
    def _get_provider(self, provider_name: str) -> Optional[BaseLLMProvider]:
        """Get or create a provider instance."""
        if provider_name in self._provider_cache:
            return self._provider_cache[provider_name]
        
        # Fetch API key from database
        conn = self.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT api_key FROM llm_providers 
            WHERE provider_name = ? AND is_active = 1
        """, (provider_name,))
        
        result = cursor.fetchone()
        conn.close()
        
        if not result or not result['api_key']:
            return None
        
        # Get API key (plain text, no decryption needed)
        api_key = result['api_key']
        
        # Create provider instance
        if provider_name == 'google_ai':
            provider = GoogleAIProvider(api_key)
        elif provider_name == 'openai':
            provider = OpenAIProvider(api_key)
        elif provider_name == 'anthropic':
            provider = AnthropicProvider(api_key)
        elif provider_name == 'openrouter':
            provider = OpenRouterProvider(api_key)
        else:
            return None
        
        self._provider_cache[provider_name] = provider
        return provider
    
    def generate(
        self,
        prompt: str,
        system_prompt: str,
        model_id: Optional[int] = None,
        thinking_mode: bool = False,
        thinking_budget: int = 0,
        max_tokens: int = 12000
    ) -> LLMResponse:
        """
        Generate a response using the specified or default model.
        
        Args:
            prompt: User prompt
            system_prompt: System instructions
            model_id: Database ID of model to use (None = use default)
            thinking_mode: Enable thinking mode
            max_tokens: Maximum tokens to generate
            
        Returns:
            LLMResponse with content and metadata
        """
        conn = self.get_db_connection()
        cursor = conn.cursor()
        
        # Get model info
        if model_id is None:
            # Use default model
            cursor.execute("""
                SELECT setting_value FROM llm_settings 
                WHERE setting_key = 'default_model_id'
            """)
            result = cursor.fetchone()
            if not result:
                raise Exception("No default model configured")
            model_id = int(result['setting_value'])
        
        # Fetch model details
        cursor.execute("""
            SELECT m.model_id, m.supports_thinking, m.cost_per_1m_input, m.cost_per_1m_output,
                   m.user_max_tokens, m.user_thinking_enabled, m.user_thinking_budget, m.max_output_tokens,
                   p.provider_name
            FROM llm_models m
            JOIN llm_providers p ON m.provider_id = p.id
            WHERE m.id = ? AND m.is_active = 1
        """, (model_id,))
        
        model_row = cursor.fetchone()
        conn.close()
        
        if not model_row:
            raise Exception(f"Model {model_id} not found or inactive")
        
        # Get provider
        provider = self._get_provider(model_row['provider_name'])
        if not provider:
            raise Exception(f"Provider {model_row['provider_name']} not configured")
        
        # Use user config if available, otherwise defaults
        # Logic: If thinking_mode is explicitly True in call, use it.
        # Otherwise, use user preference from DB.
        # If DB value is None, fallback to False.
        db_thinking_enabled = model_row['user_thinking_enabled'] == 1
        effective_thinking_mode = thinking_mode or db_thinking_enabled
        
        effective_thinking_budget = model_row['user_thinking_budget'] if model_row['user_thinking_budget'] is not None else 0
        # If user provided an override, honor it. Otherwise, cap at the lesser of requested max_tokens and model's max_output_tokens.
        if model_row['user_max_tokens'] is not None:
            effective_max_tokens = model_row['user_max_tokens']
        else:
            model_cap = model_row['max_output_tokens'] if model_row['max_output_tokens'] is not None else max_tokens
            effective_max_tokens = min(model_cap, max_tokens)
        
        # Generate response
        response = provider.generate(
            prompt=prompt,
            system_prompt=system_prompt,
            model_id=model_row['model_id'],
            thinking_mode=effective_thinking_mode,
            thinking_budget=effective_thinking_budget,
            max_tokens=effective_max_tokens
        )
        
        # Update cost with database pricing
        response.cost_usd = (
            response.tokens_input / 1_000_000 * model_row['cost_per_1m_input']
        ) + (
            response.tokens_output / 1_000_000 * model_row['cost_per_1m_output']
        )
        
        return response
    
    def sync_models(self, provider_name: str) -> int:
        """
        Sync models from provider API to database.
        
        Returns:
            Number of models synced
        """
        provider = self._get_provider(provider_name)
        if not provider:
            raise Exception(f"Provider {provider_name} not configured")
        
        # Fetch models from API
        models = provider.list_models()
        
        # Update database
        conn = self.get_db_connection()
        cursor = conn.cursor()
        
        # Get provider ID
        cursor.execute("SELECT id FROM llm_providers WHERE provider_name = ?", (provider_name,))
        provider_row = cursor.fetchone()
        if not provider_row:
            conn.close()
            raise Exception(f"Provider {provider_name} not found in database")
        
        provider_id = provider_row['id']
        synced_count = 0
        
        for model in models:
            cursor.execute("""
                INSERT INTO llm_models 
                (provider_id, model_id, display_name, supports_thinking, context_window, 
                 cost_per_1m_input, cost_per_1m_output, max_output_tokens, last_synced,
                 user_max_tokens, user_thinking_enabled, user_thinking_budget)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?, ?)
                ON CONFLICT(provider_id, model_id) DO UPDATE SET
                display_name=excluded.display_name,
                supports_thinking=excluded.supports_thinking,
                context_window=excluded.context_window,
                cost_per_1m_input=excluded.cost_per_1m_input,
                cost_per_1m_output=excluded.cost_per_1m_output,
                max_output_tokens=excluded.max_output_tokens,
                last_synced=CURRENT_TIMESTAMP
            """, (
                provider_id,
                model.model_id,
                model.display_name,
                model.supports_thinking,
                model.context_window,
                model.cost_per_1m_input,
                model.cost_per_1m_output,
                model.max_output_tokens,
                # Set defaults if new record
                model.max_output_tokens, # user_max_tokens default
                # Default thinking to enabled only for o1 models where it's mandatory/core
                1 if (model.supports_thinking and 'o1' in model.model_id.lower()) else 0, # user_thinking_enabled default
                model.max_output_tokens if model.supports_thinking else 0 # user_thinking_budget default
            ))
            synced_count += 1
        
        conn.commit()
        conn.close()
        
        return synced_count
    
    def get_available_models(self, provider_name: Optional[str] = None) -> List[dict]:
        """Get all available models, optionally filtered by provider."""
        conn = self.get_db_connection()
        cursor = conn.cursor()
        
        if provider_name:
            cursor.execute("""
                SELECT m.id, m.model_id, m.display_name, m.supports_thinking, 
                       m.context_window, m.cost_per_1m_input, m.cost_per_1m_output,
                       m.user_max_tokens, m.user_thinking_enabled, m.user_thinking_budget, m.max_output_tokens,
                       p.provider_name, p.display_name as provider_display_name
                FROM llm_models m
                JOIN llm_providers p ON m.provider_id = p.id
                WHERE p.provider_name = ? AND m.is_active = 1
                ORDER BY m.display_name
            """, (provider_name,))
        else:
            cursor.execute("""
                SELECT m.id, m.model_id, m.display_name, m.supports_thinking,
                       m.context_window, m.cost_per_1m_input, m.cost_per_1m_output,
                       m.user_max_tokens, m.user_thinking_enabled, m.user_thinking_budget, m.max_output_tokens,
                       p.provider_name, p.display_name as provider_display_name
                FROM llm_models m
                JOIN llm_providers p ON m.provider_id = p.id
                WHERE m.is_active = 1
                ORDER BY p.display_name, m.display_name
            """)
        
        models = [dict(row) for row in cursor.fetchall()]
        conn.close()
        
        return models
    
    def set_api_key(self, provider_name: str, api_key: str) -> bool:
        """Set API key for a provider (plain text)."""
        conn = self.get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE llm_providers 
            SET api_key = ?, updated_at = CURRENT_TIMESTAMP
            WHERE provider_name = ?
        """, (api_key, provider_name))
        
        conn.commit()
        conn.close()
        
        # Clear cache
        if provider_name in self._provider_cache:
            del self._provider_cache[provider_name]
        
        return True

    def update_model_config(self, model_id: int, config: dict) -> bool:
        """
        Update user configuration for a model.
        
        Args:
            model_id: Database ID of the model
            config: Dictionary containing 'user_max_tokens', 'user_thinking_enabled', 'user_thinking_budget'
            
        Returns:
            True if successful
        """
        conn = self.get_db_connection()
        cursor = conn.cursor()
        
        try:
            # Validate model exists
            cursor.execute("SELECT id FROM llm_models WHERE id = ?", (model_id,))
            if not cursor.fetchone():
                raise Exception("Model not found")
            
            # Build update query
            fields = []
            values = []
            
            if 'user_max_tokens' in config:
                fields.append("user_max_tokens = ?")
                values.append(config['user_max_tokens'])
                
            if 'user_thinking_enabled' in config:
                fields.append("user_thinking_enabled = ?")
                values.append(config['user_thinking_enabled'])
                
            if 'user_thinking_budget' in config:
                fields.append("user_thinking_budget = ?")
                values.append(config['user_thinking_budget'])
                
            if not fields:
                return True
                
            values.append(model_id)
            query = f"UPDATE llm_models SET {', '.join(fields)} WHERE id = ?"
            
            cursor.execute(query, tuple(values))
            conn.commit()
            return True
            
        except Exception as e:
            raise e
        finally:
            conn.close()
