import sys

# Windows consoles/pipes default to cp1252; a print() containing any
# non-ANSI character (zero-width spaces from Tijori data, rupee signs, etc.)
# raises UnicodeEncodeError and kills the worker that printed it.
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import sqlite3
import os
import time
import signal
import threading
import hmac
import hashlib
from datetime import datetime, timezone
import smtplib
import html
import markdown
from io import BytesIO
from xhtml2pdf import pisa
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from config import DATABASE_PATH
from db import get_db_connection as _get_db_connection
from services.queue_scheduler_service import QueueSchedulerService
from services.transcript_fetcher_worker import TranscriptFetcherWorker
from services.analysis_queue_worker import AnalysisQueueWorker
from services.email_queue_worker import EmailQueueWorker
from services.analysis_job_service import AnalysisJobService
from services.recovery_service import RecoveryService
from services.prompt_service import PromptService
from services.group_research_service import GroupResearchService
from services.document_research_service import DocumentResearchService
from services.stock_activity_service import StockActivityService
from services.stock_overview_service import StockOverviewService
from services.stock_import_service import StockImportError, StockImportService

app = Flask(__name__)
CORS(app)

# Queue-first runtime. Transcript provider calls stay single-file; analysis has
# two consumers so one long model response cannot block every other watchlist stock.
queue_scheduler = QueueSchedulerService()
fetcher_worker = TranscriptFetcherWorker()
analysis_queue_workers = [AnalysisQueueWorker(), AnalysisQueueWorker()]
analysis_queue_worker = analysis_queue_workers[0]
email_queue_worker = EmailQueueWorker()
analysis_job_service = AnalysisJobService()
recovery_service = RecoveryService()
prompt_service = PromptService()
group_research_service = GroupResearchService()
document_research_service = DocumentResearchService()
stock_activity_service = StockActivityService()
stock_overview_service = StockOverviewService()
_runtime_stop_lock = threading.Lock()
_shutdown_started = threading.Event()

DB_PATH = str(DATABASE_PATH)


def _should_start_background_workers() -> bool:
    if os.environ.get("PRODUCT_GEMINI_DISABLE_WORKERS") == "1":
        return False
    if getattr(sys, "frozen", False):
        return True
    return not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true"


def _run_startup_recovery():
    stale_minutes = os.environ.get("ANALYSIS_STALE_MINUTES", "5")
    stale_group_minutes = os.environ.get("GROUP_RESEARCH_STALE_MINUTES", "180")
    summary = recovery_service.run_startup_recovery(
        analysis_job_service=analysis_job_service,
        stale_minutes=stale_minutes,
        stale_group_minutes=stale_group_minutes,
    )
    if any(summary.values()):
        print(f"[Recovery] Startup recovery summary: {summary}")
    try:
        pruned = StockImportService(DB_PATH).prune_stale_previews()
        if pruned:
            print(f"[Recovery] Pruned {pruned} stale stock import preview(s)")
    except Exception as error:
        print(f"[Recovery] Stock import preview cleanup failed: {error}")


def _stop_background_runtime(timeout_seconds: float = 5.0):
    """Stop queue threads together and checkpoint SQLite within one deadline."""
    with _runtime_stop_lock:
        services = (
            queue_scheduler,
            fetcher_worker,
            *analysis_queue_workers,
            email_queue_worker,
        )
        for service in services:
            service.running = False
            queue = getattr(service, "queue", None)
            if queue:
                for queue_name in ("transcript_check", "analysis", "email"):
                    queue.notify(queue_name)

        deadline = time.monotonic() + max(timeout_seconds, 0)
        for service in services:
            thread = getattr(service, "thread", None)
            if thread and thread.is_alive() and thread is not threading.current_thread():
                thread.join(timeout=max(0, deadline - time.monotonic()))

        try:
            conn = get_db_connection()
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                conn.close()
        except Exception as error:
            print(f"[Shutdown] WAL checkpoint failed: {error}")


def _exit_after_graceful_shutdown():
    time.sleep(0.1)
    _stop_background_runtime(timeout_seconds=5)
    os._exit(0)


def _is_control_request_authorized() -> bool:
    expected = os.environ.get("PRODUCT_GEMINI_CONTROL_TOKEN")
    if not expected:
        return True
    provided = request.headers.get("X-Product-Gemini-Control", "")
    return hmac.compare_digest(provided, expected)


if _should_start_background_workers():
    try:
        _run_startup_recovery()
        queue_scheduler.start()
        fetcher_worker.start()
        for worker in analysis_queue_workers:
            worker.start()
        email_queue_worker.start()
    except Exception as e:
        print(f"[Scheduler] Queue runtime initialization failed: {e}")


def get_db_connection():
    return _get_db_connection(DB_PATH)


def _to_utc_iso(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value).strip()
        if not raw:
            return None
        normalized = raw.replace(" ", "T")
        try:
            dt = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        except ValueError:
            return raw
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _is_db_locked_error(error: Exception) -> bool:
    return isinstance(error, sqlite3.OperationalError) and "locked" in str(error).lower()


