"""
ntfy_commands.py

Handles reserved command dispatch for inbound ntfy messages.
Called from daemon.handle_ntfy_message() BEFORE falling through to task creation.

If the message matches a reserved command, handle it here and return True.
If not a command, return False so the caller creates a task as normal.
"""

import logging
from pathlib import Path

# Try to import yaml for orchestration data parsing
try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    yaml = None
    YAML_AVAILABLE = False

logger = logging.getLogger(__name__)


def dispatch(raw: str, queue, logs_dir, notify_fn) -> bool:
    """
    Dispatch reserved ntfy commands.
    
    Args:
        raw: The raw message string from ntfy (already stripped)
        queue: TaskQueue instance (has .get_all() method returning list of dicts with 'status' key)
        logs_dir: pathlib.Path pointing to the logs/ directory
        notify_fn: callable(title: str, message: str) that sends a push notification back via ntfy
    
    Returns:
        True if a reserved command was matched and handled
        False if not a command (caller should create task as normal)
    """
    if not raw:
        return False
    
    # Work with lowercase for case-insensitive matching
    raw_lower = raw.lower()
    
    # Try to match full string or prefix before space
    first_word = raw_lower.split()[0] if raw_lower.split() else raw_lower
    
    # Command 1: 'status'
    if first_word == 'status':
        return _handle_status(raw, queue, logs_dir, notify_fn)
    
    # Command 2: 'pause'
    if first_word == 'pause':
        return _handle_pause(logs_dir, notify_fn)
    
    # Command 3: 'stop'
    if first_word == 'stop':
        return _handle_stop(logs_dir, notify_fn)
    
    # Command 4: 'help'
    if first_word == 'help':
        return _handle_help(notify_fn)
    
    # Command 5: 'approve <anything>' or 'retry <anything>'
    if first_word == 'approve':
        return _handle_approve_retry(raw, logs_dir, notify_fn, 'approve')
    
    if first_word == 'retry':
        return _handle_approve_retry(raw, logs_dir, notify_fn, 'retry')
    
    # Not a reserved command
    return False


def _handle_status(raw: str, queue, logs_dir, notify_fn) -> bool:
    """Handle the 'status' command."""
    try:
        logger.info('[ntfy-cmd] status: querying queue and orchestration data')
        
        # Get queue counts
        all_tasks = queue.get_all()
        counts = {'done': 0, 'failed': 0, 'pending': 0, 'in_progress': 0}
        
        for task in all_tasks:
            status = task.get('status', '').lower()
            if status in counts:
                counts[status] += 1
        
        # Build line 1
        lines = [f"Done:{counts['done']} Failed:{counts['failed']} Pending:{counts['pending']}"]
        
        # Try to read orchestration data
        orchestration_path = logs_dir / 'AXEL_ORCHESTRATION.yaml'
        if YAML_AVAILABLE and orchestration_path.exists():
            try:
                with open(orchestration_path, 'r') as f:
                    data = yaml.safe_load(f)
                
                if data and isinstance(data, dict):
                    # Extract sprint goal
                    sprint = data.get('sprint', {})
                    if sprint and isinstance(sprint, dict):
                        goal = sprint.get('goal', '')
                        if goal:
                            goal = str(goal)[:40]
                            lines.append(f"Sprint: {goal}")
                    
                    # Extract design loop info
                    design_loop = data.get('design_loop', {})
                    if design_loop and isinstance(design_loop, dict) and design_loop.get('active') is True:
                        iteration = design_loop.get('iteration', 0)
                        score = design_loop.get('last_q1_score', '')
                        if score:
                            lines.append(f"Design: iter{iteration} score={score}")
                        else:
                            lines.append(f"Design: iter{iteration}")
            except Exception as e:
                logger.warning(f'[ntfy-cmd] status: failed to parse orchestration data: {e}')
        
        # Send notification (max 3 lines already enforced by logic)
        message = '\n'.join(lines)
        notify_fn(title='WESA Status', message=message)
        
        return True
    except Exception as e:
        logger.error(f'[ntfy-cmd] status: unexpected error: {e}')
        return True  # Return True so caller doesn't create spurious task


def _handle_pause(logs_dir, notify_fn) -> bool:
    """Handle the 'pause' command."""
    try:
        logger.info('[ntfy-cmd] pause: setting daemon pause flag')
        
        pause_flag = logs_dir / 'daemon_pause.flag'
        pause_flag.touch()
        
        notify_fn(title='WESA', message='Daemon pause flag set.')
        return True
    except Exception as e:
        logger.error(f'[ntfy-cmd] pause: failed to set flag: {e}')
        return True


def _handle_stop(logs_dir, notify_fn) -> bool:
    """Handle the 'stop' command."""
    try:
        logger.info('[ntfy-cmd] stop: setting daemon stop flag')
        
        stop_flag = logs_dir / 'daemon_stop.flag'
        stop_flag.touch()
        
        notify_fn(title='WESA', message='Daemon stop flag set.')
        return True
    except Exception as e:
        logger.error(f'[ntfy-cmd] stop: failed to set flag: {e}')
        return True


def _handle_help(notify_fn) -> bool:
    """Handle the 'help' command."""
    try:
        logger.info('[ntfy-cmd] help: sending command list')
        
        notify_fn(
            title='WESA Commands',
            message='status | pause | stop | help | D1:task | R1:task | W1:task'
        )
        return True
    except Exception as e:
        logger.error(f'[ntfy-cmd] help: failed to send notification: {e}')
        return True


def _handle_approve_retry(raw: str, logs_dir, notify_fn, action: str) -> bool:
    """Handle 'approve <run_id>' or 'retry <run_id>' commands."""
    try:
        # Extract run_id (everything after first space, stripped)
        parts = raw.split(None, 1)  # Split on whitespace, max 2 parts
        if len(parts) < 2:
            logger.warning(f'[ntfy-cmd] {action}: missing run_id')
            notify_fn(title='WESA Error', message=f'{action.capitalize()} requires a run_id. Usage: {action} <run_id>')
            return True
        
        run_id = parts[1].strip()
        if not run_id:
            logger.warning(f'[ntfy-cmd] {action}: empty run_id')
            notify_fn(title='WESA Error', message=f'{action.capitalize()} requires a run_id. Usage: {action} <run_id>')
            return True
        
        logger.info(f'[ntfy-cmd] {action}: writing approval for run_id={run_id}')
        
        # Create approvals directory if needed
        approvals_dir = logs_dir / 'approvals'
        approvals_dir.mkdir(parents=True, exist_ok=True)
        
        # Write approval file
        approval_file = approvals_dir / f'{run_id}.{action}'
        approval_file.touch()
        
        notify_fn(title='WESA', message=f'Approval written: {run_id}.{action}')
        return True
    except Exception as e:
        logger.error(f'[ntfy-cmd] {action}: failed to write approval: {e}')
        return True