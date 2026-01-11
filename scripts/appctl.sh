#!/bin/bash

# App Control Script
# Manages frontend and backend processes (status, start, stop, restart)
# Usage: ./appctl.sh [status|start|stop|restart] [frontend|backend|all]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BACKEND_DIR="$PROJECT_ROOT/backend"
FRONTEND_DIR="$PROJECT_ROOT/frontend"
LOG_DIR="$PROJECT_ROOT/logs"
PID_DIR="$PROJECT_ROOT/.pids"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

mkdir -p "$LOG_DIR" "$PID_DIR"

# ============ Helper Functions ============

print_status() {
    local service=$1
    local status=$2
    local pid=$3
    
    if [[ "$status" == "running" ]]; then
        echo -e "  ${service}: ${GREEN}● Running${NC} (PID: $pid)"
    else
        echo -e "  ${service}: ${RED}○ Stopped${NC}"
    fi
}

get_backend_pids() {
    # Find Python processes running app.py in the backend directory
    pgrep -f "python.*app\.py" 2>/dev/null | while read pid; do
        # Verify it's from our project
        if lsof -p "$pid" 2>/dev/null | grep -q "$BACKEND_DIR"; then
            echo "$pid"
        fi
    done
    # Also check for flask processes
    pgrep -f "flask.*run" 2>/dev/null || true
    # Also check for uvicorn
    pgrep -f "uvicorn.*app" 2>/dev/null || true
}

get_frontend_pids() {
    # Find node/npm processes for vite dev server
    pgrep -f "vite.*--port" 2>/dev/null || true
    pgrep -f "npm.*run.*dev" 2>/dev/null || true
    # Check for node processes in frontend directory
    pgrep -f "node.*$FRONTEND_DIR" 2>/dev/null || true
}

is_backend_running() {
    local pids=$(get_backend_pids)
    [[ -n "$pids" ]]
}

is_frontend_running() {
    local pids=$(get_frontend_pids)
    [[ -n "$pids" ]]
}

# ============ Status Command ============

cmd_status() {
    echo ""
    echo -e "${BLUE}╔════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║          App Status                    ║${NC}"
    echo -e "${BLUE}╚════════════════════════════════════════╝${NC}"
    echo ""
    
    # Backend status
    local backend_pids=$(get_backend_pids)
    if [[ -n "$backend_pids" ]]; then
        print_status "Backend " "running" "$(echo $backend_pids | tr '\n' ' ')"
    else
        print_status "Backend " "stopped" ""
    fi
    
    # Frontend status
    local frontend_pids=$(get_frontend_pids)
    if [[ -n "$frontend_pids" ]]; then
        print_status "Frontend" "running" "$(echo $frontend_pids | tr '\n' ' ')"
    else
        print_status "Frontend" "stopped" ""
    fi
    
    echo ""
    
    # Check ports
    echo -e "${YELLOW}Ports in use:${NC}"
    if lsof -i :5001 -sTCP:LISTEN &>/dev/null; then
        echo "  Port 5001 (Backend):  In use"
    else
        echo "  Port 5001 (Backend):  Available"
    fi
    
    if lsof -i :5173 -sTCP:LISTEN &>/dev/null; then
        echo "  Port 5173 (Frontend): In use"
    else
        echo "  Port 5173 (Frontend): Available"
    fi
    echo ""
}

# ============ Stop Command ============

stop_backend() {
    echo -e "${YELLOW}Stopping backend...${NC}"
    
    local pids=$(get_backend_pids)
    if [[ -n "$pids" ]]; then
        echo "$pids" | xargs -I {} kill {} 2>/dev/null || true
        sleep 1
        # Force kill if still running
        pids=$(get_backend_pids)
        if [[ -n "$pids" ]]; then
            echo "$pids" | xargs -I {} kill -9 {} 2>/dev/null || true
        fi
        echo -e "  ${GREEN}✓ Backend stopped${NC}"
    else
        echo -e "  ${YELLOW}Backend was not running${NC}"
    fi
    
    # Also kill anything on port 5001
    lsof -ti :5001 | xargs kill -9 2>/dev/null || true
}

stop_frontend() {
    echo -e "${YELLOW}Stopping frontend...${NC}"
    
    local pids=$(get_frontend_pids)
    if [[ -n "$pids" ]]; then
        echo "$pids" | xargs -I {} kill {} 2>/dev/null || true
        sleep 1
        # Force kill if still running
        pids=$(get_frontend_pids)
        if [[ -n "$pids" ]]; then
            echo "$pids" | xargs -I {} kill -9 {} 2>/dev/null || true
        fi
        echo -e "  ${GREEN}✓ Frontend stopped${NC}"
    else
        echo -e "  ${YELLOW}Frontend was not running${NC}"
    fi
    
    # Also kill anything on port 5173
    lsof -ti :5173 | xargs kill -9 2>/dev/null || true
}

cmd_stop() {
    local service="${1:-all}"
    
    case "$service" in
        backend)
            stop_backend
            ;;
        frontend)
            stop_frontend
            ;;
        all|*)
            stop_frontend
            stop_backend
            ;;
    esac
    echo ""
}

# ============ Start Command ============