def _trigger_stock_fetch_with_retry(
    stock_id: int,
    *,
    quarter: str = None,
    year: int = None,
    max_attempts: int = 5,
):
    for attempt in range(1, max_attempts + 1):
        try:
            queue_scheduler.trigger_for_stock(stock_id, quarter=quarter, year=year)
            return
        except Exception as e:
            if _is_db_locked_error(e) and attempt < max_attempts:
                time.sleep(0.15 * attempt)
                continue
            raise


@app.route('/api/system/health', methods=['GET'])
def get_system_health():
    """Identify the packaged backend so Electron never reuses a stale process."""
    control_token = os.environ.get('PRODUCT_GEMINI_CONTROL_TOKEN', '')
    return jsonify({
        'status': 'ok',
        'service': 'product-gemini-backend',
        'api_version': 2,
        'app_version': os.environ.get('PRODUCT_GEMINI_APP_VERSION'),
        'pid': os.getpid(),
        'control_session_id': (
            hashlib.sha256(control_token.encode('utf-8')).hexdigest()
            if control_token
            else None
        ),
    })


@app.route('/api/system/shutdown', methods=['POST'])
def shutdown_system():
    """Allow Electron to stop workers before taskkill/SIGKILL fallback."""
    if not _is_control_request_authorized():
        return jsonify({'error': 'Unauthorized control request'}), 403
    if not _shutdown_started.is_set():
        _shutdown_started.set()
        threading.Thread(
            target=_exit_after_graceful_shutdown,
            name="graceful-shutdown",
            daemon=True,
        ).start()
    return jsonify({'status': 'shutting_down'}), 202


@app.route('/api/system/resumed', methods=['POST'])
def resume_system():
    """Catch up promptly after laptop sleep without resetting in-flight jobs."""
    if not _is_control_request_authorized():
        return jsonify({'error': 'Unauthorized control request'}), 403
    try:
        result = queue_scheduler.trigger_now(fresh=False)
        return jsonify({'status': 'resumed', **result}), 202
    except Exception as error:
        return jsonify({'error': str(error)}), 500


@app.route('/api/poll/status', methods=['GET'])
def get_poll_status():
    try:
        status = queue_scheduler.get_status()
        status["workers"].update({
            "transcript": bool(fetcher_worker.thread and fetcher_worker.thread.is_alive()),
            "analysis": bool(
                analysis_queue_worker.thread and analysis_queue_worker.thread.is_alive()
            ),
            "email": bool(email_queue_worker.thread and email_queue_worker.thread.is_alive()),
        })
        return jsonify(status)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/queue/health', methods=['GET'])
def get_queue_health():
    return get_poll_status()


@app.route('/api/poll/trigger', methods=['POST'])
def trigger_poll():
    try:
        result = queue_scheduler.trigger_now(fresh=False)
        return jsonify({
            'message': 'Due checks and queued work were started without resetting the queue',
            'started': True,
            **result
        }), 202
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/queue/reset', methods=['POST'])
def reset_queue():
    data = request.get_json(silent=True) or {}
    if data.get("confirm") is not True:
        return jsonify({
            'error': 'Queue reset requires confirm=true',
        }), 400
    try:
        result = queue_scheduler.trigger_now(fresh=True)
        return jsonify({
            'message': 'Queue engine reset completed',
            **result,
        }), 202
    except Exception as error:
        return jsonify({'error': str(error)}), 500


@app.route('/api/queue/retry-failed', methods=['POST'])
def retry_failed_queue_jobs():
    data = request.get_json(silent=True) or {}
    queue_type = str(data.get("queue") or "all").strip().lower()
    if queue_type not in {"all", "transcript", "analysis", "email"}:
        return jsonify({'error': 'queue must be all, transcript, analysis, or email'}), 400

    conn = get_db_connection()
    summary = {"transcript": 0, "analysis": 0, "email": 0}
    try:
        if queue_type in {"all", "transcript"}:
            summary["transcript"] = conn.execute(
                """
                UPDATE transcript_fetch_schedule
                SET last_status = 'error',
                    attempts = 0,
                    next_check_at = CURRENT_TIMESTAMP,
                    locked_until = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE last_status = 'failed'
                """
            ).rowcount
        if queue_type in {"all", "analysis"}:
            summary["analysis"] = conn.execute(
                """
                UPDATE analysis_jobs
                SET status = 'retrying',
                    attempts = 0,
                    retry_next_at = CURRENT_TIMESTAMP,
                    locked_until = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE status IN ('failed', 'error')
                """
            ).rowcount
        if queue_type in {"all", "email"}:
            summary["email"] = conn.execute(
                """
                UPDATE email_outbox
                SET status = 'retrying',
                    attempts = 0,
                    retry_next_at = CURRENT_TIMESTAMP,
                    locked_until = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE status = 'failed'
                """
            ).rowcount
        conn.commit()
    finally:
        conn.close()

    trigger_result = queue_scheduler.trigger_now(fresh=False)
    return jsonify({
        'message': 'Failed jobs queued for retry',
        'retried': summary,
        'quarter': trigger_result["quarter"],
        'year': trigger_result["year"],
    }), 202

