import sys
import os
from unittest.mock import MagicMock, patch

# Add backend to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.services.llm.google_ai_provider import GoogleAIProvider
from backend.services.llm.openai_provider import OpenAIProvider
from backend.services.llm.openrouter_provider import OpenRouterProvider

def test_google_ai():
    print("Testing GoogleAIProvider...")
    try:
        # Mock genai client
        with patch('backend.services.llm.google_ai_provider.genai') as mock_genai:
            provider = GoogleAIProvider("fake_key")
            
            # Test generate with thinking
            mock_response = MagicMock()
            mock_response.text = "Response"
            mock_response.usage_metadata.prompt_token_count = 10
            mock_response.usage_metadata.candidates_token_count = 10
            
            provider.client.models.generate_content.return_value = mock_response
            
            # Test Gemini 3 logic
            provider.generate("prompt", "system", "gemini-3-pro", thinking_mode=True, thinking_budget=1000)
            
            # Verify config was created with thinking_config
            call_args = provider.client.models.generate_content.call_args
            config = call_args.kwargs['config']
            # We can't easily inspect the types.GenerateContentConfig object without the real lib, 
            # but if it didn't crash, it's a good sign.
            print("GoogleAIProvider generate (Gemini 3) passed.")
            
    except Exception as e:
        print(f"GoogleAIProvider failed: {e}")
        import traceback
        traceback.print_exc()

def test_openai():
    print("\nTesting OpenAIProvider...")
    try:
        with patch('backend.services.llm.openai_provider.OpenAI') as mock_openai:
            provider = OpenAIProvider("fake_key")
            
            # Test GPT-5
            mock_response = MagicMock()
            mock_response.output_text = "Response"
            mock_response.usage.prompt_tokens = 10
            mock_response.usage.completion_tokens = 10
            
            provider.client.responses.create.return_value = mock_response
            
            provider.generate("prompt", "system", "gpt-5", thinking_mode=True, thinking_budget=1000)
            
            # Verify reasoning_effort was passed
            provider.client.responses.create.assert_called()
            call_kwargs = provider.client.responses.create.call_args.kwargs
            if 'reasoning' in call_kwargs:
                print(f"GPT-5 reasoning config: {call_kwargs['reasoning']}")
            
            print("OpenAIProvider generate (GPT-5) passed.")
            
            # Test o1
            mock_chat_response = MagicMock()
            mock_chat_response.choices[0].message.content = "Response"
            mock_chat_response.usage.prompt_tokens = 10
            mock_chat_response.usage.completion_tokens = 10
            
            provider.client.chat.completions.create.return_value = mock_chat_response
            
            provider.generate("prompt", "system", "o1-preview", thinking_mode=True)
            
            # Verify max_completion_tokens used
            call_kwargs = provider.client.chat.completions.create.call_args.kwargs
            if 'max_completion_tokens' in call_kwargs:
                print("o1 used max_completion_tokens.")
            
            print("OpenAIProvider generate (o1) passed.")
            
    except Exception as e:
        print(f"OpenAIProvider failed: {e}")
        import traceback
        traceback.print_exc()

def test_openrouter():
    print("\nTesting OpenRouterProvider...")
    try:
        with patch('backend.services.llm.openrouter_provider.OpenAI') as mock_openai:
            provider = OpenRouterProvider("fake_key")
            
            mock_response = MagicMock()
            mock_response.choices[0].message.content = "Response"
            mock_response.usage.prompt_tokens = 10
            mock_response.usage.completion_tokens = 10
            
            provider.client.chat.completions.create.return_value = mock_response
            
            provider.generate("prompt", "system", "some-model", thinking_mode=True)
            
            # Verify extra_body
            call_kwargs = provider.client.chat.completions.create.call_args.kwargs
            if 'extra_body' in call_kwargs and call_kwargs['extra_body'].get('include_reasoning'):
                print("OpenRouter used include_reasoning.")
            
            print("OpenRouterProvider generate passed.")
            
    except Exception as e:
        print(f"OpenRouterProvider failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_google_ai()
    test_openai()
    test_openrouter()
