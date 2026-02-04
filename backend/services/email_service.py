import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import sqlite3
import os
import sys
import ssl
import html
from typing import Optional, Dict, Any
from datetime import datetime

# Import certifi for SSL certificates in bundled apps
try:
    import certifi
    SSL_CERT_FILE = certifi.where()
except ImportError:
    SSL_CERT_FILE = None
from datetime import datetime

# Prefer vendored dependencies (installed via pip --target) before falling back to system
VENDOR_PATH = os.path.join(os.path.dirname(__file__), '..', 'vendor')
if VENDOR_PATH not in sys.path:
    sys.path.insert(0, VENDOR_PATH)

import markdown

# Add parent directory to path
from config import DATABASE_PATH
from db import get_db_connection

class EmailService:
    def __init__(self):
        self.db_path = str(DATABASE_PATH)

    def get_db_connection(self):
        return get_db_connection(self.db_path)

    def get_active_smtp_config(self) -> Optional[Dict[str, Any]]:
        """Get the active SMTP configuration from database"""
        conn = self.get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM smtp_settings WHERE is_active = 1 ORDER BY created_at DESC LIMIT 1")
            config = cursor.fetchone()
            return dict(config) if config else None
        finally:
            conn.close()

    def test_connection(self, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Test SMTP connection with provided or stored credentials"""
        smtp_config = config
        if not smtp_config:
            smtp_config = self.get_active_smtp_config()
            if not smtp_config:
                raise ValueError("No active SMTP configuration found")
        
        try:
            # Try to connect to SMTP server with timeout
            server = smtplib.SMTP(smtp_config['smtp_server'], int(smtp_config['smtp_port']), timeout=30)
            server.ehlo()
            
            # Use starttls with SSL context - use certifi for CA certs in bundled apps
            context = ssl.create_default_context()
            if SSL_CERT_FILE:
                context.load_verify_locations(SSL_CERT_FILE)
            server.starttls(context=context)
            server.ehlo()
            
            server.login(smtp_config['email'], smtp_config['app_password'])
            server.quit()
            
            return {
                'status': 'success',
                'message': 'SMTP connection successful',
                'server': smtp_config['smtp_server'],
                'port': smtp_config['smtp_port']
            }
            
        except smtplib.SMTPAuthenticationError as e:
            raise ValueError(f'Authentication failed. Check email and app password. Details: {str(e)}')
        except smtplib.SMTPException as e:
            raise Exception(f'SMTP error: {str(e)}')
        except Exception as e:
            raise Exception(f'Connection error: {str(e)}')

    def send_email(self, to_email: str, subject: str, body: str, is_html: bool = False) -> bool:
        """Send email using active SMTP configuration"""
        # Get active SMTP config
        smtp_config = self.get_active_smtp_config()
        if not smtp_config:
            raise ValueError("No active SMTP configuration found")
        
        try:
            # Create message
            msg = MIMEMultipart()
            msg['From'] = smtp_config['email']
            msg['To'] = to_email
            msg['Subject'] = subject
            
            # Add body
            msg.attach(MIMEText(body, 'html' if is_html else 'plain'))
            
            # Send email with timeout and SSL context - use certifi for CA certs
            server = smtplib.SMTP(smtp_config['smtp_server'], int(smtp_config['smtp_port']), timeout=30)
            server.ehlo()
            context = ssl.create_default_context()
            if SSL_CERT_FILE:
                context.load_verify_locations(SSL_CERT_FILE)
            server.starttls(context=context)
            server.ehlo()
            server.login(smtp_config['email'], smtp_config['app_password'])
            server.send_message(msg)
            server.quit()
            
            return True
            
        except smtplib.SMTPAuthenticationError as e:
            raise Exception(f"SMTP authentication failed: {str(e)}")
        except Exception as e:
            raise Exception(f"Failed to send email: {str(e)}")

    def get_active_email_list(self) -> list[str]:
        """Get list of active email addresses to send reports to"""
        conn = self.get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT email FROM email_list WHERE is_active = 1")
            return [row['email'] for row in cursor.fetchall()]
        finally:
            conn.close()

    def _normalize_markdown(self, text: str) -> str:
        """
        Clean up common LLM Markdown quirks so tables render in HTML emails.
        - Strip leading whitespace on pipe-table rows.
        - Ensure blank lines surround table blocks for python-markdown parsing.
        """
        cleaned_lines = []
        in_table = False
        for line in (text or "").splitlines():
            stripped = line.lstrip()
            is_table_row = stripped.startswith("|") and stripped.count("|") >= 2

            if is_table_row and not in_table:
                if cleaned_lines and cleaned_lines[-1].strip():
                    cleaned_lines.append("")
                in_table = True
            elif not is_table_row and in_table:
                if cleaned_lines and cleaned_lines[-1].strip():
                    cleaned_lines.append("")
                in_table = False

            cleaned_lines.append(stripped if is_table_row else line)

        return "\n".join(cleaned_lines)
    
    def render_template(self, template_name: str, variables: Dict[str, str]) -> str:
        """Render email template with variables"""
        # Handle PyInstaller frozen app vs development
        if getattr(sys, 'frozen', False):
            # Running as PyInstaller bundle - templates are in _MEIPASS/templates
            base_path = sys._MEIPASS
        else:
            # Running in development - templates are in ../templates relative to this file
            base_path = os.path.join(os.path.dirname(__file__), '..')
        
        template_path = os.path.join(base_path, 'templates', template_name)
        
        try:
            with open(template_path, 'r') as f:
                template = f.read()
            
            # Replace variables
            for key, value in variables.items():
                template = template.replace(f"{{{{{key}}}}}", str(value))
            
            return template
        except FileNotFoundError:
            raise FileNotFoundError(f"Email template not found: {template_name} (looked at: {template_path})")
    
    def send_analysis_email(self, to_email: str, stock_symbol: str, stock_name: str, 
                           quarter: str, year: int, analysis_content: str, 
                           model_provider: str, model_name: str = None, 
                           transcript_url: str = None) -> bool:
        """Send analysis email using template"""
        try:
            # Convert Markdown (including tables/lists) to HTML; fall back to escaped text on failure
            md_extensions = ['extra', 'tables', 'sane_lists', 'nl2br']
            try:
                normalized = self._normalize_markdown(analysis_content or "")
                analysis_html = markdown.markdown(normalized, extensions=md_extensions)
            except Exception:
                escaped = html.escape(analysis_content or "")
                escaped_with_br = escaped.replace('\n', '<br>')
                analysis_html = f"<p>{escaped_with_br}</p>"
            
            # Use model_name if provided, otherwise use provider name
            display_model = model_name if model_name else model_provider.upper()
            
            # Render template
            html_body = self.render_template('email_analysis_report.html', {
                'STOCK_SYMBOL': stock_symbol,
                'STOCK_NAME': stock_name,
                'QUARTER': quarter,
                'YEAR': str(year),
                'ANALYSIS_CONTENT': analysis_html,
                'MODEL_PROVIDER': model_provider.upper(),
                'MODEL_NAME': display_model,
                'TRANSCRIPT_URL': transcript_url if transcript_url else '#',
                'GENERATED_DATE': datetime.now().strftime('%B %d, %Y at %I:%M %p')
            })
            
            subject = f"📊 Analysis Report: {stock_symbol} - {quarter} {year}"
            
            return self.send_email(
                to_email=to_email,
                subject=subject,
                body=html_body,
                is_html=True
            )
        except Exception as e:
            raise Exception(f"Failed to send analysis email: {str(e)}")

    def send_document_research_email(
        self,
        to_email: str,
        stock_symbol: str,
        stock_name: str,
        years: list[int],
        analysis_content: str,
        model_provider: str,
        model_name: str = None
    ) -> bool:
        """Send annual report research email."""
        try:
            md_extensions = ['extra', 'tables', 'sane_lists', 'nl2br']
            try:
                normalized = self._normalize_markdown(analysis_content or "")
                analysis_html = markdown.markdown(normalized, extensions=md_extensions)
            except Exception:
                escaped = html.escape(analysis_content or "")
                escaped_with_br = escaped.replace('\n', '<br>')
                analysis_html = f"<p>{escaped_with_br}</p>"

            years_str = ", ".join(str(y) for y in sorted(years, reverse=True))
            display_model = model_name if model_name else model_provider.upper()
            generated_date = datetime.now().strftime('%B %d, %Y at %I:%M %p')

            html_body = self.render_template('email_document_research.html', {
                'STOCK_SYMBOL': stock_symbol,
                'STOCK_NAME': stock_name,
                'YEARS': years_str,
                'ANALYSIS_CONTENT': analysis_html,
                'MODEL_PROVIDER': model_provider.upper(),
                'MODEL_NAME': display_model,
                'GENERATED_DATE': generated_date
            })

            subject = f"📄 Annual Report Research: {stock_symbol} ({years_str})"

            return self.send_email(
                to_email=to_email,
                subject=subject,
                body=html_body,
                is_html=True
            )
        except Exception as e:
            raise Exception(f"Failed to send document research email: {str(e)}")