@app.route('/api/scheduler/status', methods=['GET'])
def get_scheduler_status():
    try:
        return jsonify(queue_scheduler.get_status())
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/scheduler/trigger', methods=['POST'])
def trigger_scheduler():
    data = request.get_json(silent=True) or {}
    stock_id = data.get('stock_id')
    quarter = data.get('quarter')
    year = data.get('year')
    if stock_id is None:
        return jsonify({'error': 'stock_id is required'}), 400
    try:
        queue_scheduler.trigger_for_stock(int(stock_id), quarter=quarter, year=year)
        return jsonify({'message': 'Stock queued for transcript check'}), 202
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/activity', methods=['GET'])
def get_stock_activity():
    stock_id = request.args.get('stock_id', type=int)
    level = request.args.get('level')
    limit = request.args.get('limit', default=200, type=int)

    try:
        if stock_id is not None:
            conn = get_db_connection()
            try:
                stock = conn.execute(
                    """
                    SELECT id,
                           COALESCE(stock_symbol, bse_code, isin_number) AS symbol,
                           stock_name AS name
                    FROM stocks
                    WHERE id = ?
                    """,
                    (stock_id,),
                ).fetchone()
            finally:
                conn.close()
            if not stock:
                return jsonify({'error': 'Stock not found'}), 404

        events = stock_activity_service.get_activity(
            stock_id=stock_id,
            level=level,
            limit=limit or 200,
        )
        return jsonify({
            'events': events,
            'count': len(events),
            'stock_id': stock_id,
        })
    except ValueError as error:
        return jsonify({'error': str(error)}), 400
    except Exception as error:
        return jsonify({'error': str(error)}), 500


@app.route('/api/stocks/<int:stock_id>/overview', methods=['GET'])
def get_stock_overview(stock_id):
    try:
        overview = stock_overview_service.get_overview(stock_id)
        if overview is None:
            return jsonify({'error': 'Stock not found'}), 404
        return jsonify(overview)
    except Exception as error:
        return jsonify({'error': str(error)}), 500

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
    
    # ISIN-only user stocks remain searchable and usable.
    search_term = f"%{query}%"
    cursor.execute("""
        SELECT id,
               COALESCE(stock_symbol, bse_code, isin_number) as symbol,
               isin_number AS isin,
               stock_name as name,
               source
        FROM stocks
        WHERE is_active = 1
          AND (
              stock_symbol LIKE ? OR bse_code LIKE ?
              OR isin_number LIKE ? OR stock_name LIKE ?
          )
        ORDER BY 
            CASE 
                WHEN stock_symbol = ? THEN 1 
                WHEN bse_code = ? THEN 2
                WHEN isin_number = ? THEN 3
                WHEN stock_symbol LIKE ? THEN 4
                WHEN bse_code LIKE ? THEN 5
                WHEN isin_number LIKE ? THEN 6
                WHEN stock_name LIKE ? THEN 7
                ELSE 8
            END,
            COALESCE(stock_symbol, bse_code, isin_number) ASC
        LIMIT 10
    """, (
        search_term, search_term, search_term, search_term,
        query, query, query,
        f"{query}%", f"{query}%", f"{query}%", f"{query}%"
    ))
    
    stocks = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    # Add default status for now (since it's not in DB yet)
    for stock in stocks:
        stock['status'] = 'not-ready'
        
    return jsonify(stocks)


def _stock_import_error_response(error: StockImportError):
    payload = {"error": str(error)}
    payload.update(error.details)
    return jsonify(payload), error.status_code


@app.route('/api/stocks/template.csv', methods=['GET'])
def download_stock_template():
    return Response(
        "ISIN,CompanyName\r\nINE009A01021,Infosys Limited\r\n",
        mimetype="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="stock_import_template.csv"'
        },
    )


@app.route('/api/stocks/import/preview', methods=['POST'])
def preview_stock_import():
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return jsonify({"error": "Choose a CSV, TSV, or XLSX file."}), 400
    if request.content_length and request.content_length > 5 * 1024 * 1024 + 64 * 1024:
        return jsonify({"error": "The upload exceeds the 5 MB limit."}), 413
    try:
        content = upload.stream.read(5 * 1024 * 1024 + 1)
        result = StockImportService(DB_PATH).preview(upload.filename, content)
        return jsonify(result)
    except StockImportError as error:
        return _stock_import_error_response(error)
    except Exception as error:
        return jsonify({"error": f"Could not preview stock import: {error}"}), 500


