"""
Encryption utility for API keys using Fernet symmetric encryption.
"""
import logging
import os
from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

class EncryptionService:
    def __init__(self):
        # Path to store encryption key persistently
        self.key_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            '.encryption_key'
        )
        
        # Try to get encryption key from environment first
        self.encryption_key = os.getenv('ENCRYPTION_KEY')
        
        if not self.encryption_key:
            # Try to load from persistent file
            if os.path.exists(self.key_file):
                try:
                    with open(self.key_file, 'r') as f:
                        self.encryption_key = f.read().strip()
                    logger.info("Loaded encryption key from %s", self.key_file)
                except Exception as e:
                    logger.warning("Failed to load encryption key from file: %s", e)
        
        if not self.encryption_key:
            # Generate a new key if not found (for first-time setup)
            self.encryption_key = Fernet.generate_key().decode()
            logger.info("Generated new encryption key")
            
            # Save to persistent file
            try:
                with open(self.key_file, 'w') as f:
                    f.write(self.encryption_key)
                # Set file permissions to read/write for owner only
                os.chmod(self.key_file, 0o600)
                logger.info("Saved encryption key to %s", self.key_file)
            except Exception as e:
                logger.warning("Failed to save encryption key: %s", e)
                logger.warning("Please set ENCRYPTION_KEY in environment or .env file")
        
        self.cipher = Fernet(self.encryption_key.encode() if isinstance(self.encryption_key, str) else self.encryption_key)
    
    def encrypt(self, plaintext: str) -> str:
        """Encrypt a plaintext string and return base64 encoded ciphertext."""
        if not plaintext:
            return None
        encrypted_bytes = self.cipher.encrypt(plaintext.encode())
        return encrypted_bytes.decode()
    
    def decrypt(self, ciphertext: str) -> str:
        """Decrypt a base64 encoded ciphertext and return plaintext string."""
        if not ciphertext:
            return None
        decrypted_bytes = self.cipher.decrypt(ciphertext.encode())
        return decrypted_bytes.decode()
    
    def get_encryption_key(self) -> str:
        """Return the current encryption key (for backup purposes)."""
        return self.encryption_key

# Singleton instance
_encryption_service = None

def get_encryption_service() -> EncryptionService:
    """Get or create the encryption service singleton."""
    global _encryption_service
    if _encryption_service is None:
        _encryption_service = EncryptionService()
    return _encryption_service
