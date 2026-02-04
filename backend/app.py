from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import sqlite3
import os
import sys
import smtplib
import html
import markdown
from io import BytesIO
from xhtml2pdf import pisa
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from config import DATABASE_PATH
from db import get_db_connection as _get_db_connection
from services.scheduler_service import SchedulerService
from services.prompt_service import PromptService
from services.group_research_service import GroupResearchService
from services.document_research_service import DocumentResearchService

app = Flask(__name__)
CORS(app)

# Initialize and start the background scheduler
scheduler = SchedulerService(poll_interval_seconds=300)  # Poll every 5 minutes
if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or os.environ.get("WERKZEUG_RUN_MAIN") is None:
    scheduler.start()
prompt_service = PromptService()
group_research_service = GroupResearchService()
document_research_service = DocumentResearchService()

DB_PATH = str(DATABASE_PATH)

def get_db_connection():
    return _get_db_connection(DB_PATH)

@app.route('/api/poll/status', methods=['GET'])
def get_poll_status():
    try:
        return jsonify(scheduler.get_poll_status())
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/poll/trigger', methods=['POST'])
def trigger_poll():
    try:
        started = scheduler.trigger_poll()
        if started:
            return jsonify({'message': 'Poll started', 'started': True}), 202
        return jsonify({'message': 'Poll already running', 'started': False}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def get_current_fy_quarter():
    """
    Returns (quarter, fiscal_year) based on current date.
    Indian FY: Q1=Apr-Jun, Q2=Jul-Sep, Q3=Oct-Dec, Q4=Jan-Mar
    """
    from datetime import datetime
    now = datetime.now()
    month = now.month
    year = now.year
    
    if 4 <= month <= 6:
        return "Q1", year + 1
    elif 7 <= month <= 9:
        return "Q2", year + 1
    elif 10 <= month <= 12:
        return "Q3", year + 1
    else:  # 1-3 (Jan, Feb, Mar)
        return "Q4", year

def get_previous_fy_quarter():
    """
    Returns (quarter, year) for the previous quarter.
    This is what's currently being released (earnings are released after quarter ends).
    """
    current_q, current_fy = get_current_fy_quarter()
    if current_q == "Q1":
        return "Q4", current_fy - 1
    elif current_q == "Q2":
        return "Q1", current_fy
    elif current_q == "Q3":
        return "Q2", current_fy
    else:  # Q4
        return "Q3", current_fy

def get_available_quarters(count=8):
    """
    Returns list of quarters for dropdown, going back from previous quarter.
    """
    quarters = []
    q, fy = get_previous_fy_quarter()
    quarter_order = ["Q4", "Q3", "Q2", "Q1"]
    
    for _ in range(count):
        month_range = {"Q1": "Apr-Jun", "Q2": "Jul-Sep", "Q3": "Oct-Dec", "Q4": "Jan-Mar"}[q]
        quarters.append({
            "quarter": q,
            "year": fy,
            "label": f"{q} FY{str(fy)[-2:]} ({month_range})"
        })
        # Move to previous quarter
        idx = quarter_order.index(q)
        if idx == 3:  # Was Q1, go to Q4 of previous year
            q = "Q4"
            fy -= 1
        else:
            q = quarter_order[idx + 1]
    
    return quarters

@app.route('/api/quarters', methods=['GET'])
def get_quarters():
    """Returns list of quarters for dropdown."""
    quarters = get_available_quarters()
    return jsonify(quarters)

@app.route('/api/stocks', methods=['GET'])
def search_stocks():
    query = request.args.get('q', '')
    if not query:
        return jsonify([])

    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Search by NSE symbol, BSE code, or name, limit to 10 results
    # Use COALESCE to return NSE symbol if available, otherwise BSE code
    search_term = f"%{query}%"
    cursor.execute("""
        SELECT id, COALESCE(stock_symbol, bse_code) as symbol, stock_name as name 
        FROM stocks 
        WHERE stock_symbol LIKE ? OR bse_code LIKE ? OR stock_name LIKE ? 
        ORDER BY 
            CASE 
                WHEN stock_symbol = ? THEN 1 
                WHEN bse_code = ? THEN 2
                WHEN stock_symbol LIKE ? THEN 3 
                WHEN bse_code LIKE ? THEN 4
                WHEN stock_name LIKE ? THEN 5 
                ELSE 6 
            END,
            COALESCE(stock_symbol, bse_code) ASC
        LIMIT 10
    """, (search_term, search_term, search_term, query, query, f"{query}%", f"{query}%", f"{query}%"))
    
    stocks = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    # Add default status for now (since it's not in DB yet)
    for stock in stocks:
        stock['status'] = 'not-ready'
        
    return jsonify(stocks)

@app.route('/api/watchlist', methods=['GET'])
def get_watchlist():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Accept quarter/year from query params, default to previous quarter
    quarter = request.args.get('quarter')
    year = request.args.get('year', type=int)
    
    if not quarter or not year:
        quarter, year = get_previous_fy_quarter()
    
    cursor.execute("""
        SELECT 
            s.id,
            COALESCE(s.stock_symbol, s.bse_code) as symbol, 
            s.stock_name as name,
            w.added_at,
            tc.status as transcript_check_status
        FROM stocks s 
        JOIN watchlist_items w ON s.id = w.stock_id 
        LEFT JOIN transcript_checks tc ON tc.stock_id = s.id
        ORDER BY w.added_at DESC
    """)
    
    stocks = []
    for row in cursor.fetchall():
        stock_id = row['id']
        
        # Get transcript info for SELECTED QUARTER only
        cursor.execute("""
            SELECT 
                id,
                quarter, 
                year, 
                status, 
                event_date,
                source_url,
                created_at,
                analysis_status,
                analysis_error
            FROM transcripts 
            WHERE stock_id = ? AND quarter = ? AND year = ?
            LIMIT 1
        """, (stock_id, quarter, year))
        
        transcript = cursor.fetchone()
        
        # Get latest analysis info
        analysis_info = None
        if transcript:
            cursor.execute("""
                SELECT 
                    id,
                    created_at,
                    model_provider
                FROM transcript_analyses 
                WHERE transcript_id = ? 
                ORDER BY created_at DESC 
                LIMIT 1
            """, (transcript['id'],))
            
            analysis = cursor.fetchone()
            if analysis:
                analysis_info = {
                    'completed': True,
                    'date': analysis['created_at'],
                    'provider': analysis['model_provider']
                }
        
        # Determine detailed status
        status_info = {
            'status': 'no_transcript',
            'message': 'No transcript available',
            'details': None
        }
        
        if transcript:
            analysis_state = transcript['analysis_status']
            analysis_error = transcript['analysis_error']

            if analysis_state == 'in_progress':
                status_info = {
                    'status': 'analyzing',
                    'message': 'Analyzing transcript...',
                    'details': {
                        'quarter': transcript['quarter'],
                        'year': transcript['year']
                    }
                }
            elif row['transcript_check_status'] == 'checking':
                status_info = {
                    'status': 'fetching',
                    'message': 'Fetching transcript...',
                    'details': {
                        'quarter': transcript['quarter'],
                        'year': transcript['year']
                    }
                }
            elif transcript['status'] == 'upcoming':
                status_info = {
                    'status': 'upcoming',
                    'message': f"Upcoming: {transcript['event_date']}",
                    'details': {
                        'quarter': transcript['quarter'],
                        'year': transcript['year'],
                        'event_date': transcript['event_date']
                    }
                }
            elif transcript['status'] == 'available':
                if analysis_state == 'error' and not analysis_info:
                    status_info = {
                        'status': 'analysis_failed',
                        'message': 'Analysis failed',
                        'details': {
                            'quarter': transcript['quarter'],
                            'year': transcript['year'],
                            'analysis_error': analysis_error
                        }
                    }
                elif analysis_info:
                    status_info = {
                        'status': 'analyzed',
                        'message': f"Analysis Complete ({transcript['quarter']} {transcript['year']})",
                        'details': {
                            'quarter': transcript['quarter'],
                            'year': transcript['year'],
                            'analyzed_at': analysis_info['date'],
                            'provider': analysis_info['provider']
                        }
                    }
                else:
                    status_info = {
                        'status': 'transcript_ready',
                        'message': f"Transcript Available ({transcript['quarter']} {transcript['year']})",
                        'details': {
                            'quarter': transcript['quarter'],
                            'year': transcript['year'],
                            'transcript_date': transcript['created_at']
                        }
                    }
        elif row['transcript_check_status'] == 'checking':
            status_info = {
                'status': 'fetching',
                'message': 'Fetching transcript...',
                'details': None
            }
        
        stocks.append({
            'id': stock_id,
            'symbol': row['symbol'],
            'name': row['name'],
            'added_at': row['added_at'],
            'status': status_info['status'],
            'status_message': status_info['message'],
            'status_details': status_info['details']
        })
    
    conn.close()
    return jsonify(stocks)

@app.route('/api/watchlist', methods=['POST'])
def add_to_watchlist():
    data = request.json
    symbol = data.get('symbol')
    
    if not symbol:
        return jsonify({'error': 'Symbol is required'}), 400
        
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Get stock ID - check both NSE symbol and BSE code
        cursor.execute("SELECT id FROM stocks WHERE stock_symbol = ? OR bse_code = ?", (symbol, symbol))
        stock = cursor.fetchone()
        
        if not stock:
            return jsonify({'error': 'Stock not found'}), 404
            
        # Add to watchlist
        cursor.execute("INSERT INTO watchlist_items (stock_id) VALUES (?)", (stock['id'],))
        conn.commit()

        # Kick off immediate transcript check instead of waiting for the next poll
        scheduler.trigger_check_for_stock(stock['id'])
        return jsonify({'message': 'Added to watchlist'}), 201
        
    except sqlite3.IntegrityError:
        return jsonify({'message': 'Already in watchlist'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@app.route('/api/watchlist/<symbol>', methods=['DELETE'])
def remove_from_watchlist(symbol):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Get stock ID first - check both NSE symbol and BSE code
        cursor.execute("SELECT id FROM stocks WHERE stock_symbol = ? OR bse_code = ?", (symbol, symbol))
        stock = cursor.fetchone()
        
        if stock:
            cursor.execute("DELETE FROM watchlist_items WHERE stock_id = ?", (stock['id'],))
            conn.commit()
            
        return jsonify({'message': 'Removed from watchlist'}), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

# Groups API Endpoints

@app.route('/api/groups', methods=['GET'])
def get_groups():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM groups ORDER BY created_at DESC")
    groups = [dict(row) for row in cursor.fetchall()]
    
    # Get stock counts for each group
    for group in groups:
        cursor.execute("SELECT COUNT(*) FROM group_stocks WHERE group_id = ?", (group['id'],))
        group['stock_count'] = cursor.fetchone()[0]
        
    conn.close()
    return jsonify(groups)

@app.route('/api/groups', methods=['POST'])
def create_group():
    data = request.json
    name = data.get('name')
    
    if not name:
        return jsonify({'error': 'Group name is required'}), 400
        
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Check for duplicate name (case-insensitive)
        cursor.execute("SELECT id FROM groups WHERE LOWER(name) = LOWER(?)", (name.strip(),))
        if cursor.fetchone():
            return jsonify({'error': 'A group with this name already exists'}), 409
            
        cursor.execute("""
            INSERT INTO groups (name, deep_research_prompt, stock_summary_prompt, is_active)
            VALUES (?, ?, ?, ?)
        """, (name.strip(), data.get('deep_research_prompt'), data.get('stock_summary_prompt'), data.get('is_active', True)))
        conn.commit()
        return jsonify({'message': 'Group created', 'id': cursor.lastrowid}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@app.route('/api/groups/<int:group_id>', methods=['PATCH'])
def update_group(group_id):
    data = request.json
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Check if group exists
        cursor.execute("SELECT id FROM groups WHERE id = ?", (group_id,))
        if not cursor.fetchone():
            return jsonify({'error': 'Group not found'}), 404
            
        # Build update query dynamically
        fields = []
        values = []
        
        if 'name' in data:
            # Check for duplicate name (case-insensitive), excluding current group
            new_name = data['name'].strip()
            cursor.execute(
                "SELECT id FROM groups WHERE LOWER(name) = LOWER(?) AND id != ?", 
                (new_name, group_id)
            )
            if cursor.fetchone():
                return jsonify({'error': 'A group with this name already exists'}), 409
            fields.append("name = ?")
            values.append(new_name)
            
        if 'deep_research_prompt' in data:
            fields.append("deep_research_prompt = ?")
            values.append(data['deep_research_prompt'])
            
        if 'stock_summary_prompt' in data:
            fields.append("stock_summary_prompt = ?")
            values.append(data['stock_summary_prompt'])
            
        if 'is_active' in data:
            fields.append("is_active = ?")
            values.append(data['is_active'])
            
        if not fields:
            return jsonify({'message': 'No changes provided'}), 200
            
        values.append(group_id)
        query = f"UPDATE groups SET {', '.join(fields)} WHERE id = ?"
        
        cursor.execute(query, tuple(values))
        conn.commit()
        
        return jsonify({'message': 'Group updated'}), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@app.route('/api/groups/<int:group_id>', methods=['DELETE'])
def delete_group(group_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Check if group exists
        cursor.execute("SELECT id FROM groups WHERE id = ?", (group_id,))
        if not cursor.fetchone():
            return jsonify({'error': 'Group not found'}), 404
            
        # Delete group (cascade will handle group_stocks)
        cursor.execute("DELETE FROM groups WHERE id = ?", (group_id,))
        conn.commit()
        
        return jsonify({'message': 'Group deleted'}), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@app.route('/api/groups/<int:group_id>', methods=['GET'])
def get_group_details(group_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Accept quarter/year from query params, default to previous quarter
    quarter = request.args.get('quarter')
    year = request.args.get('year', type=int)
    
    if not quarter or not year:
        quarter, year = get_previous_fy_quarter()
    
    # Get group info
    cursor.execute("SELECT * FROM groups WHERE id = ?", (group_id,))
    group = cursor.fetchone()
    
    if not group:
        conn.close()
        return jsonify({'error': 'Group not found'}), 404
        
    group_data = dict(group)
    
    # Add selected quarter info for frontend
    group_data['selected_quarter'] = quarter
    group_data['selected_year'] = year
    
    # Get stocks in group with transcript status for SELECTED QUARTER
    cursor.execute("""
        SELECT 
            s.id,
            COALESCE(s.stock_symbol, s.bse_code) as symbol, 
            s.stock_name as name, 
            gs.added_at,
            t.quarter,
            t.year,
            t.status as transcript_status,
            t.created_at as transcript_created_at
        FROM stocks s
        JOIN group_stocks gs ON s.id = gs.stock_id
        LEFT JOIN transcripts t ON t.stock_id = s.id 
            AND t.quarter = ? AND t.year = ?
        WHERE gs.group_id = ?
        ORDER BY gs.added_at DESC
    """, (quarter, year, group_id))
    
    group_data['stocks'] = [dict(row) for row in cursor.fetchall()]

    # Add transcript completion counts for selected quarter
    try:
        cursor.execute("""
            SELECT 
                COUNT(*) AS total_stocks,
                SUM(CASE WHEN t.status = 'available' THEN 1 ELSE 0 END) AS transcripts_ready
            FROM group_stocks gs
            LEFT JOIN transcripts t 
                ON t.stock_id = gs.stock_id
                AND t.quarter = ? AND t.year = ?
            WHERE gs.group_id = ?
        """, (quarter, year, group_id))
        counts = cursor.fetchone()
        group_data['transcripts_ready'] = counts['transcripts_ready'] if counts['transcripts_ready'] else 0
        group_data['transcripts_total'] = counts['total_stocks'] if counts else 0
    except Exception:
        group_data['transcripts_ready'] = 0
        group_data['transcripts_total'] = 0
    conn.close()
    
    return jsonify(group_data)

@app.route('/api/groups/<int:group_id>/stocks', methods=['POST'])
def add_stock_to_group(group_id):
    data = request.json
    symbol = data.get('symbol')
    
    if not symbol:
        return jsonify({'error': 'Symbol is required'}), 400
        
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Get stock ID - check both NSE symbol and BSE code
        cursor.execute("SELECT id FROM stocks WHERE stock_symbol = ? OR bse_code = ?", (symbol, symbol))
        stock = cursor.fetchone()
        
        if not stock:
            return jsonify({'error': 'Stock not found'}), 404
            
        # Add to group
        cursor.execute("""
            INSERT INTO group_stocks (group_id, stock_id)
            VALUES (?, ?)
        """, (group_id, stock['id']))
        conn.commit()

        # Immediately check for transcripts for newly grouped stock
        scheduler.trigger_check_for_stock(stock['id'])
        return jsonify({'message': 'Stock added to group'}), 201
        
    except sqlite3.IntegrityError:
        return jsonify({'message': 'Stock already in group'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@app.route('/api/groups/<int:group_id>/stocks/<symbol>', methods=['DELETE'])
def remove_stock_from_group(group_id, symbol):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Get stock ID - check both NSE symbol and BSE code
        cursor.execute("SELECT id FROM stocks WHERE stock_symbol = ? OR bse_code = ?", (symbol, symbol))
        stock = cursor.fetchone()
        
        if stock:
            cursor.execute("""
                DELETE FROM group_stocks 
                WHERE group_id = ? AND stock_id = ?
            """, (group_id, stock['id']))
            conn.commit()
            
        return jsonify({'message': 'Stock removed from group'}), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@app.route('/api/groups/<int:group_id>/articles', methods=['GET'])
def list_group_articles(group_id):
    """List deep-research group runs (one per quarter)."""
    try:
        runs = group_research_service.list_runs(group_id)
        return jsonify(runs), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/groups/<int:group_id>/articles/<int:run_id>', methods=['GET'])
def get_group_article(group_id, run_id):
    """Get a specific deep-research run for a group (includes content)."""
    try:
        run = group_research_service.get_run(run_id)
        if not run or run.get('group_id') != group_id:
            return jsonify({'error': 'Article not found'}), 404
        return jsonify(run), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/groups/<int:group_id>/articles', methods=['POST'])
def force_group_article(group_id):
    """Force-generate a group deep research run even if not all transcripts are present."""
    data = request.json or {}
    quarter = data.get('quarter')
    year = data.get('year')
    raw_allow_partial = data.get('allow_partial', True)
    if isinstance(raw_allow_partial, str):
        allow_partial = raw_allow_partial.lower() != 'false'
    else:
        allow_partial = bool(raw_allow_partial)

    if not quarter or not year:
        return jsonify({'error': 'quarter and year are required'}), 400

    try:
        run_id, included, missing = group_research_service.force_run(
            group_id, quarter, int(year), allow_partial
        )
        if run_id is None:
            if missing and not allow_partial:
                return jsonify({
                    'error': 'Missing transcripts for some stocks',
                    'missing_symbols': missing
                }), 400
            return jsonify({'error': 'Unable to create run (group not found or no stocks)'}), 400
        return jsonify({
            'message': 'Run started',
            'run_id': run_id,
            'included_symbols': included,
            'missing_symbols': missing,
            'allow_partial': bool(allow_partial)
        }), 202
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Email List API Endpoints

@app.route('/api/emails', methods=['GET'])
def get_emails():
    active_filter = request.args.get('active')
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if active_filter is not None:
        is_active = 1 if active_filter.lower() == 'true' else 0
        cursor.execute("SELECT * FROM email_list WHERE is_active = ? ORDER BY created_at DESC", (is_active,))
    else:
        cursor.execute("SELECT * FROM email_list ORDER BY created_at DESC")
    
    emails = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(emails)

@app.route('/api/emails/<int:email_id>', methods=['GET'])
def get_email(email_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM email_list WHERE id = ?", (email_id,))
    email = cursor.fetchone()
    conn.close()
    
    if not email:
        return jsonify({'error': 'Email not found'}), 404
        
    return jsonify(dict(email))

@app.route('/api/emails', methods=['POST'])
def add_email():
    data = request.json
    email = data.get('email')
    name = data.get('name')
    
    if not email or not name:
        return jsonify({'error': 'Email and name are required'}), 400
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            INSERT INTO email_list (email, name, is_active)
            VALUES (?, ?, ?)
        """, (email, name, data.get('is_active', True)))
        conn.commit()
        return jsonify({'message': 'Email added', 'id': cursor.lastrowid}), 201
    except sqlite3.IntegrityError:
        return jsonify({'error': 'Email already exists'}), 409
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@app.route('/api/emails/<int:email_id>', methods=['PATCH'])
def update_email(email_id):
    data = request.json
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Check if email exists
        cursor.execute("SELECT id FROM email_list WHERE id = ?", (email_id,))
        if not cursor.fetchone():
            return jsonify({'error': 'Email not found'}), 404
            
        # Build update query dynamically
        fields = []
        values = []
        
        if 'email' in data:
            fields.append("email = ?")
            values.append(data['email'])
            
        if 'name' in data:
            fields.append("name = ?")
            values.append(data['name'])
            
        if 'is_active' in data:
            fields.append("is_active = ?")
            values.append(data['is_active'])
            
        if not fields:
            return jsonify({'message': 'No changes provided'}), 200
            
        values.append(email_id)
        query = f"UPDATE email_list SET {', '.join(fields)} WHERE id = ?"
        
        cursor.execute(query, tuple(values))
        conn.commit()
        
        return jsonify({'message': 'Email updated'}), 200
        
    except sqlite3.IntegrityError:
        return jsonify({'error': 'Email already exists'}), 409
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@app.route('/api/emails/<int:email_id>', methods=['DELETE'])
def delete_email(email_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Check if email exists
        cursor.execute("SELECT id FROM email_list WHERE id = ?", (email_id,))
        if not cursor.fetchone():
            return jsonify({'error': 'Email not found'}), 404
            
        cursor.execute("DELETE FROM email_list WHERE id = ?", (email_id,))
        conn.commit()
        
        return jsonify({'message': 'Email deleted'}), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

# SMTP Settings API Endpoints

@app.route('/api/smtp-settings', methods=['GET'])
def get_smtp_settings():
    active_filter = request.args.get('active')
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if active_filter is not None:
        is_active = 1 if active_filter.lower() == 'true' else 0
        cursor.execute("SELECT * FROM smtp_settings WHERE is_active = ? ORDER BY created_at DESC", (is_active,))
    else:
        cursor.execute("SELECT * FROM smtp_settings ORDER BY created_at DESC")
    
    settings = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(settings)

@app.route('/api/smtp-settings/<int:setting_id>', methods=['GET'])
def get_smtp_setting(setting_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM smtp_settings WHERE id = ?", (setting_id,))
    setting = cursor.fetchone()
    conn.close()
    
    if not setting:
        return jsonify({'error': 'SMTP setting not found'}), 404
        
    return jsonify(dict(setting))

@app.route('/api/smtp-settings', methods=['POST'])
def add_smtp_setting():
    data = request.json
    email = data.get('email')
    app_password = data.get('app_password')
    
    if not email or not app_password:
        return jsonify({'error': 'Email and app password are required'}), 400
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # If this new setting is active, deactivate all others
        if data.get('is_active', True):
            cursor.execute("UPDATE smtp_settings SET is_active = 0")
            
        cursor.execute("""
            INSERT INTO smtp_settings (email, app_password, smtp_server, smtp_port, is_active)
            VALUES (?, ?, ?, ?, ?)
        """, (
            email, 
            app_password, 
            data.get('smtp_server', 'smtp.gmail.com'),
            data.get('smtp_port', 587),
            data.get('is_active', True)
        ))
        conn.commit()
        return jsonify({'message': 'SMTP setting added', 'id': cursor.lastrowid}), 201
    except sqlite3.IntegrityError:
        # If email exists, try to update it instead
        try:
            if data.get('is_active', True):
                cursor.execute("UPDATE smtp_settings SET is_active = 0")
                
            cursor.execute("""
                UPDATE smtp_settings 
                SET app_password = ?, smtp_server = ?, smtp_port = ?, is_active = ?
                WHERE email = ?
            """, (
                app_password,
                data.get('smtp_server', 'smtp.gmail.com'),
                data.get('smtp_port', 587),
                data.get('is_active', True),
                email
            ))
            conn.commit()
            return jsonify({'message': 'SMTP setting updated'}), 200
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@app.route('/api/smtp-settings/<int:setting_id>', methods=['PATCH'])
def update_smtp_setting(setting_id):
    data = request.json
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Check if setting exists
        cursor.execute("SELECT id FROM smtp_settings WHERE id = ?", (setting_id,))
        if not cursor.fetchone():
            return jsonify({'error': 'SMTP setting not found'}), 404
            
        # If setting is being made active, deactivate others
        if data.get('is_active'):
            cursor.execute("UPDATE smtp_settings SET is_active = 0")
            
        # Build update query dynamically
        fields = []
        values = []
        
        if 'email' in data:
            fields.append("email = ?")
            values.append(data['email'])
            
        if 'app_password' in data:
            fields.append("app_password = ?")
            values.append(data['app_password'])
            
        if 'smtp_server' in data:
            fields.append("smtp_server = ?")
            values.append(data['smtp_server'])
            
        if 'smtp_port' in data:
            fields.append("smtp_port = ?")
            values.append(data['smtp_port'])
            
        if 'is_active' in data:
            fields.append("is_active = ?")
            values.append(data['is_active'])
            
        if not fields:
            return jsonify({'message': 'No changes provided'}), 200
            
        values.append(setting_id)
        query = f"UPDATE smtp_settings SET {', '.join(fields)} WHERE id = ?"
        
        cursor.execute(query, tuple(values))
        conn.commit()
        
        return jsonify({'message': 'SMTP setting updated'}), 200
        
    except sqlite3.IntegrityError:
        return jsonify({'error': 'SMTP setting with this email already exists'}), 409
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@app.route('/api/smtp-settings/<int:setting_id>', methods=['DELETE'])
def delete_smtp_setting(setting_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Check if setting exists
        cursor.execute("SELECT id FROM smtp_settings WHERE id = ?", (setting_id,))
        if not cursor.fetchone():
            return jsonify({'error': 'SMTP setting not found'}), 404
            
        cursor.execute("DELETE FROM smtp_settings WHERE id = ?", (setting_id,))
        conn.commit()
        
        return jsonify({'message': 'SMTP setting deleted'}), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

# SMTP Email Functionality

from services.email_service import EmailService

email_service = EmailService()

@app.route('/api/smtp/test', methods=['POST'])
def test_smtp():
    """Test SMTP connection with provided or stored credentials"""
    data = request.json or {}
    
    try:
        # Use provided credentials or get from database
        if 'email' in data and 'app_password' in data:
            smtp_config = {
                'email': data['email'],
                'app_password': data['app_password'],
                'smtp_server': data.get('smtp_server', 'smtp.gmail.com'),
                'smtp_port': data.get('smtp_port', 587)
            }
            result = email_service.test_connection(smtp_config)
        else:
            result = email_service.test_connection()
            
        return jsonify(result), 200
        
    except ValueError as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 401
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/api/smtp/send', methods=['POST'])
def send_email():
    """Send email using active SMTP configuration"""
    data = request.json
    
    # Validate required fields
    to_email = data.get('to')
    subject = data.get('subject')
    body = data.get('body')
    
    if not all([to_email, subject, body]):
        return jsonify({'error': 'Missing required fields: to, subject, body'}), 400
    
    try:
        email_service.send_email(
            to_email=to_email,
            subject=subject,
            body=body,
            is_html=data.get('is_html', False)
        )
        return jsonify({'message': 'Email sent successfully'}), 200
        
    except ValueError as e:
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/smtp/send-test-analysis', methods=['POST'])
def send_test_analysis_email():
    """Send a test analysis email with sample data to see the template"""
    try:
        # Get active email recipients
        email_list = email_service.get_active_email_list()
        
        if not email_list:
            return jsonify({'error': 'No active email recipients found'}), 404
        
        # Sample analysis content
        sample_analysis = """# Executive Summary
The company demonstrated strong operational performance in Q2 FY2026, with revenue growth of 18% YoY driven by robust demand across key segments.

## Key Highlights
- **Revenue**: ₹2,450 Cr (+18% YoY)
- **EBITDA**: ₹485 Cr (+22% YoY)
- **Net Profit**: ₹320 Cr (+25% YoY)
- **EBITDA Margin**: 19.8% (expansion of 60 bps)

## Segment Performance
### Specialty Chemicals
- Volume growth of 15% driven by new product launches
- Capacity utilization at 85%
- Strong order book visibility for next 2 quarters

### Performance Chemicals
- Margin improvement due to favorable raw material prices
- New customer wins in international markets

## Strategic Initiatives
- Capex of ₹150 Cr announced for capacity expansion
- R&D investments increased by 20%
- Focus on sustainability and green chemistry

## Outlook
Management maintains positive outlook with guided revenue growth of 15-18% for FY2026. Strong demand environment and healthy order book provide visibility.

## Risks
- Raw material price volatility
- Global economic uncertainties
- Competition in key markets

*This is a sample analysis for demonstration purposes.*"""
        
        # Send to all active recipients
        sent_count = 0
        errors = []
        for email in email_list:
            try:
                email_service.send_analysis_email(
                    to_email=email,
                    stock_symbol="AARTIIND",
                    stock_name="Aarti Industries Limited",
                    quarter="Q2",
                    year=2026,
                    analysis_content=sample_analysis,
                    model_provider="Google AI",
                    model_name="gemini-2.0-flash-exp",
                    transcript_url="https://stockdiscovery.s3.amazonaws.com/insight/india/2619/Conference Call/CC-Jun25.pdf"
                )
                sent_count += 1
            except Exception as e:
                error_msg = f"Failed to send to {email}: {str(e)}"
                print(error_msg)
                errors.append(error_msg)
        
        response = {
            'message': f'Test analysis email sent to {sent_count} recipient(s)',
            'recipients': email_list,
            'sent_count': sent_count
        }
        if errors:
            response['errors'] = errors
        
        return jsonify(response), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# LLM API Endpoints

from services.llm.llm_service import LLMService

llm_service = LLMService()

@app.route('/api/llm/providers', methods=['GET'])
def get_llm_providers():
    """Get all LLM providers and their status."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT id, provider_name, display_name, is_active, 
               (api_key_encrypted IS NOT NULL AND api_key_encrypted != '') as has_key
        FROM llm_providers
        ORDER BY display_name
    """)
    
    providers = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return jsonify(providers)

@app.route('/api/llm/providers/<provider_name>/key', methods=['POST'])
def set_provider_key(provider_name):
    """Set API key for a provider."""
    data = request.json
    api_key = data.get('api_key')
    
    if not api_key:
        return jsonify({'error': 'API key is required'}), 400
        
    try:
        llm_service.set_api_key(provider_name, api_key)
        return jsonify({'message': 'API key saved successfully'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/llm/providers/<provider_name>/sync', methods=['POST'])
def sync_provider_models(provider_name):
    """Sync models for a provider."""
    try:
        count = llm_service.sync_models(provider_name)
        return jsonify({'message': f'Synced {count} models', 'count': count}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/llm/models', methods=['GET'])
def get_llm_models():
    """Get all available LLM models."""
    provider_name = request.args.get('provider')
    try:
        models = llm_service.get_available_models(provider_name)
        return jsonify(models)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/llm/settings', methods=['GET'])
def get_llm_settings():
    """Get global LLM settings."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT setting_key, setting_value FROM llm_settings")
    settings = {row['setting_key']: row['setting_value'] for row in cursor.fetchall()}
    conn.close()
    
    return jsonify(settings)

@app.route('/api/llm/settings', methods=['POST'])
def update_llm_settings():
    """Update global LLM settings."""
    data = request.json
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        for key, value in data.items():
            cursor.execute("""
                INSERT OR REPLACE INTO llm_settings (setting_key, setting_value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
            """, (key, str(value)))
            
        conn.commit()
        return jsonify({'message': 'Settings updated'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()


# Default Prompt Endpoints
@app.route('/api/prompts/default', methods=['GET'])
def get_default_prompt():
    """Get the current default analysis prompt (non-group stocks)."""
    try:
        prompt = prompt_service._get_default_prompt()
        return jsonify({'prompt': prompt})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/prompts/default', methods=['POST'])
def update_default_prompt():
    """Update the default analysis prompt (used when no group prompt is present)."""
    data = request.json or {}
    prompt = data.get('prompt')

    if not prompt:
        return jsonify({'error': 'Prompt is required'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            INSERT INTO llm_settings (setting_key, setting_value, updated_at)
            VALUES ('default_prompt', ?, CURRENT_TIMESTAMP)
            ON CONFLICT(setting_key) DO UPDATE SET 
                setting_value = excluded.setting_value,
                updated_at = CURRENT_TIMESTAMP
        """, (prompt,))
        conn.commit()
        return jsonify({'message': 'Default prompt updated'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()


# Analysis API Endpoints

from services.analysis_worker import AnalysisWorker

analysis_worker = AnalysisWorker()

@app.route('/api/analyze/<int:stock_id>', methods=['POST'])
def trigger_analysis(stock_id):
    data = request.get_json(silent=True) or {}
    # Accept params from body or query for compatibility
    quarter = data.get('quarter') or request.args.get('quarter')
    year_param = data.get('year') if 'year' in data else request.args.get('year', type=int)
    force_raw = data.get('force') if 'force' in data else request.args.get('force')
    if isinstance(force_raw, bool):
        force = force_raw
    elif force_raw is None:
        force = False
    else:
        force = str(force_raw).strip().lower() in {'1', 'true', 'yes', 'y', 'on'}
    year = None

    if year_param is not None:
        try:
            year = int(year_param)
        except (TypeError, ValueError):
            return jsonify({'error': 'year must be an integer'}), 400

    if (quarter and not year) or (year and not quarter):
        return jsonify({'error': 'Both quarter and year are required together'}), 400

    if quarter:
        quarter = quarter.upper()
        if quarter not in ['Q1', 'Q2', 'Q3', 'Q4']:
            return jsonify({'error': 'quarter must be one of Q1, Q2, Q3, Q4'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Verify stock exists
    cursor.execute("SELECT id FROM stocks WHERE id = ?", (stock_id,))
    if not cursor.fetchone():
        conn.close()
        return jsonify({'error': 'Stock not found'}), 404

    # If targeting a specific quarter/year, verify transcript is available
    if quarter and year:
        cursor.execute("""
            SELECT id, status, source_url FROM transcripts 
            WHERE stock_id = ? AND quarter = ? AND year = ?
            LIMIT 1
        """, (stock_id, quarter, year))
        transcript = cursor.fetchone()
        if not transcript:
            conn.close()
            return jsonify({'error': f'Transcript for {quarter} {year} not found'}), 404
        if transcript['status'] != 'available':
            conn.close()
            return jsonify({'error': f'Transcript status is {transcript["status"]}, cannot analyze'}), 422
        if not transcript['source_url']:
            conn.close()
            return jsonify({'error': f'Transcript for {quarter} {year} has no source_url to analyze'}), 422

    conn.close()
    
    # Start background job
    try:
        job_id = analysis_worker.start_analysis_job(stock_id, quarter=quarter, year=year, force=force)
        return jsonify({
            'message': 'Analysis started',
            'job_id': job_id,
            'status': 'pending'
        }), 202
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/analyses/<int:stock_id>', methods=['GET'])
def get_analyses(stock_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Get analyses with transcript metadata
        cursor.execute("""
            SELECT 
                ta.id,
                ta.llm_output,
                ta.created_at,
                ta.model_provider,
                t.quarter,
                t.year,
                t.source_url
            FROM transcript_analyses ta
            JOIN transcripts t ON ta.transcript_id = t.id
            WHERE t.stock_id = ?
            ORDER BY ta.created_at DESC
        """, (stock_id,))
        
        analyses = [dict(row) for row in cursor.fetchall()]
        return jsonify(analyses)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@app.route('/api/analyses/<int:stock_id>/download', methods=['GET'])
def download_latest_analysis(stock_id):
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        quarter = request.args.get('quarter')
        year = request.args.get('year', type=int)

        if (quarter and not year) or (year and not quarter):
            return jsonify({'error': 'Both quarter and year are required together'}), 400

        if quarter:
            quarter = quarter.upper()
            if quarter not in ['Q1', 'Q2', 'Q3', 'Q4']:
                return jsonify({'error': 'quarter must be one of Q1, Q2, Q3, Q4'}), 400

        params = [stock_id]
        query = """
            SELECT 
                ta.llm_output,
                ta.created_at,
                ta.model_provider,
                ta.model_id,
                t.quarter,
                t.year,
                t.source_url,
                s.stock_symbol,
                s.bse_code,
                s.stock_name
            FROM transcript_analyses ta
            JOIN transcripts t ON ta.transcript_id = t.id
            JOIN stocks s ON t.stock_id = s.id
            WHERE s.id = ?
        """

        if quarter and year:
            query += " AND t.quarter = ? AND t.year = ?"
            params.extend([quarter, year])

        query += " ORDER BY ta.created_at DESC LIMIT 1"

        cursor.execute(query, tuple(params))

        analysis = cursor.fetchone()
        if not analysis:
            if quarter and year:
                return jsonify({'error': f'No analysis found for {quarter} {year}'}), 404
            return jsonify({'error': 'No analysis found for this stock'}), 404

        def normalize_markdown(text: str) -> str:
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

        normalized = normalize_markdown(analysis['llm_output'])
        try:
            rendered_content = markdown.markdown(
                normalized,
                extensions=['extra', 'tables', 'sane_lists', 'nl2br']
            )
        except Exception:
            rendered_content = f"<pre>{html.escape(analysis['llm_output'] or '')}</pre>"

        symbol = analysis['stock_symbol'] or analysis['bse_code'] or f"stock-{stock_id}"
        stock_name = analysis['stock_name'] or symbol
        quarter = analysis['quarter']
        year = analysis['year']
        provider = (analysis['model_provider'] or 'LLM').upper()
        model_name_value = str(analysis['model_id']) if analysis['model_id'] is not None else provider
        transcript_url = analysis['source_url'] or '#'

        generated_at = str(analysis['created_at'])
        html_body = f"""
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                @page {{
                    size: A4;
                    margin: 28pt 32pt;
                }}
                body {{
                    font-family: "Helvetica", "Arial", sans-serif;
                    font-size: 11pt;
                    color: #111;
                    line-height: 1.55;
                }}
                .header {{
                    border-bottom: 1px solid #ccc;
                    padding-bottom: 10pt;
                    margin-bottom: 12pt;
                }}
                .title {{
                    font-size: 16pt;
                    font-weight: 700;
                    margin: 0;
                }}
                .meta {{
                    font-size: 9pt;
                    color: #555;
                    margin-top: 4pt;
                }}
                h1, h2, h3, h4, h5, h6 {{
                    margin-top: 14pt;
                    margin-bottom: 8pt;
                    color: #0f172a;
                }}
                p {{
                    margin: 8pt 0;
                }}
                ul, ol {{
                    margin: 8pt 0 8pt 18pt;
                }}
                li {{
                    margin: 4pt 0;
                }}
                table {{
                    width: 100%;
                    border-collapse: collapse;
                    table-layout: fixed;
                    margin: 12pt 0;
                    font-size: 10pt;
                }}
                th, td {{
                    border: 1px solid #d5d7db;
                    padding: 6pt 8pt;
                    word-wrap: break-word;
                    vertical-align: top;
                }}
                th {{
                    background: #f3f4f6;
                    font-weight: 700;
                    text-align: left;
                }}
                tr:nth-child(even) td {{
                    background: #fafafa;
                }}
                pre, code {{
                    font-family: "Consolas", "Courier New", monospace;
                    background: #f8fafc;
                    padding: 6pt;
                    border-radius: 4pt;
                    white-space: pre-wrap;
                    word-wrap: break-word;
                    display: block;
                }}
                /* Constrain excessive columns from overflowing the page */
                table thead tr th,
                table tbody tr td {{
                    max-width: 160pt;
                }}
            </style>
        </head>
        <body>
            <div class="header">
                <div class="title">{html.escape(stock_name)} ({html.escape(symbol)})</div>
                <div class="meta">Quarter: {html.escape(quarter)} {html.escape(str(year))}</div>
                <div class="meta">Model: {html.escape(model_name_value)} | Provider: {html.escape(provider)}</div>
                <div class="meta">Generated: {html.escape(generated_at)}</div>
                <div class="meta">Transcript: {html.escape(transcript_url)}</div>
            </div>
            <div class="content">
                {rendered_content}
            </div>
        </body>
        </html>
        """

        safe_symbol = "".join([c if c.isalnum() or c in ['-', '_'] else '_' for c in symbol])
        filename = f"{safe_symbol}_{quarter}_{year}_analysis.pdf"

        # Render HTML to PDF
        pdf_buffer = BytesIO()
        pdf_result = pisa.CreatePDF(html_body, dest=pdf_buffer)

        if pdf_result.err:
            return jsonify({'error': 'Failed to generate PDF'}), 500

        pdf_buffer.seek(0)
        response = Response(pdf_buffer.getvalue(), mimetype='application/pdf')
        response.headers['Content-Disposition'] = f'attachment; filename=\"{filename}\"'
        return response
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

# API Key Management Endpoints

from services.key_service import KeyService

key_service = KeyService()

@app.route('/api/keys', methods=['POST'])
def update_api_key():
    data = request.json
    provider = data.get('provider')
    key = data.get('key')
    
    if not provider or not key:
        return jsonify({'error': 'Provider and key are required'}), 400
        
    try:
        key_service.set_api_key(provider, key)
        return jsonify({'message': f'API key for {provider} updated successfully'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/keys/<provider>', methods=['GET'])
def check_api_key(provider):
    """Checks if an API key exists for the provider (returns masked)"""
    try:
        key = key_service.get_api_key(provider)
        if key:
            # Return masked key (e.g., "sk-1234...5678")
            masked = f"{key[:4]}...{key[-4:]}" if len(key) > 8 else "****"
            return jsonify({'provider': provider, 'has_key': True, 'masked_key': masked}), 200
        else:
            return jsonify({'provider': provider, 'has_key': False}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/upcoming-calls', methods=['GET'])
def get_upcoming_calls():
    """Fetches upcoming conference calls for all watchlist stocks."""
    try:
        from services.transcript_service import TranscriptService
        service = TranscriptService()
        
        # Get all upcoming calls (no filter)
        upcoming = service.get_upcoming_calls()
        
        # Convert to dict for JSON response
        results = []
        for call in upcoming:
            results.append({
                'company': call.stock_symbol,
                'isin': call.isin,
                'quarter': call.quarter,
                'year': call.year,
                'title': call.title
            })
        
        return jsonify({'upcoming_calls': results, 'count': len(results)}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/keys/<provider>/validate', methods=['POST'])
def validate_provider_key(provider):
    """Validates the API key for a specific provider by making a test call."""
    if provider == 'tijori':
        # We need to instantiate TranscriptService here or inject it
        # Ideally we should have a factory or cleaner dependency injection, but for now:
        from services.transcript_service import TranscriptService
        service = TranscriptService()
        is_valid = service.validate_api_key()
        
        if is_valid:
            return jsonify({'provider': provider, 'status': 'valid', 'message': 'API key is working correctly.'}), 200
        else:
            return jsonify({'provider': provider, 'status': 'invalid', 'message': 'API key validation failed. Check credentials.'}), 400
    else:
        return jsonify({'error': f'Validation not implemented for provider: {provider}'}), 501



@app.route('/api/llm/test', methods=['POST'])
def test_llm_generation():
    """Test LLM generation with a sample prompt."""
    try:
        data = request.json
        prompt = data.get('prompt', 'Hello, how are you?')
        model_id = data.get('model_id')  # Optional, uses default if not provided
        thinking_mode = data.get('thinking_mode', False)
        
        from services.llm.llm_service import LLMService
        llm_service = LLMService()
        
        response = llm_service.generate(
            prompt=prompt,
            system_prompt="You are a helpful assistant.",
            model_id=model_id,
            thinking_mode=thinking_mode,
            max_tokens=500
        )
        
        return jsonify({
            'content': response.content,
            'model_id': response.model_id,
            'provider': response.provider_name,
            'tokens_input': response.tokens_input,
            'tokens_output': response.tokens_output,
            'cost_usd': response.cost_usd,
            'thinking_mode_used': response.thinking_mode_used
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/llm/models/<int:model_id>/config', methods=['POST'])
def update_model_config(model_id):
    try:
        data = request.json
        llm_service.update_model_config(model_id, data)
        return jsonify({'message': 'Model configuration updated'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Document Research API Endpoints

@app.route('/api/research/documents/<symbol>', methods=['GET'])
def get_available_documents(symbol):
    """Get list of available annual reports for a stock from screener.in"""
    try:
        result = document_research_service.get_available_documents(symbol.upper())
        return jsonify(result), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/research/runs', methods=['GET'])
def list_research_runs():
    """List all document research runs"""
    try:
        runs = document_research_service.list_runs()
        return jsonify(runs), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/research/runs', methods=['POST'])
def create_research_run():
    """Start a new document research run"""
    data = request.json or {}
    stock_id = data.get('stock_id')
    document_years = data.get('document_years', [])
    prompt = data.get('prompt', '')
    
    if not stock_id:
        return jsonify({'error': 'stock_id is required'}), 400
    if not document_years:
        return jsonify({'error': 'document_years is required'}), 400
    
    try:
        run_id = document_research_service.create_run(stock_id, document_years, prompt)
        return jsonify({'message': 'Research started', 'run_id': run_id}), 202
    except ValueError as e:
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/research/runs/<int:run_id>', methods=['GET'])
def get_research_run(run_id):
    """Get research run details including rendered output"""
    try:
        run = document_research_service.get_run(run_id)
        if not run:
            return jsonify({'error': 'Run not found'}), 404
        return jsonify(run), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/research/runs/<int:run_id>/download', methods=['GET'])
def download_research_pdf(run_id):
    """Download research run as PDF"""
    try:
        pdf_content = document_research_service.generate_pdf(run_id)
        if not pdf_content:
            return jsonify({'error': 'PDF not available'}), 404
        
        run = document_research_service.get_run(run_id)
        filename = f"{run.get('stock_symbol', 'research')}-annual-report-analysis.pdf"
        
        return Response(
            pdf_content,
            mimetype='application/pdf',
            headers={'Content-Disposition': f'attachment; filename="{filename}"'}
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    # Disable debug mode in production (PyInstaller) to prevent reloader
    is_frozen = getattr(sys, 'frozen', False)
    app.run(debug=not is_frozen, port=5001, use_reloader=not is_frozen)