@app.route('/api/stocks/import/commit', methods=['POST'])
def commit_stock_import():
    data = request.get_json(silent=True) or {}
    batch_id = data.get("batch_id")
    if batch_id is None:
        return jsonify({"error": "batch_id is required."}), 400
    try:
        result = StockImportService(DB_PATH).commit_batch(int(batch_id))
        return jsonify({"message": "Stock import completed.", **result})
    except StockImportError as error:
        return _stock_import_error_response(error)
    except (TypeError, ValueError):
        return jsonify({"error": "batch_id must be an integer."}), 400
    except Exception as error:
        return jsonify({"error": f"Could not commit stock import: {error}"}), 500


@app.route('/api/stocks/manual', methods=['POST'])
def add_stock_manually():
    data = request.get_json(silent=True) or {}
    try:
        stock = StockImportService(DB_PATH).create_manual(
            data.get("isin") or data.get("ISIN"),
            data.get("company_name") or data.get("CompanyName"),
        )
        return jsonify({"message": "Stock added.", "stock": stock}), 201
    except StockImportError as error:
        return _stock_import_error_response(error)
    except Exception as error:
        return jsonify({"error": f"Could not add stock: {error}"}), 500


@app.route('/api/stocks/admin', methods=['GET'])
def list_admin_stocks():
    query = (request.args.get("q") or "").strip()
    source = (request.args.get("source") or "user").strip().lower()
    page = max(request.args.get("page", default=1, type=int) or 1, 1)
    per_page = min(
        max(request.args.get("per_page", default=50, type=int) or 50, 1),
        100,
    )
    filters = []
    params = []
    if query:
        filters.append(
            "(isin_number LIKE ? OR stock_name LIKE ? "
            "OR stock_symbol LIKE ? OR bse_code LIKE ?)"
        )
        search_term = f"%{query}%"
        params.extend([search_term] * 4)
    if source == "user":
        filters.append("source IN ('import', 'manual')")
    elif source in {"master", "import", "manual"}:
        filters.append("source = ?")
        params.append(source)

    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
    connection = get_db_connection()
    try:
        total = connection.execute(
            f"SELECT COUNT(*) FROM stocks {where_clause}",
            params,
        ).fetchone()[0]
        rows = connection.execute(
            f"""
            SELECT s.id,
                   COALESCE(s.stock_symbol, s.bse_code, s.isin_number) AS symbol,
                   s.stock_symbol,
                   s.bse_code,
                   s.isin_number AS isin,
                   s.stock_name AS company_name,
                   s.source,
                   s.is_active,
                   EXISTS(
                       SELECT 1 FROM watchlist_items w WHERE w.stock_id = s.id
                   ) AS in_watchlist,
                   (
                       SELECT COUNT(*) FROM group_stocks gs WHERE gs.stock_id = s.id
                   ) AS group_count
            FROM stocks s
            {where_clause}
            ORDER BY datetime(s.updated_at) DESC, s.id DESC
            LIMIT ? OFFSET ?
            """,
            (*params, per_page, (page - 1) * per_page),
        ).fetchall()
        return jsonify(
            {
                "stocks": [
                    {
                        **dict(row),
                        "is_active": bool(row["is_active"]),
                        "in_watchlist": bool(row["in_watchlist"]),
                    }
                    for row in rows
                ],
                "total": total,
                "page": page,
                "per_page": per_page,
                "pages": (total + per_page - 1) // per_page,
            }
        )
    finally:
        connection.close()