start_backend() {
    if is_backend_running; then
        echo -e "  ${YELLOW}Backend is already running${NC}"
        return
    fi
    
    echo -e "${YELLOW}Starting backend...${NC}"
    
    # Find Python executable
    local PYTHON=""
    if [[ -x "$BACKEND_DIR/.venv/bin/python" ]]; then
        PYTHON="$BACKEND_DIR/.venv/bin/python"
    elif [[ -x "$BACKEND_DIR/backend_venv/bin/python" ]]; then
        PYTHON="$BACKEND_DIR/backend_venv/bin/python"
    elif [[ -x "$PROJECT_ROOT/venv/bin/python" ]]; then
        PYTHON="$PROJECT_ROOT/venv/bin/python"
    else
        PYTHON="python3"
    fi
    
    cd "$BACKEND_DIR"
    nohup $PYTHON app.py > "$LOG_DIR/backend.log" 2>&1 &
    local pid=$!
    echo $pid > "$PID_DIR/backend.pid"
    
    sleep 2
    if is_backend_running; then
        echo -e "  ${GREEN}✓ Backend started${NC} (PID: $pid)"
        echo -e "  ${BLUE}Log: $LOG_DIR/backend.log${NC}"
    else
        echo -e "  ${RED}✗ Backend failed to start. Check $LOG_DIR/backend.log${NC}"
    fi
}

start_frontend() {
    if is_frontend_running; then
        echo -e "  ${YELLOW}Frontend is already running${NC}"
        return
    fi
    
    echo -e "${YELLOW}Starting frontend...${NC}"
    
    cd "$FRONTEND_DIR"
    
    # Install dependencies if needed
    if [[ ! -d node_modules ]]; then
        echo -e "  ${YELLOW}Installing dependencies...${NC}"
        npm install > "$LOG_DIR/npm_install.log" 2>&1
    fi
    
    nohup npm run dev -- --host 0.0.0.0 --port 5173 > "$LOG_DIR/frontend.log" 2>&1 &
    local pid=$!
    echo $pid > "$PID_DIR/frontend.pid"
    
    sleep 3
    if is_frontend_running; then
        echo -e "  ${GREEN}✓ Frontend started${NC} (PID: $pid)"
        echo -e "  ${BLUE}Log: $LOG_DIR/frontend.log${NC}"
        echo -e "  ${BLUE}URL: http://localhost:5173${NC}"
    else
        echo -e "  ${RED}✗ Frontend failed to start. Check $LOG_DIR/frontend.log${NC}"
    fi
}

cmd_start() {
    local service="${1:-all}"
    
    case "$service" in
        backend)
            start_backend
            ;;
        frontend)
            start_frontend
            ;;
        all|*)
            start_backend
            start_frontend
            ;;
    esac
    echo ""
}

# ============ Restart Command ============

cmd_restart() {
    local service="${1:-all}"
    
    echo -e "${BLUE}Restarting $service...${NC}"
    echo ""
    cmd_stop "$service"
    sleep 1
    cmd_start "$service"
}

# ============ Logs Command ============

cmd_logs() {
    local service="${1:-all}"
    
    echo -e "${BLUE}Recent logs:${NC}"
    echo ""
    
    case "$service" in
        backend)
            echo -e "${YELLOW}=== Backend Log ===${NC}"
            tail -50 "$LOG_DIR/backend.log" 2>/dev/null || echo "No backend log found"
            ;;
        frontend)
            echo -e "${YELLOW}=== Frontend Log ===${NC}"
            tail -50 "$LOG_DIR/frontend.log" 2>/dev/null || echo "No frontend log found"
            ;;
        all|*)
            echo -e "${YELLOW}=== Backend Log (last 20 lines) ===${NC}"
            tail -20 "$LOG_DIR/backend.log" 2>/dev/null || echo "No backend log found"
            echo ""
            echo -e "${YELLOW}=== Frontend Log (last 20 lines) ===${NC}"
            tail -20 "$LOG_DIR/frontend.log" 2>/dev/null || echo "No frontend log found"
            ;;
    esac
    echo ""
}

# ============ Help ============

show_help() {
    echo ""
    echo -e "${BLUE}App Control Script${NC}"
    echo ""
    echo "Usage: $0 <command> [service]"
    echo ""
    echo "Commands:"
    echo "  status              Show running status of all services"
    echo "  start [service]     Start services in background"
    echo "  stop [service]      Stop services"
    echo "  restart [service]   Restart services"
    echo "  logs [service]      Show recent logs"
    echo ""
    echo "Services:"
    echo "  all (default)       Both frontend and backend"
    echo "  frontend            Frontend only (Vite/React)"
    echo "  backend             Backend only (Flask/Python)"
    echo ""
    echo "Examples:"
    echo "  $0 status           # Check if app is running"
    echo "  $0 start            # Start both frontend and backend"
    echo "  $0 stop backend     # Stop only backend"
    echo "  $0 restart          # Restart everything"
    echo "  $0 logs frontend    # View frontend logs"
    echo ""
}

# ============ Main ============

main() {
    local command="${1:-status}"
    local service="${2:-all}"
    
    case "$command" in
        status)
            cmd_status
            ;;
        start)
            cmd_start "$service"
            ;;
        stop)
            cmd_stop "$service"
            ;;
        restart)
            cmd_restart "$service"
            ;;
        logs)
            cmd_logs "$service"
            ;;
        help|--help|-h)
            show_help
            ;;
        *)
            echo -e "${RED}Unknown command: $command${NC}"
            show_help
            exit 1
            ;;
    esac
}

main "$@"