@app.route('/api/stocks/<int:stock_id>', methods=['PUT'])
def update_managed_stock(stock_id):
    data = request.get_json(silent=True) or {}
    if "isin" in data or "ISIN" in data:
        return jsonify({"error": "A stock's ISIN cannot be changed."}), 400
    updates = []
    params = []
    if "company_name" in data or "CompanyName" in data:
        company_name = " ".join(
            str(data.get("company_name") or data.get("CompanyName") or "").split()
        )
        if not company_name:
            return jsonify({"error": "CompanyName is required."}), 400
        updates.append("stock_name = ?")
        params.append(company_name)
    if "is_active" in data:
        updates.append("is_active = ?")
        params.append(1 if bool(data["is_active"]) else 0)
    if not updates:
        return jsonify({"error": "No editable fields were supplied."}), 400

    connection = get_db_connection()
    try:
        stock = connection.execute(
            "SELECT id FROM stocks WHERE id = ?",
            (stock_id,),
        ).fetchone()
        if not stock:
            return jsonify({"error": "Stock not found."}), 404
        params.append(stock_id)
        connection.execute(
            f"""
            UPDATE stocks
            SET {', '.join(updates)}, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            params,
        )
        connection.commit()
        return jsonify({"message": "Stock updated."})
    finally:
        connection.close()


@app.route('/api/stocks/<int:stock_id>', methods=['DELETE'])
def delete_managed_stock(stock_id):
    connection = get_db_connection()
    try:
        stock = connection.execute(
            """
            SELECT id, source,
                   (SELECT COUNT(*) FROM watchlist_items WHERE stock_id = stocks.id)
                       AS watchlists,
                   (SELECT COUNT(*) FROM group_stocks WHERE stock_id = stocks.id)
                       AS groups,
                   (
                       (SELECT COUNT(*) FROM transcripts WHERE stock_id = stocks.id)
                       + (SELECT COUNT(*) FROM transcript_fetch_schedule
                          WHERE stock_id = stocks.id)
                       + (SELECT COUNT(*) FROM stock_activity_logs
                          WHERE stock_id = stocks.id
                            AND stage != 'stock_import')
                   ) AS history
            FROM stocks
            WHERE id = ?
            """,
            (stock_id,),
        ).fetchone()
        if not stock:
            return jsonify({"error": "Stock not found."}), 404
        if stock["watchlists"] or stock["groups"] or stock["history"]:
            return jsonify(
                {
                    "error": "Stock is in use or has activity history. Deactivate it instead.",
                    "reason": "in_use",
                    "watchlists": stock["watchlists"],
                    "groups": stock["groups"],
                    "history": stock["history"],
                }
            ), 409
        if stock["source"] == "master":
            return jsonify(
                {
                    "error": "Bundled master stocks cannot be deleted. Deactivate it instead.",
                    "reason": "master",
                }
            ), 409
        connection.execute("DELETE FROM stocks WHERE id = ?", (stock_id,))
        connection.commit()
        return jsonify({"message": "Stock deleted."})
    finally:
        connection.close()

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
            COALESCE(s.stock_symbol, s.bse_code, s.isin_number) as symbol,
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
            ORDER BY 
                CASE status 
                    WHEN 'available' THEN 0
                    WHEN 'upcoming' THEN 1
                    ELSE 2
                END,
                COALESCE(updated_at, created_at) DESC,
                id DESC
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
                    'id': analysis['id'],
                    'completed': True,
                    'date': analysis['created_at'],
                    'provider': analysis['model_provider']
                }

        retry_info = {
            'retrying': False,
            'retry_attempts': 0,
            'retry_next_at': None,
            'retry_scope': None
        }
        active_analysis_job = None

        if transcript:
            cursor.execute("""
                SELECT status, attempts, retry_next_at
                FROM analysis_jobs
                WHERE transcript_id = ?
                  AND status IN ('pending', 'queued', 'retrying', 'in_progress')
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
            """, (transcript['id'],))
            active_analysis_job = cursor.fetchone()
            if active_analysis_job and active_analysis_job['status'] == 'retrying':
                retry_info = {
                    'retrying': True,
                    'retry_attempts': active_analysis_job['attempts'],
                    'retry_next_at': _to_utc_iso(active_analysis_job['retry_next_at']),
                    'retry_scope': 'analysis'
                }

        if not retry_info['retrying'] and analysis_info:
            cursor.execute("""
                SELECT attempts, retry_next_at
                FROM email_outbox
                WHERE analysis_id = ? AND status = 'retrying'
                ORDER BY updated_at DESC
                LIMIT 1
            """, (analysis_info['id'],))
            retry_row = cursor.fetchone()
            if retry_row:
                retry_info = {
                    'retrying': True,
                    'retry_attempts': retry_row['attempts'],
                    'retry_next_at': _to_utc_iso(retry_row['retry_next_at']),
                    'retry_scope': 'email'
                }

        fetch_schedule = None
        if not retry_info['retrying']:
            cursor.execute("""
                SELECT attempts, next_check_at, last_status, last_checked_at,
                       CASE
                           WHEN locked_until IS NOT NULL
                            AND datetime(locked_until) > datetime('now')
                           THEN 1
                           ELSE 0
                       END AS is_locked
                FROM transcript_fetch_schedule
                WHERE stock_id = ? AND quarter = ? AND year = ?
                LIMIT 1
            """, (stock_id, quarter, year))
            fetch_schedule = cursor.fetchone()
            if fetch_schedule and fetch_schedule['attempts'] > 0 and fetch_schedule['last_status'] == 'error':
                retry_info = {
                    'retrying': True,
                    'retry_attempts': fetch_schedule['attempts'],
                    'retry_next_at': _to_utc_iso(fetch_schedule['next_check_at']),
                    'retry_scope': 'transcript_fetch'
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

            if active_analysis_job:
                analysis_job_status = active_analysis_job['status']
                if analysis_job_status in ('pending', 'queued'):
                    analysis_message = 'Analysis queued...'
                elif analysis_job_status == 'retrying':
                    analysis_message = 'Retrying analysis...'
                elif analysis_state == 'preparing':
                    analysis_message = 'Preparing transcript text...'
                elif analysis_state == 'generating':
                    analysis_message = 'Generating analysis...'
                else:
                    analysis_message = 'Analyzing transcript...'

                status_info = {
                    'status': 'analyzing',
                    'message': analysis_message,
                    'details': {
                        'quarter': transcript['quarter'],
                        'year': transcript['year']
                    }
                }
            elif analysis_state in ('in_progress', 'preparing', 'generating'):
                analysis_message = {
                    'preparing': 'Preparing transcript text...',
                    'generating': 'Generating analysis...',
                }.get(analysis_state, 'Analyzing transcript...')
                status_info = {
                    'status': 'analyzing',
                    'message': analysis_message,
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
                event_date_iso = _to_utc_iso(transcript['event_date'])
                status_info = {
                    'status': 'upcoming',
                    'message': f"Upcoming: {event_date_iso or transcript['event_date']}",
                    'details': {
                        'quarter': transcript['quarter'],
                        'year': transcript['year'],
                        'event_date': event_date_iso or transcript['event_date']
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
                            'analyzed_at': _to_utc_iso(analysis_info['date']) or analysis_info['date'],
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
                            'transcript_date': _to_utc_iso(transcript['created_at']) or transcript['created_at']
                        }
                    }
        elif row['transcript_check_status'] == 'checking' or (
            fetch_schedule and fetch_schedule['is_locked']
        ):
            status_info = {
                'status': 'fetching',
                'message': 'Fetching transcript...',
                'details': None
            }
        elif fetch_schedule:
            next_check_at = _to_utc_iso(fetch_schedule['next_check_at'])
            status_info = {
                'status': 'waiting',
                'message': 'Waiting for transcript',
                'details': {
                    'next_check_at': next_check_at,
                    'last_checked_at': _to_utc_iso(fetch_schedule['last_checked_at']),
                }
            }

        if fetch_schedule:
            schedule_details = {
                'next_check_at': _to_utc_iso(fetch_schedule['next_check_at']),
                'last_checked_at': _to_utc_iso(fetch_schedule['last_checked_at']),
                'fetch_attempts': fetch_schedule['attempts'],
            }
            status_info['details'] = {
                **(status_info['details'] or {}),
                **schedule_details,
            }
        
        stocks.append({
            'id': stock_id,
            'symbol': row['symbol'],
            'name': row['name'],
            'added_at': _to_utc_iso(row['added_at']) or row['added_at'],
            'status': status_info['status'],
            'status_message': status_info['message'],
            'status_details': status_info['details'],
            'retrying': retry_info['retrying'],
            'retry_attempts': retry_info['retry_attempts'],
            'retry_next_at': retry_info['retry_next_at'],
            'retry_scope': retry_info['retry_scope']
        })
    
    conn.close()
    return jsonify(stocks)

@app.route('/api/watchlist', methods=['POST'])
def add_to_watchlist():
    data = request.get_json(silent=True) or {}
    stock_id = data.get('stock_id')
    symbol = data.get('symbol')
    
    if stock_id is None and not symbol:
        return jsonify({'error': 'stock_id or symbol is required'}), 400
        
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        if stock_id is not None:
            cursor.execute(
                "SELECT id FROM stocks WHERE id = ? AND is_active = 1",
                (int(stock_id),),
            )
        else:
            cursor.execute(
                """
                SELECT id FROM stocks
                WHERE is_active = 1
                  AND (stock_symbol = ? OR bse_code = ? OR isin_number = ?)
                """,
                (symbol, symbol, symbol),
            )
        stock = cursor.fetchone()
        
        if not stock:
            return jsonify({'error': 'Stock not found'}), 404
            
        # Add to watchlist
        cursor.execute("INSERT INTO watchlist_items (stock_id) VALUES (?)", (stock['id'],))
        conn.commit()

        # Kick off immediate transcript check instead of waiting for the next poll
        _trigger_stock_fetch_with_retry(stock['id'])
        return jsonify({'message': 'Added to watchlist'}), 201
        
    except sqlite3.IntegrityError:
        return jsonify({'message': 'Already in watchlist'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@app.route('/api/watchlist/<symbol>', methods=['DELETE'])
def remove_from_watchlist(symbol):
    max_attempts = 5
    for attempt in range(1, max_attempts + 1):
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            # Get stock ID first - check both NSE symbol and BSE code
            cursor.execute(
                """
                SELECT id FROM stocks
                WHERE stock_symbol = ? OR bse_code = ? OR isin_number = ?
                """,
                (symbol, symbol, symbol),
            )
            stock = cursor.fetchone()

            if stock:
                cursor.execute("DELETE FROM watchlist_items WHERE stock_id = ?", (stock['id'],))
                conn.commit()

            return jsonify({'message': 'Removed from watchlist'}), 200
        except Exception as e:
            if _is_db_locked_error(e) and attempt < max_attempts:
                time.sleep(0.15 * attempt)
                continue
            return jsonify({'error': str(e)}), 500
        finally:
            conn.close()

    return jsonify({'error': 'Database is busy, please retry'}), 503


@app.route('/api/watchlist/id/<int:stock_id>', methods=['DELETE'])
def remove_from_watchlist_by_id(stock_id):
    connection = get_db_connection()
    try:
        connection.execute(
            "DELETE FROM watchlist_items WHERE stock_id = ?",
            (stock_id,),
        )
        connection.commit()
        return jsonify({'message': 'Removed from watchlist'}), 200
    except Exception as error:
        return jsonify({'error': str(error)}), 500
    finally:
        connection.close()

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
            COALESCE(s.stock_symbol, s.bse_code, s.isin_number) as symbol,
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
    data = request.get_json(silent=True) or {}
    stock_id = data.get('stock_id')
    symbol = data.get('symbol')
    
    if stock_id is None and not symbol:
        return jsonify({'error': 'stock_id or symbol is required'}), 400
        
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        if stock_id is not None:
            cursor.execute(
                "SELECT id FROM stocks WHERE id = ? AND is_active = 1",
                (int(stock_id),),
            )
        else:
            cursor.execute(
                """
                SELECT id FROM stocks
                WHERE is_active = 1
                  AND (stock_symbol = ? OR bse_code = ? OR isin_number = ?)
                """,
                (symbol, symbol, symbol),
            )
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
        queue_scheduler.trigger_for_stock(stock['id'])
        return jsonify({'message': 'Stock added to group'}), 201
        
    except sqlite3.IntegrityError:
        return jsonify({'message': 'Stock already in group'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()


@app.route('/api/groups/<int:group_id>/stocks/id/<int:stock_id>', methods=['DELETE'])
def remove_stock_from_group_by_id(group_id, stock_id):
    connection = get_db_connection()
    try:
        connection.execute(
            "DELETE FROM group_stocks WHERE group_id = ? AND stock_id = ?",
            (group_id, stock_id),
        )
        connection.commit()
        return jsonify({'message': 'Stock removed from group'}), 200
    except Exception as error:
        return jsonify({'error': str(error)}), 500
    finally:
        connection.close()

@app.route('/api/groups/<int:group_id>/stocks/<symbol>', methods=['DELETE'])
def remove_stock_from_group(group_id, symbol):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Get stock ID - check both NSE symbol and BSE code
        cursor.execute(
            """
            SELECT id FROM stocks
            WHERE stock_symbol = ? OR bse_code = ? OR isin_number = ?
            """,
            (symbol, symbol, symbol),
        )
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
        quarter = request.args.get('quarter')
        year = request.args.get('year', type=int)

        if (quarter and year is None) or (year is not None and not quarter):
            return jsonify({'error': 'Both quarter and year are required together'}), 400

        if quarter:
            quarter = quarter.upper()
            if quarter not in ['Q1', 'Q2', 'Q3', 'Q4']:
                return jsonify({'error': 'quarter must be one of Q1, Q2, Q3, Q4'}), 400

        runs = group_research_service.list_runs(group_id, quarter=quarter, year=year)
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
            INSERT INTO smtp_settings (
                email, app_password, smtp_server, smtp_port, smtp_security, is_active
            )
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            email, 
            app_password, 
            data.get('smtp_server', 'smtp.gmail.com'),
            data.get('smtp_port', 587),
            data.get('smtp_security', 'auto'),
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
                SET app_password = ?, smtp_server = ?, smtp_port = ?,
                    smtp_security = ?, is_active = ?
                WHERE email = ?
            """, (
                app_password,
                data.get('smtp_server', 'smtp.gmail.com'),
                data.get('smtp_port', 587),
                data.get('smtp_security', 'auto'),
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

        if 'smtp_security' in data:
            fields.append("smtp_security = ?")
            values.append(data['smtp_security'])
            
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
                'smtp_port': data.get('smtp_port', 587),
                'smtp_security': data.get('smtp_security', 'auto'),
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
               (
                   COALESCE(NULLIF(api_key, ''), NULLIF(api_key_encrypted, ''))
                   IS NOT NULL
               ) as has_key
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
    settings = {
        'watchlist_fast_mode': '1',
        'watchlist_max_tokens': '8000',
        'watchlist_fallback_model_id': '0',
        **{row['setting_key']: row['setting_value'] for row in cursor.fetchall()},
    }
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
            if key in {'default_model_id', 'watchlist_model_id', 'group_research_model_id', 'watchlist_fallback_model_id'}:
                try:
                    model_id = int(value)
                except (TypeError, ValueError):
                    return jsonify({'error': f'Invalid model ID for {key}'}), 400
                if model_id != 0:
                    cursor.execute(
                        "SELECT 1 FROM llm_models WHERE id = ? AND is_active = 1",
                        (model_id,),
                    )
                    if cursor.fetchone() is None:
                        return jsonify({'error': f'Model {model_id} is not available'}), 400
            if key == 'watchlist_max_tokens':
                try:
                    token_limit = int(value)
                except (TypeError, ValueError):
                    return jsonify({'error': 'Watchlist max tokens must be a number'}), 400
                if token_limit < 2000 or token_limit > 12000:
                    return jsonify({'error': 'Watchlist max tokens must be between 2000 and 12000'}), 400
            if key == 'watchlist_fast_mode':
                value = '1' if str(value).lower() not in {'0', 'false', 'off', 'no'} else '0'
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

    # Stock-level analysis is only allowed for watchlist stocks
    cursor.execute("SELECT 1 FROM watchlist_items WHERE stock_id = ? LIMIT 1", (stock_id,))
    if cursor.fetchone() is None:
        conn.close()
        return jsonify({'error': 'Stock is not in watchlist; stock-level analysis is disabled'}), 409

    transcript_id = None
    # If targeting a specific quarter/year, verify transcript is available
    if quarter and year:
        cursor.execute("""
            SELECT id, status, source_url FROM transcripts 
            WHERE stock_id = ? AND quarter = ? AND year = ?
            LIMIT 1
        """, (stock_id, quarter, year))
        transcript = cursor.fetchone()
        transcript_status = transcript['status'] if transcript else 'none'
        transcript_source_url = transcript['source_url'] if transcript else None
        needs_fetch = (
            transcript is None
            or transcript_status != 'available'
            or not transcript_source_url
        )
        if needs_fetch:
            conn.close()
            try:
                _trigger_stock_fetch_with_retry(stock_id, quarter=quarter, year=year)
            except Exception as e:
                return jsonify({'error': f'Failed to trigger transcript fetch: {e}'}), 500
            return jsonify({
                'message': f'Transcript check triggered for {quarter} {year}',
                'status': 'fetching_transcript',
                'quarter': quarter,
                'year': year,
                'transcript_status': transcript_status
            }), 202
        transcript_id = transcript['id']
    else:
        cursor.execute("""
            SELECT id, quarter, year, status, source_url
            FROM transcripts
            WHERE stock_id = ? AND status = 'available'
            ORDER BY COALESCE(updated_at, created_at) DESC, id DESC
            LIMIT 1
        """, (stock_id,))
        transcript = cursor.fetchone()
        if transcript and transcript['source_url']:
            transcript_id = transcript['id']
        else:
            cursor.execute("""
                SELECT quarter, year, status
                FROM transcripts
                WHERE stock_id = ?
                ORDER BY COALESCE(updated_at, created_at) DESC, id DESC
                LIMIT 1
            """, (stock_id,))
            latest = cursor.fetchone()
            if latest:
                target_quarter = latest['quarter']
                target_year = latest['year']
                transcript_status = latest['status'] or 'none'
            else:
                target_quarter, target_year = get_previous_fy_quarter()
                transcript_status = 'none'
            conn.close()
            try:
                _trigger_stock_fetch_with_retry(stock_id, quarter=target_quarter, year=target_year)
            except Exception as e:
                return jsonify({'error': f'Failed to trigger transcript fetch: {e}'}), 500
            return jsonify({
                'message': f'Transcript check triggered for {target_quarter} {target_year}',
                'status': 'fetching_transcript',
                'quarter': target_quarter,
                'year': target_year,
                'transcript_status': transcript_status
            }), 202

    conn.close()
    
    # Start background job
    try:
        job_id = analysis_job_service.enqueue_for_transcript(transcript_id, force=force)
        if job_id is None:
            return jsonify({
                'message': 'Analysis already exists for this transcript',
                'status': 'skipped'
            }), 200
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
                COALESCE(ta.model_name, lm.model_id, CAST(ta.model_id AS TEXT)) AS model_name,
                t.quarter,
                t.year,
                t.source_url
            FROM transcript_analyses ta
            JOIN transcripts t ON ta.transcript_id = t.id
            LEFT JOIN llm_models lm ON lm.id = ta.model_id
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
                COALESCE(ta.model_name, lm.model_id, CAST(ta.model_id AS TEXT)) AS model_name,
                t.quarter,
                t.year,
                t.source_url,
                s.stock_symbol,
                s.bse_code,
                s.isin_number,
                s.stock_name
            FROM transcript_analyses ta
            JOIN transcripts t ON ta.transcript_id = t.id
            JOIN stocks s ON t.stock_id = s.id
            LEFT JOIN llm_models lm ON lm.id = ta.model_id
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

        symbol = (
            analysis['stock_symbol']
            or analysis['bse_code']
            or analysis['isin_number']
            or f"stock-{stock_id}"
        )
        stock_name = analysis['stock_name'] or symbol
        quarter = analysis['quarter']
        year = analysis['year']
        provider = (analysis['model_provider'] or 'LLM').upper()
        model_name_value = analysis['model_name'] or provider
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
    is_frozen = getattr(sys, 'frozen', False)

    def _handle_termination(_signum, _frame):
        _stop_background_runtime(timeout_seconds=5)
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _handle_termination)
    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, _handle_termination)

    if is_frozen:
        from waitress import serve
        serve(app, host='127.0.0.1', port=5001, threads=8)
    else:
        app.run(
            debug=True,
            host='127.0.0.1',
            port=5001,
            use_reloader=True,
        )
